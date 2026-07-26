from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from backend.app.services.sync.adapter import ItemUnavailable, SourceItem, SourceUnavailable
from backend.app.services.sync.dingtalk import (
    DingTalkRecruitmentAdapter,
    parse_candidates_page,
)

FIXTURES = Path(__file__).parents[1] / "contracts" / "dingtalk-recruitment" / "v1.0"
CANDIDATES_URL = "https://api.dingtalk.com/v1.0/recruitment/candidates"
DOWNLOAD_URL = "https://example.invalid/d/1001"


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


def test_the_adapter_declares_its_source_name() -> None:
    assert DingTalkRecruitmentAdapter.source_name == "dingtalk"


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
