from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from backend.app.config import get_settings
from backend.app.services.sync.adapter import (
    ItemUnavailable,
    JobMeta,
    SourceCapabilityUnavailable,
    SourceItem,
    SourceUnavailable,
)
from backend.app.services.sync.dingtalk import (
    MISSING_ATTACHMENT_FILENAME,
    DingTalkRecruitmentAdapter,
    parse_candidates_page,
    parse_jobs_page,
)

FIXTURES = Path(__file__).parents[1] / "contracts" / "dingtalk-recruitment" / "v1.0"
CANDIDATES_URL = "https://api.dingtalk.com/v1.0/recruitment/candidates"
JOBS_URL = "https://api.dingtalk.com/v1.0/recruitment/jobs"
DOWNLOAD_URL = "https://example.invalid/d/1001"
SINCE = datetime(2026, 7, 27, tzinfo=timezone.utc)


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _row(**overrides: Any) -> dict:
    """One page holding a single row, with the recorded shape as its baseline."""
    resume = {
        "fileName": "zhang.pdf",
        "fileType": "application/pdf",
        "downloadUrl": DOWNLOAD_URL,
        **overrides.pop("resume", {}),
    }
    row = {
        "candidateId": "cand-1001",
        "updateTime": "2026-07-27T04:30:00Z",
        "jobCode": "FOREIGN_TRADE",
        **overrides,
        "resume": resume,
    }
    return {"hasMore": False, "nextCursor": None, "list": [row]}


def _page(*rows: dict) -> dict:
    """A page holding exactly these rows, in exactly this order."""
    return {"hasMore": False, "nextCursor": None, "list": list(rows)}


def _candidate(external_id: str, update_time: str) -> dict:
    """One well-formed row, for tests that care only about ordering."""
    return {
        "candidateId": external_id,
        "updateTime": update_time,
        "jobCode": None,
        "resume": {
            "fileName": f"{external_id}.pdf",
            "fileType": "application/pdf",
            "downloadUrl": f"https://example.invalid/d/{external_id}",
        },
    }


# --------------------------------------------------------------------------
# Parsing the recorded page
# --------------------------------------------------------------------------


def test_a_normal_page_maps_every_documented_field() -> None:
    items, urls = parse_candidates_page(_load("candidates-page"))

    assert [i.external_id for i in items] == ["cand-1001", "cand-1002"]
    assert items[0].updated_at == datetime(2026, 7, 27, 4, 30, tzinfo=timezone.utc)
    assert items[0].jd_code == "FOREIGN_TRADE"
    assert items[0].filename == "zhang.pdf"
    assert items[1].jd_code is None
    # URLs travel beside the items, never inside them.
    assert urls["cand-1001"] == "https://example.invalid/d/1001"


def test_an_empty_page_is_not_an_error() -> None:
    assert parse_candidates_page(_load("candidates-empty")) == ([], {})


def test_a_missing_required_field_raises_rather_than_yielding_none() -> None:
    # Silent `None` here would create a candidate with no source id and break
    # deduplication for every later run.
    with pytest.raises(ValueError, match="candidateId"):
        parse_candidates_page(_load("candidates-malformed"))


def test_a_row_without_a_timestamp_raises_because_the_cursor_needs_it() -> None:
    # Skipping it silently would let the cursor advance past a row whose
    # position in the window is unknown.
    with pytest.raises(ValueError, match="updateTime"):
        parse_candidates_page(_page({"candidateId": "cand-1", "resume": {}}))


def test_the_adapter_declares_its_source_name() -> None:
    # The primary key of `sync_cursors`, settled in d7b531f. Changing it after
    # a production run orphans every cursor and every ledger row.
    assert DingTalkRecruitmentAdapter.source_name == "dingtalk_recruitment"


def test_a_numeric_job_code_reaches_the_pipeline_as_a_string() -> None:
    # `jd_code` is a string column and `ingest_upload(jd_code=...)` passes it
    # straight through; an int would only fail at the database boundary.
    items, _ = parse_candidates_page(_row(jobCode=8801))

    assert items[0].jd_code == "8801"


# --------------------------------------------------------------------------
# JD metadata (WP8 §10) — a separate page shape, no cursor, no download
# --------------------------------------------------------------------------


def test_a_normal_jobs_page_maps_every_documented_field() -> None:
    jobs = parse_jobs_page(_load("jobs-page"))

    assert jobs == [
        JobMeta(code="FOREIGN_TRADE", name="外贸业务员", description="负责海外客户开发与订单跟进"),
        JobMeta(code="WAREHOUSE_CLERK", name="仓库文员", description=""),
    ]


def test_an_empty_jobs_page_is_not_an_error() -> None:
    assert parse_jobs_page({"hasMore": False, "nextCursor": None, "list": []}) == []


def test_a_job_row_missing_job_code_raises() -> None:
    # A JD synced with no code can never be matched against a resume's
    # `SourceItem.jd_code` on any later run.
    with pytest.raises(ValueError, match="jobCode"):
        parse_jobs_page({"list": [{"name": "外贸业务员", "description": ""}]})


def test_a_job_row_missing_name_raises() -> None:
    # `jds.name` is NOT NULL; failing here gives a clear cause instead of an
    # IntegrityError surfacing from deep inside the sync path.
    with pytest.raises(ValueError, match="name"):
        parse_jobs_page({"list": [{"jobCode": "FOREIGN_TRADE"}]})


def test_a_job_row_with_no_description_becomes_an_empty_string() -> None:
    jobs = parse_jobs_page({"list": [{"jobCode": "FOREIGN_TRADE", "name": "外贸业务员"}]})

    assert jobs[0].description == ""


@respx.mock
async def test_list_jobs_calls_the_documented_endpoint() -> None:
    route = respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json=_load("jobs-page")))
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    jobs = await adapter.list_jobs()

    assert [j.code for j in jobs] == ["FOREIGN_TRADE", "WAREHOUSE_CLERK"]
    assert route.calls[0].request.headers["x-acs-dingtalk-access-token"] == "corp-token-1"


@respx.mock
async def test_a_jobs_listing_transport_failure_becomes_source_unavailable() -> None:
    # `sync_jd_metadata` catches `SourceUnavailable` only; a raw transport
    # error escaping here would surface as an unhandled exception instead.
    respx.get(JOBS_URL).mock(side_effect=httpx.ReadTimeout("timeout"))
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    with pytest.raises(SourceUnavailable):
        await adapter.list_jobs()


@respx.mock
async def test_a_jobs_listing_http_status_becomes_source_unavailable() -> None:
    respx.get(JOBS_URL).mock(return_value=httpx.Response(500, json={"code": "InternalError"}))
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    with pytest.raises(SourceUnavailable):
        await adapter.list_jobs()


@respx.mock
async def test_a_jobs_payload_not_matching_the_recorded_shape_is_source_unavailable() -> None:
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json={"result": {"items": []}}))
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    with pytest.raises(SourceUnavailable):
        await adapter.list_jobs()


# --------------------------------------------------------------------------
# The cap must keep the OLDEST window, because the cursor advances by value
# --------------------------------------------------------------------------


@respx.mock
async def test_a_newest_first_page_is_sorted_before_the_limit_truncates_it() -> None:
    # The runner truncates by POSITION and then advances the cursor by VALUE
    # (`max(updated_at)`). If the newest items survived the cap, the cursor
    # would jump past the oldest ones this run dropped and `overlap_start`
    # would open after them: they are never listed again. Server ordering is
    # UNVERIFIED, so the adapter must not depend on it.
    respx.get(CANDIDATES_URL).mock(
        return_value=httpx.Response(
            200,
            json=_page(
                _candidate("cand-newest", "2026-07-27T06:00:00Z"),
                _candidate("cand-middle", "2026-07-27T05:00:00Z"),
                _candidate("cand-oldest", "2026-07-27T04:00:00Z"),
            ),
        )
    )
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    items = await adapter.list_changed(SINCE, 2)

    assert [i.external_id for i in items] == ["cand-oldest", "cand-middle"]


@respx.mock
async def test_an_unordered_page_is_returned_oldest_first() -> None:
    respx.get(CANDIDATES_URL).mock(
        return_value=httpx.Response(
            200,
            json=_page(
                _candidate("cand-b", "2026-07-27T05:00:00Z"),
                _candidate("cand-c", "2026-07-27T06:00:00Z"),
                _candidate("cand-a", "2026-07-27T04:00:00Z"),
            ),
        )
    )
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    items = await adapter.list_changed(SINCE, 10)

    assert [i.updated_at for i in items] == sorted(i.updated_at for i in items)
    assert [i.external_id for i in items] == ["cand-a", "cand-b", "cand-c"]


# --------------------------------------------------------------------------
# A candidate with no attachment must fail alone, not wedge every future run
# --------------------------------------------------------------------------


def test_a_candidate_with_no_resume_object_is_emitted_rather_than_aborting() -> None:
    # A row with no resume attached is an ordinary ATS state. Raising would
    # make `list_changed` SourceUnavailable, which leaves the cursor untouched
    # — so every later run fails identically, forever, over a growing window.
    items, urls = parse_candidates_page(
        _page(
            {"candidateId": "cand-1", "updateTime": "2026-07-27T04:30:00Z"},
            _candidate("cand-2", "2026-07-27T04:45:00Z"),
        )
    )

    assert [i.external_id for i in items] == ["cand-1", "cand-2"]
    # No URL recorded for it, which is what makes `fetch` refuse it alone.
    assert "cand-1" not in urls
    assert items[0].filename == MISSING_ATTACHMENT_FILENAME


def test_a_row_with_no_download_url_is_emitted_with_a_pii_free_placeholder() -> None:
    items, urls = parse_candidates_page(
        _page(
            {
                "candidateId": "cand-1",
                "updateTime": "2026-07-27T04:30:00Z",
                "resume": {"fileName": "zhang-san-13800138000.pdf", "fileType": "application/pdf"},
            }
        )
    )

    assert urls == {}
    assert items[0].filename == MISSING_ATTACHMENT_FILENAME
    assert "zhang" not in items[0].filename
    assert "13800138000" not in items[0].filename


def test_a_row_with_no_filename_keeps_its_download_url() -> None:
    """A fetchable resume must not be discarded over an OPTIONAL field.

    Spec §16.1 lists `fileName` as optional, and `_resolve_filename` already
    covers its absence completely — `"resume"` plus a suffix from `fileType`,
    then from the URL path. Gating the URL on the name threw the attachment
    away before any of that ran, and the loss is permanent rather than
    deferred: `fetch` raises `ItemUnavailable`, the ledger writes `failed` under
    `UNKNOWN_SHA256`, the cursor advances past the row, and replay is inert for
    DingTalk until the recruitment permission lands.
    """
    items, urls = parse_candidates_page(
        _page(
            {
                "candidateId": "cand-1",
                "updateTime": "2026-07-27T04:30:00Z",
                "resume": {"fileType": "application/pdf", "downloadUrl": DOWNLOAD_URL},
            }
        )
    )

    assert urls == {"cand-1": DOWNLOAD_URL}
    assert items[0].filename == "resume.pdf"
    assert items[0].filename != MISSING_ATTACHMENT_FILENAME


def test_a_row_with_no_filename_and_no_file_type_takes_its_suffix_from_the_url() -> None:
    items, urls = parse_candidates_page(
        _page(
            {
                "candidateId": "cand-1",
                "updateTime": "2026-07-27T04:30:00Z",
                "resume": {"downloadUrl": "https://example.invalid/d/1001/cv.docx?sig=x"},
            }
        )
    )

    assert urls["cand-1"] == "https://example.invalid/d/1001/cv.docx?sig=x"
    assert items[0].filename == "resume.docx"


@respx.mock
async def test_a_nameless_attachment_is_actually_downloaded() -> None:
    # The end the parse change exists for: the row survives to `fetch` and the
    # bytes land, instead of the candidate being dropped for good.
    respx.get(CANDIDATES_URL).mock(
        return_value=httpx.Response(
            200,
            json=_page(
                {
                    "candidateId": "cand-1",
                    "updateTime": "2026-07-27T04:30:00Z",
                    "resume": {"fileType": "application/pdf", "downloadUrl": DOWNLOAD_URL},
                }
            ),
        )
    )
    respx.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b"%PDF-1.4 fake"))
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    items = await adapter.list_changed(SINCE, 10)
    fetched = await adapter.fetch(items[0])

    assert fetched.content == b"%PDF-1.4 fake"
    assert fetched.filename == "resume.pdf"


def test_an_undeclared_file_type_does_not_abort_the_page() -> None:
    # The upload gate reads the suffix alone, so a name that already carries
    # one needs no declared type.
    items, urls = parse_candidates_page(_row(resume={"fileType": None}))

    assert items[0].filename == "zhang.pdf"
    assert urls["cand-1001"] == DOWNLOAD_URL


@respx.mock
async def test_an_attachment_less_item_fails_alone_so_the_cursor_can_advance() -> None:
    # Design §9: the run records one `failed` ledger row, appends the item to
    # `processed`, and the cursor moves past it.
    respx.get(CANDIDATES_URL).mock(
        return_value=httpx.Response(
            200,
            json=_page(
                {"candidateId": "cand-1", "updateTime": "2026-07-27T04:30:00Z"},
                _candidate("cand-2", "2026-07-27T04:45:00Z"),
            ),
        )
    )
    respx.get("https://example.invalid/d/cand-2").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.4 fake")
    )
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    items = await adapter.list_changed(SINCE, 10)

    assert len(items) == 2
    with pytest.raises(ItemUnavailable):
        await adapter.fetch(items[0])
    # The rest of the run is unaffected.
    assert (await adapter.fetch(items[1])).content == b"%PDF-1.4 fake"


# --------------------------------------------------------------------------
# Hard requirement 1: the filename must carry an extension WP1 accepts
# --------------------------------------------------------------------------


def test_a_filename_without_an_extension_gets_one_from_the_declared_type() -> None:
    # The sync path builds an `UploadFile` with no headers, so
    # `upload.content_type` is None and `upload/validation.py` decides on the
    # suffix alone. Passing this name through would make the item
    # `unsupported_attachment` every single run.
    items, _ = parse_candidates_page(_row(resume={"fileName": "resume"}))

    assert items[0].filename == "resume.pdf"


def test_a_filename_without_an_extension_falls_back_to_the_download_url() -> None:
    items, _ = parse_candidates_page(
        _row(
            resume={
                "fileName": "resume",
                "fileType": "application/octet-stream",
                "downloadUrl": "https://example.invalid/d/1001/candidate.docx?sig=x",
            }
        )
    )

    assert items[0].filename == "resume.docx"


def test_an_undeterminable_extension_is_left_for_the_per_item_validator() -> None:
    # One unsupported attachment must be one failed item, not a page-level
    # error that aborts the whole run.
    items, _ = parse_candidates_page(
        _row(
            resume={
                "fileName": "resume",
                "fileType": "application/x-7z-compressed",
                "downloadUrl": "https://example.invalid/d/1001",
            }
        )
    )

    assert items[0].filename == "resume"


def test_a_path_like_filename_is_reduced_to_its_base_name() -> None:
    items, _ = parse_candidates_page(_row(resume={"fileName": "../../etc/zhang.pdf"}))

    assert items[0].filename == "zhang.pdf"


# --------------------------------------------------------------------------
# Hard requirement 2: updated_at must be timezone-aware
# --------------------------------------------------------------------------


def test_epoch_millisecond_timestamps_become_aware_utc_instants() -> None:
    # DingTalk commonly returns epoch milliseconds; a naive conversion would
    # raise in `SourceItem.__post_init__`.
    items, _ = parse_candidates_page(_row(updateTime=1785126600000))

    assert items[0].updated_at == datetime(2026, 7, 27, 4, 30, tzinfo=timezone.utc)
    assert items[0].updated_at.tzinfo is not None


def test_epoch_milliseconds_are_also_accepted_as_a_string() -> None:
    items, _ = parse_candidates_page(_row(updateTime="1785126600000"))

    assert items[0].updated_at == datetime(2026, 7, 27, 4, 30, tzinfo=timezone.utc)


def test_a_timezone_naive_timestamp_is_rejected_rather_than_guessed() -> None:
    # Guessing a zone would silently shift the cursor by hours and skip or
    # replay a window of candidates. Fail loudly instead.
    with pytest.raises(ValueError, match="updateTime"):
        parse_candidates_page(_row(updateTime="2026-07-27T04:30:00"))


# --------------------------------------------------------------------------
# Hard requirement 3: every transport error maps to a port error
# --------------------------------------------------------------------------


@respx.mock
async def test_list_changed_calls_the_documented_endpoint_and_keeps_urls_out_of_items() -> None:
    route = respx.get(CANDIDATES_URL).mock(
        return_value=httpx.Response(200, json=_load("candidates-page"))
    )
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    items = await adapter.list_changed(datetime(2026, 7, 27, tzinfo=timezone.utc), 10)

    assert [i.external_id for i in items] == ["cand-1001", "cand-1002"]
    request = route.calls[0].request
    assert request.headers["x-acs-dingtalk-access-token"] == "corp-token-1"
    assert request.url.params["since"] == "2026-07-27T00:00:00+00:00"
    assert request.url.params["maxResults"] == "10"
    assert not hasattr(items[0], "download_url")


@respx.mock
async def test_a_listing_transport_failure_becomes_source_unavailable() -> None:
    # A raw httpx error escaping here aborts the run before the runner can
    # write `resume_sync_failed`; runner.py catches SourceUnavailable only.
    respx.get(CANDIDATES_URL).mock(side_effect=httpx.ReadTimeout("timeout"))
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    with pytest.raises(SourceUnavailable):
        await adapter.list_changed(datetime(2026, 7, 27, tzinfo=timezone.utc), 10)


@respx.mock
async def test_a_listing_http_status_becomes_source_unavailable() -> None:
    respx.get(CANDIDATES_URL).mock(return_value=httpx.Response(403, json={"code": "Forbidden"}))
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    with pytest.raises(SourceUnavailable):
        await adapter.list_changed(datetime(2026, 7, 27, tzinfo=timezone.utc), 10)


@respx.mock
async def test_a_payload_that_does_not_match_the_recorded_shape_is_source_unavailable() -> None:
    # The endpoint is unverified. If the real shape differs, the run must abort
    # with the cursor untouched — not raise ValueError out of the port.
    respx.get(CANDIDATES_URL).mock(
        return_value=httpx.Response(200, json={"result": {"items": []}})
    )
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    with pytest.raises(SourceUnavailable):
        await adapter.list_changed(datetime(2026, 7, 27, tzinfo=timezone.utc), 10)


@respx.mock
async def test_a_non_json_listing_body_is_source_unavailable() -> None:
    respx.get(CANDIDATES_URL).mock(return_value=httpx.Response(200, text="<html>gateway</html>"))
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    with pytest.raises(SourceUnavailable):
        await adapter.list_changed(datetime(2026, 7, 27, tzinfo=timezone.utc), 10)


@respx.mock
async def test_fetch_downloads_the_attachment_and_hashes_the_bytes() -> None:
    respx.get(CANDIDATES_URL).mock(
        return_value=httpx.Response(200, json=_load("candidates-page"))
    )
    respx.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b"%PDF-1.4 fake"))
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    items = await adapter.list_changed(datetime(2026, 7, 27, tzinfo=timezone.utc), 10)
    fetched = await adapter.fetch(items[0])

    assert fetched.content == b"%PDF-1.4 fake"
    assert fetched.sha256 == sha256(b"%PDF-1.4 fake").hexdigest()
    assert fetched.filename == "zhang.pdf"
    assert fetched.content_type == "application/pdf"


@respx.mock
async def test_a_download_transport_failure_becomes_item_unavailable() -> None:
    respx.get(CANDIDATES_URL).mock(
        return_value=httpx.Response(200, json=_load("candidates-page"))
    )
    respx.get(DOWNLOAD_URL).mock(side_effect=httpx.ConnectError("no route"))
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    items = await adapter.list_changed(datetime(2026, 7, 27, tzinfo=timezone.utc), 10)

    with pytest.raises(ItemUnavailable):
        await adapter.fetch(items[0])


@respx.mock
async def test_a_download_http_status_becomes_item_unavailable() -> None:
    respx.get(CANDIDATES_URL).mock(
        return_value=httpx.Response(200, json=_load("candidates-page"))
    )
    respx.get(DOWNLOAD_URL).mock(return_value=httpx.Response(404))
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    items = await adapter.list_changed(datetime(2026, 7, 27, tzinfo=timezone.utc), 10)

    with pytest.raises(ItemUnavailable):
        await adapter.fetch(items[0])


async def test_an_item_with_no_recorded_url_is_item_unavailable() -> None:
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")
    item = SourceItem(
        external_id="cand-9999",
        updated_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
        filename="resume.pdf",
        content_type="application/pdf",
        jd_code=None,
    )

    with pytest.raises(ItemUnavailable):
        await adapter.fetch(item)


@respx.mock
async def test_an_empty_download_is_item_unavailable() -> None:
    respx.get(CANDIDATES_URL).mock(
        return_value=httpx.Response(200, json=_load("candidates-page"))
    )
    respx.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b""))
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    items = await adapter.list_changed(datetime(2026, 7, 27, tzinfo=timezone.utc), 10)

    with pytest.raises(ItemUnavailable):
        await adapter.fetch(items[0])


# --------------------------------------------------------------------------
# The response body is bounded before it is buffered
# --------------------------------------------------------------------------


def _cap_downloads_at(monkeypatch, limit: int) -> None:
    """Shrink `MAX_RESUME_FILE_BYTES` for the adapter built after this call.

    The real cap is 20 MB; materialising 20 MB of test bytes would prove the
    same thing several orders of magnitude more slowly.
    """
    capped = get_settings().model_copy(update={"MAX_RESUME_FILE_BYTES": limit})
    monkeypatch.setattr("backend.app.services.sync.dingtalk.get_settings", lambda: capped)


async def _chunked(total: int, chunk: int = 8):
    """A body delivered in pieces and declaring no `Content-Length`."""
    for start in range(0, total, chunk):
        yield b"x" * min(chunk, total - start)


@respx.mock
async def test_a_download_at_the_size_limit_is_accepted(monkeypatch) -> None:
    _cap_downloads_at(monkeypatch, 32)
    respx.get(CANDIDATES_URL).mock(
        return_value=httpx.Response(200, json=_load("candidates-page"))
    )
    respx.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b"x" * 32))
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    items = await adapter.list_changed(SINCE, 10)
    fetched = await adapter.fetch(items[0])

    # Streaming must not change what an in-bounds download produces.
    assert fetched.content == b"x" * 32
    assert fetched.sha256 == sha256(b"x" * 32).hexdigest()


@respx.mock
async def test_a_download_declaring_too_many_bytes_is_refused(monkeypatch) -> None:
    # `Content-Length` is the cheap early reject: the body is never read.
    _cap_downloads_at(monkeypatch, 32)
    respx.get(CANDIDATES_URL).mock(
        return_value=httpx.Response(200, json=_load("candidates-page"))
    )
    respx.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b"x" * 33))
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    items = await adapter.list_changed(SINCE, 10)

    with pytest.raises(ItemUnavailable) as caught:
        await adapter.fetch(items[0])

    assert "max_resume_file_bytes" in str(caught.value)


@respx.mock
async def test_a_chunked_download_is_aborted_once_it_passes_the_limit(monkeypatch) -> None:
    """The bound cannot rest on a header the same untrusted party wrote.

    `MAX_RESUME_FILE_BYTES` is enforced by `UploadValidator`, which only ever
    sees bytes already whole in this worker's memory — so `response.content` let
    an unverified endpoint decide how much a worker allocates. A body with no
    declared length is the case that proves the counter, not the header, is what
    actually stops it.
    """
    _cap_downloads_at(monkeypatch, 32)
    respx.get(CANDIDATES_URL).mock(
        return_value=httpx.Response(200, json=_load("candidates-page"))
    )
    respx.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=_chunked(4096)))
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    items = await adapter.list_changed(SINCE, 10)

    with pytest.raises(ItemUnavailable) as caught:
        await adapter.fetch(items[0])

    assert "content-length" not in respx.calls[-1].response.headers
    message = str(caught.value)
    assert "max_resume_file_bytes" in message
    # Same rule as every other raise here: no URL, no filename.
    assert "example.invalid" not in message
    assert "zhang" not in message


@respx.mock
async def test_a_chunked_download_inside_the_limit_still_arrives(monkeypatch) -> None:
    _cap_downloads_at(monkeypatch, 4096)
    respx.get(CANDIDATES_URL).mock(
        return_value=httpx.Response(200, json=_load("candidates-page"))
    )
    respx.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=_chunked(100)))
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    items = await adapter.list_changed(SINCE, 10)
    fetched = await adapter.fetch(items[0])

    # Reassembled in order and whole, not merely under the cap.
    assert fetched.content == b"x" * 100


# --------------------------------------------------------------------------
# The corp access token must not reach a host we do not control
# --------------------------------------------------------------------------


@respx.mock
async def test_the_access_token_is_not_sent_to_a_foreign_download_host() -> None:
    respx.get(CANDIDATES_URL).mock(
        return_value=httpx.Response(200, json=_load("candidates-page"))
    )
    route = respx.get(DOWNLOAD_URL).mock(
        return_value=httpx.Response(200, content=b"%PDF-1.4 fake")
    )
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    items = await adapter.list_changed(datetime(2026, 7, 27, tzinfo=timezone.utc), 10)
    await adapter.fetch(items[0])

    assert "x-acs-dingtalk-access-token" not in route.calls[0].request.headers


@respx.mock
async def test_the_access_token_is_sent_when_the_download_stays_on_the_api_origin() -> None:
    on_origin = "https://api.dingtalk.com/v1.0/recruitment/attachments/1001"
    payload = _row(resume={"downloadUrl": on_origin})
    respx.get(CANDIDATES_URL).mock(return_value=httpx.Response(200, json=payload))
    route = respx.get(on_origin).mock(return_value=httpx.Response(200, content=b"%PDF-1.4 x"))
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    items = await adapter.list_changed(datetime(2026, 7, 27, tzinfo=timezone.utc), 10)
    await adapter.fetch(items[0])

    assert route.calls[0].request.headers["x-acs-dingtalk-access-token"] == "corp-token-1"


@respx.mock
async def test_the_access_token_is_never_sent_over_plain_http() -> None:
    # The URL comes out of the very response the host check exists to
    # distrust, so a downgraded scheme would otherwise put the corp
    # credential on the wire in cleartext.
    downgraded = "http://api.dingtalk.com/v1.0/recruitment/attachments/1001"
    respx.get(CANDIDATES_URL).mock(
        return_value=httpx.Response(200, json=_row(resume={"downloadUrl": downgraded}))
    )
    route = respx.get(downgraded).mock(return_value=httpx.Response(200, content=b"%PDF-1.4 x"))
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    items = await adapter.list_changed(SINCE, 10)
    await adapter.fetch(items[0])

    assert "x-acs-dingtalk-access-token" not in route.calls[0].request.headers


@respx.mock
async def test_the_access_token_is_sent_when_the_url_spells_out_the_default_port() -> None:
    # `netloc` carries the port, so comparing it would withhold the token from
    # the API host itself: every item would report `item_unavailable` on every
    # run — a total outage wearing a per-item error code.
    on_origin = "https://api.dingtalk.com:443/v1.0/recruitment/attachments/1001"
    respx.get(CANDIDATES_URL).mock(
        return_value=httpx.Response(200, json=_row(resume={"downloadUrl": on_origin}))
    )
    route = respx.get(on_origin).mock(return_value=httpx.Response(200, content=b"%PDF-1.4 x"))
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    items = await adapter.list_changed(SINCE, 10)
    await adapter.fetch(items[0])

    assert route.calls[0].request.headers["x-acs-dingtalk-access-token"] == "corp-token-1"


@respx.mock
async def test_a_foreign_host_wearing_the_api_host_as_userinfo_gets_no_token() -> None:
    # Moving from `netloc` to `hostname` must not open the other direction:
    # `hostname` reads past the userinfo, so this URL is `evil.example`.
    spoofed = "https://api.dingtalk.com@example.invalid/d/1001"
    respx.get(CANDIDATES_URL).mock(
        return_value=httpx.Response(200, json=_row(resume={"downloadUrl": spoofed}))
    )
    route = respx.get(spoofed).mock(return_value=httpx.Response(200, content=b"%PDF-1.4 x"))
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    items = await adapter.list_changed(SINCE, 10)
    await adapter.fetch(items[0])

    assert "x-acs-dingtalk-access-token" not in route.calls[0].request.headers


# --------------------------------------------------------------------------
# No candidate-supplied string may reach an exception message
# --------------------------------------------------------------------------


@respx.mock
async def test_a_download_failure_names_neither_the_url_nor_the_filename() -> None:
    # A `fileName` and a signed `downloadUrl` both carry a real person's
    # identity. An `ItemUnavailable` message is read by whoever debugs the run.
    signed = "https://example.invalid/d/1001?name=zhang-san&sig=deadbeef"
    respx.get(CANDIDATES_URL).mock(
        return_value=httpx.Response(
            200,
            json=_row(resume={"fileName": "zhang-san-13800138000.pdf", "downloadUrl": signed}),
        )
    )
    respx.get(signed).mock(side_effect=httpx.ConnectError("no route"))
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    items = await adapter.list_changed(SINCE, 10)

    with pytest.raises(ItemUnavailable) as caught:
        await adapter.fetch(items[0])

    message = str(caught.value)
    assert "zhang-san" not in message
    assert "13800138000" not in message
    assert "example.invalid" not in message
    assert "sig=" not in message
    assert "corp-token-1" not in message


async def test_a_missing_url_failure_names_neither_the_item_nor_its_file() -> None:
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")
    item = SourceItem(
        external_id="cand-9999",
        updated_at=SINCE,
        filename="zhang-san-13800138000.pdf",
        content_type="application/pdf",
        jd_code=None,
    )

    with pytest.raises(ItemUnavailable) as caught:
        await adapter.fetch(item)

    message = str(caught.value)
    assert "zhang-san" not in message
    assert "cand-9999" not in message


@respx.mock
async def test_describe_refuses_because_the_endpoint_does_not_exist_yet() -> None:
    """DingTalk cannot look a candidate up by id, and says so as a capability gap.

    Design §8.2 documents exactly one recruitment path — the list — and the OAS
    read on 2026-07-27 had no `recruitment` namespace at all. Guessing a
    single-candidate URL would make every replay 404, and the sweeper would
    count each 404 as a spent attempt until the row went terminal: permanent
    data loss manufactured entirely out of a guess. It refuses instead, and the
    sweeper spends no attempt on a refusal.
    """
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    with pytest.raises(SourceCapabilityUnavailable) as caught:
        await adapter.describe("cand-1001")

    # No route is registered on the respx mock, so any HTTP call at all would
    # raise instead of reaching this assertion.
    assert "cand-1001" not in str(caught.value)


async def test_describe_carries_no_candidate_identifier() -> None:
    adapter = DingTalkRecruitmentAdapter(access_token="corp-token-1")

    with pytest.raises(SourceCapabilityUnavailable) as caught:
        await adapter.describe("zhang-san-13800138000")

    assert "zhang-san" not in str(caught.value)
