"""The DingTalk recruitment source adapter.

**The endpoint binding in this file is UNVERIFIED.** Every recruitment path,
query parameter, and response field below is transcribed from
`docs/specs/2026-05-12-resume-screening-agent-design.md` §8.2, which names them
but — unlike its OAuth section — cites no `oas-ref:` for any of them. A read of
the project's DingTalk OAS on 2026-07-27 found no `recruitment` namespace at
all, and the recruitment API permission has not been granted, so nothing here
has ever been checked against a real response. See WP8 design §2.2 and §16.1.

What IS verified: the corp access token this adapter authenticates with. That
endpoint (`POST /v1.0/oauth2/accessToken`) is read from the authoritative OAS —
see `backend.app.services.dingtalk.oauth.DingTalkCorpTokenClient`.

A THIRD fact about this API was established on 2026-07-27 and belongs in the
same inventory: **the recruitment surface documents no single-candidate
lookup.** Design §8.2 names the list path and nothing else — no
`GET /candidates/{id}`, and no id filter on the list. That is a finding about
the API, not an implementation note, and it is the first thing to re-check when
the permission is granted, because `describe` (and therefore the whole bounded
replay of failed items) cannot be bound until it is answered. See `describe`
below.

The unverified surface is contained here on purpose: this is the only file in
the codebase that knows these endpoints exist. Recorded fixtures in
`backend/tests/contracts/dingtalk-recruitment/v1.0/` pin our reading of the
documentation as executable assertions, and
`backend/tests/external/test_dingtalk_recruitment_contract.py` (marked
`external_contract`) is the live probe that will settle it once the permission
is granted. If the real shape differs, only this file and those fixtures change.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import httpx

from backend.app.config import get_settings
from backend.app.services.dingtalk.oauth import DingTalkCorpTokenClient
from backend.app.services.sync.adapter import (
    FetchedResume,
    ItemUnavailable,
    JobMeta,
    SourceCapabilityUnavailable,
    SourceItem,
    SourceUnavailable,
)

# UNVERIFIED — design §8.2, no oas-ref, absent from the OAS read on 2026-07-27.
CANDIDATES_PATH = "/v1.0/recruitment/candidates"
# UNVERIFIED, same status as CANDIDATES_PATH — see the module docstring.
JOBS_PATH = "/v1.0/recruitment/jobs"
# VERIFIED — the DingTalk v1.0 credential header, same one `oauth.py` uses.
ACCESS_TOKEN_HEADER = "x-acs-dingtalk-access-token"
REQUEST_TIMEOUT_SECONDS = 30.0

# `upload/validation.py` accepts exactly these, and the sync path gives it no
# content-type header, so it decides on the filename suffix ALONE.
ACCEPTED_SUFFIXES = frozenset({".pdf", ".docx", ".png", ".jpg", ".jpeg"})

# A candidate row can name a person without attaching a resume — an ordinary
# ATS state, not a malformed response. Such an item is still emitted so the
# cursor moves past it, wearing this stand-in name. It holds nothing the
# candidate supplied, and it is never used: the item has no recorded download
# URL, so `fetch` refuses it before the name can reach storage.
MISSING_ATTACHMENT_FILENAME = "attachment-unavailable"

_SUFFIX_BY_CONTENT_TYPE = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}


def _require(row: dict, key: str) -> object:
    value = row.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"recruitment row is missing {key}")
    return value


def _optional_text(value: object) -> str:
    """One field the source is allowed to omit, as blank when it does."""
    if value is None:
        return ""
    return str(value).strip()


def _comparable_host(url: str) -> str:
    """The host of a URL, normalised so two spellings of it compare equal.

    `hostname` rather than `netloc`: it is already lowercased and carries
    neither the port nor any userinfo, both of which a URL taken from an
    unverified response can carry — enough to make an on-origin URL look
    foreign (`api.dingtalk.com:443`) or a foreign one look on-origin
    (`api.dingtalk.com@evil.example`). The DNS root dot is dropped as well,
    because `api.dingtalk.com.` names the same host.
    """
    return (urlsplit(url).hostname or "").rstrip(".")


def _suffix_of(name: str) -> str:
    return PurePosixPath(name).suffix.lower()


def _resolve_filename(raw_name: str, content_type: str, download_url: str) -> str:
    """Give the item a filename the WP1 upload gate can act on.

    The sync path constructs an `UploadFile` with no headers, so
    `upload.content_type` is `None` and `upload/validation.py` decides
    acceptance on the suffix alone — `FetchedResume.content_type` is accepted by
    the runner and then discarded. A name that arrives without an extension
    would therefore turn every item into `unsupported_attachment`, forever.

    Whether the recruitment payload's `fileName` always carries an extension is
    UNVERIFIED (design open question §16.3), so an extension is derived from the
    declared type, then from the download URL's path. If neither yields one the
    base name is passed through unchanged: an attachment we genuinely cannot
    classify must fail as ONE item at the validator, not abort the page here.
    """
    # `.name` also strips any directory component: a `fileName` is candidate-
    # controlled and must never reach the storage layer as a path.
    name = PurePosixPath(raw_name.replace("\\", "/")).name.strip()
    if not name:
        name = "resume"
    if _suffix_of(name) in ACCEPTED_SUFFIXES:
        return name
    declared = content_type.split(";", 1)[0].strip().lower()
    derived = _SUFFIX_BY_CONTENT_TYPE.get(declared) or _suffix_of(urlsplit(download_url).path)
    if derived in ACCEPTED_SUFFIXES:
        return f"{name}{derived}"
    return name


def _parse_updated_at(value: object) -> datetime:
    """Read one instant as timezone-aware, or refuse it.

    `SourceItem.__post_init__` raises on a naive datetime, and the cursor
    compares this value against an aware instant. DingTalk commonly returns
    epoch milliseconds, so both that and ISO-8601 are read here, always with an
    explicit UTC tzinfo.

    A naive ISO string is rejected rather than assumed to be UTC: the real
    timezone of this field is UNVERIFIED, and guessing wrong would shift the
    cursor by hours and silently skip or replay a window of candidates.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    text = str(value).strip()
    if text.isdigit():
        return datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc)
    if text.endswith(("Z", "z")):
        # Python 3.10's `fromisoformat` does not accept the "Z" designator.
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("recruitment row has a timezone-naive updateTime")
    return parsed


def parse_candidates_page(payload: dict) -> tuple[list[SourceItem], dict[str, str]]:
    """Map one documented page into source items plus their download URLs.

    UNVERIFIED shape (design §8.2): `list[]` of rows carrying `candidateId`,
    `updateTime`, `jobCode`, and `resume{fileName, fileType, downloadUrl}`.
    `hasMore` and `nextCursor` are documented but deliberately unread: the
    runner caps a run and the cursor picks up the remainder next time — which
    is true *because* `list_changed` sorts oldest-first before it truncates.
    Without that sort a newest-first feed would lose every item the cap
    dropped, permanently and silently. With it, an unread `hasMore` costs
    observability only (`SyncReport.truncated` under-reports a server-paged
    run), never data.

    The URLs are returned separately so `SourceItem` carries no transport
    detail; the adapter keeps them and the runner never sees them.

    Identity is required, the attachment is not:

    - A row missing `candidateId` or `updateTime` **raises**. An item with no
      external id breaks deduplication for every later run, and one with no
      timestamp cannot move the cursor; skipping either silently would corrupt
      both.
    - A row with no `resume`, no `downloadUrl`, or no `fileName` is emitted
      anyway, with no recorded URL — so `fetch` fails that ONE item and the
      cursor still advances past it (design §9). A candidate with no resume
      attached is an ordinary ATS state; raising here would wedge every future
      run behind the first one, over a window that grows without bound.
    """
    rows = payload.get("list")
    if not isinstance(rows, list):
        raise ValueError("recruitment page is missing list")

    items: list[SourceItem] = []
    urls: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("recruitment row is not an object")
        external_id = str(_require(row, "candidateId"))
        updated_at = _parse_updated_at(_require(row, "updateTime"))
        resume = row.get("resume")
        attachment = resume if isinstance(resume, dict) else {}
        download_url = _optional_text(attachment.get("downloadUrl"))
        raw_name = _optional_text(attachment.get("fileName"))
        # `fileType` is optional: a `fileName` that already carries an accepted
        # suffix needs no declared type, and the upload gate reads the suffix
        # alone anyway.
        content_type = _optional_text(attachment.get("fileType"))
        if download_url and raw_name:
            filename = _resolve_filename(raw_name, content_type, download_url)
            urls[external_id] = download_url
        else:
            # Recording no URL is what makes `fetch` raise `ItemUnavailable`
            # for this item and this item only.
            filename = MISSING_ATTACHMENT_FILENAME
        job_code = row.get("jobCode")
        items.append(
            SourceItem(
                external_id=external_id,
                updated_at=updated_at,
                filename=filename,
                content_type=content_type,
                # Coerced: a numeric `jobCode` would otherwise reach
                # `ingest_upload(jd_code=...)`, and a string column, as an int.
                jd_code=str(job_code) if job_code else None,
            )
        )
    return items, urls


def parse_jobs_page(payload: dict) -> list[JobMeta]:
    """Map one documented page of recruitment jobs into `JobMeta`.

    UNVERIFIED shape, by the same standard as `parse_candidates_page` (design
    §8.2 names no jobs endpoint at all, so this is an extrapolation from the
    candidates shape, not a documented one): `list[]` of rows carrying
    `jobCode`, `name`, and `description`.

    Identity and display name are required, the description is not:

    - A row missing `jobCode` or `name` **raises**. A JD synced with no code
      cannot be matched to a resume's `SourceItem.jd_code` on any later run,
      and one with no name would violate `jds.name NOT NULL` at the database
      boundary instead of failing here with a clear cause.
    - A missing `description` becomes `""` rather than raising: WP6c already
      treats a JD with no description as ordinary, and it is not a field
      anything downstream keys on.

    No `active_rule_version_id` and no `status` are read here because
    `JobMeta` has no such fields — see its docstring in `adapter.py`.
    """
    rows = payload.get("list")
    if not isinstance(rows, list):
        raise ValueError("recruitment jobs page is missing list")

    jobs: list[JobMeta] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("recruitment job row is not an object")
        code = str(_require(row, "jobCode"))
        name = str(_require(row, "name"))
        description = _optional_text(row.get("description"))
        jobs.append(JobMeta(code=code, name=name, description=description))
    return jobs


class DingTalkRecruitmentAdapter:
    """DingTalk recruitment source, satisfying `ResumeSourceAdapter`.

    This is the ONLY class that knows the recruitment endpoints. The binding is
    provisional until the live probe runs — see the module docstring, design
    §2.2 and §16.1.

    Nothing candidate-supplied is ever logged or put in an exception message: a
    `fileName`, a `downloadUrl`, and a response body all carry a real person's
    identity. Failures carry a fixed string and chain the cause.
    """

    # The primary key of `sync_cursors`, and what lands in
    # `sync_source_items.source` and `IngestionJob.source`. Settled as
    # `dingtalk_recruitment` in commit d7b531f: DingTalk may later be a source
    # through a different channel, and changing this after a production run
    # orphans every cursor and every ledger row. Every consumer reads
    # `adapter.source_name`; nothing hardcodes a second copy.
    source_name = "dingtalk_recruitment"

    def __init__(
        self,
        access_token: str | None = None,
        *,
        token_client: DingTalkCorpTokenClient | None = None,
    ) -> None:
        """Authenticate with a supplied token, or mint a corp one on demand.

        `access_token` exists for the live probe, which is handed a token by an
        operator. In production the adapter mints and caches its own via
        `DingTalkCorpTokenClient` — a recruitment pull is server-to-server and
        has no logged-in user to borrow a token from.
        """
        self._settings = get_settings()
        self._access_token = access_token
        self._token_client = token_client or DingTalkCorpTokenClient()
        self._download_urls: dict[str, str] = {}

    async def list_changed(self, since: datetime, limit: int) -> list[SourceItem]:
        url = f"{self._settings.DINGTALK_RECRUITMENT_BASE_URL}{CANDIDATES_PATH}"
        try:
            token = await self._token()
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.get(
                    url,
                    # UNVERIFIED parameter names (design §8.2).
                    params={"since": since.isoformat(), "maxResults": limit},
                    headers={ACCESS_TOKEN_HEADER: token},
                )
                response.raise_for_status()
                payload = response.json()
            items, urls = parse_candidates_page(payload)
        except Exception as exc:
            # Deliberately total. `runner.py` catches `SourceUnavailable` around
            # this call and NOTHING else, so anything else escaping aborts the
            # run with no `resume_sync_failed` audit row. That containment is
            # also why the parse lives inside: this endpoint is unverified, so a
            # shape mismatch must abort the run with the cursor untouched rather
            # than raise `ValueError` out of the port.
            raise SourceUnavailable("recruitment listing failed") from exc
        self._download_urls.update(urls)
        # Oldest first, ALWAYS, and before the cap bites.
        #
        # The runner truncates by POSITION (`offered[:max_items]`) and then
        # advances the cursor by VALUE (`max(updated_at for ... in processed)`).
        # Those two agree only on an ascending list. Served newest-first — the
        # more common convention for a "changed since" feed, and UNVERIFIED
        # here — the run would keep the newest items, drop the oldest, and then
        # jump the cursor to the global maximum: the next `overlap_start` opens
        # *after* the dropped items and they are never listed again. When the
        # page is shorter than the cap that loss is completely silent, because
        # `truncated` reads false.
        #
        # Sorting ascending makes the kept window the oldest one and the cursor
        # advance minimally, which is correct under either server ordering. The
        # live probe records which one the real endpoint uses.
        items.sort(key=lambda item: item.updated_at)
        return items[:limit]

    async def list_jobs(self) -> list[JobMeta]:
        """List every recruitment job as JD metadata — see `parse_jobs_page`.

        Unlike `list_changed`, this has no cursor and no cap: WP8 §10 syncs JD
        *metadata*, a small, low-churn set, not a growing stream of candidates,
        so there is nothing here for the bounded-cap and cursor machinery to
        protect against.
        """
        url = f"{self._settings.DINGTALK_RECRUITMENT_BASE_URL}{JOBS_PATH}"
        try:
            token = await self._token()
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.get(url, headers={ACCESS_TOKEN_HEADER: token})
                response.raise_for_status()
                payload = response.json()
            return parse_jobs_page(payload)
        except Exception as exc:
            # Deliberately total, same reasoning as `list_changed`: this is the
            # only call `sync_jd_metadata` makes, so a raw transport error
            # escaping here would surface as an unhandled exception instead of
            # the `SourceUnavailable` its caller is written to expect.
            raise SourceUnavailable("recruitment job listing failed") from exc

    async def fetch(self, item: SourceItem) -> FetchedResume:
        download_url = self._download_urls.get(item.external_id)
        if not download_url:
            # Either the item did not come from this adapter's own listing, or
            # its row carried no usable attachment — `parse_candidates_page`
            # deliberately emits that row and records no URL for it, so the
            # failure lands on this ONE item and the cursor still moves past
            # it. The message names neither the item nor its file.
            raise ItemUnavailable("no download url for item")
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.get(
                    download_url, headers=await self._download_headers(download_url)
                )
                response.raise_for_status()
                content = response.content
        except Exception as exc:
            # Deliberately total, for the same reason as `list_changed`: the
            # runner catches `ItemUnavailable` here and nothing else, and a raw
            # transport error escaping mid-loop leaves items already committed
            # and enqueued with no cursor write and no audit row at all.
            #
            # `ItemUnavailable` is correct HERE and only here. This mapping is
            # per-item because the caller is the runner, which is already
            # inside a listed window: one item's download failing costs that
            # item an attempt and the batch continues.
            #
            # WHEN `describe` IS BOUND TO A REAL ENDPOINT, IT MUST NOT COPY
            # THIS MAPPING. Replay calls `describe` (and then `fetch`) once per
            # failed ledger row, so a provider outage would raise here for
            # EVERY row, spending an attempt on each; at
            # `SYNC_MAX_ITEM_ATTEMPTS=3` and a 3600s sweep, roughly three hours
            # of downtime drives the entire failed queue terminal without one
            # genuine per-item failure. Transport-level failures on the replay
            # path — connection refused, 5xx, timeout — must raise
            # `SourceUnavailable`, which aborts the pass and spends nothing.
            # Reserve `ItemUnavailable` for the source answering "not here"
            # (a 404, or an empty candidate row).
            raise ItemUnavailable("attachment download failed") from exc
        if not content:
            raise ItemUnavailable("attachment download returned no bytes")
        return FetchedResume(
            content=content,
            sha256=sha256(content).hexdigest(),
            filename=item.filename,
            content_type=item.content_type,
        )

    async def describe(self, external_id: str) -> SourceItem:
        """Refuse, because DingTalk documents no way to ask.

        The recruitment surface has one documented path — the list (§8.2) — and
        the OAS read on 2026-07-27 had no `recruitment` namespace at all. There
        is no single-candidate GET and no id filter to guess from.

        Guessing one would be actively destructive rather than merely wrong. A
        wrong URL answers 404, `fetch` would never run, and the sweeper would
        record a spent attempt for every failed row on every pass until each
        one crossed `SYNC_MAX_ITEM_ATTEMPTS` and stopped being selected —
        permanent, silent loss of precisely the rows replay exists to recover,
        manufactured entirely out of a guess. Refusing costs nothing: the rows
        wait, keeping their attempts and their real error codes, until this is
        bound for real.

        `SourceCapabilityUnavailable`, not `ItemUnavailable`: the sweeper
        spends an attempt on the latter and nothing on this one.

        No request is made — deliberately, since there is no endpoint to make it
        against. When the recruitment permission is granted, this method and its
        fixture are what change; the sweeper does not.
        """
        # `external_id` is a real person's identifier in the recruiting system
        # and never reaches the message, matching every other raise in this file.
        raise SourceCapabilityUnavailable(
            "dingtalk recruitment documents no single-candidate lookup; "
            "replay stays inert until the recruitment permission is granted"
        )

    async def _token(self) -> str:
        if self._access_token is not None:
            return self._access_token
        return await self._token_client.get_token()

    async def _download_headers(self, download_url: str) -> dict[str, str]:
        """Credential the download only over HTTPS to the API host.

        Whether a recruitment `downloadUrl` points at DingTalk or at a
        pre-signed object-store link on a host we do not control is UNVERIFIED.
        Sending the corp access token to the latter would hand our application
        credential to a third party, so it goes out only when the URL is on the
        API host AND uses HTTPS.

        The scheme is half the guard, not a formality: this URL comes out of
        the very response the host check exists to distrust, so an
        `http://api.dingtalk.com/...` would otherwise put the corp credential
        on the wire in cleartext.

        Hosts are compared through `_comparable_host` rather than `netloc`,
        which carries the port and any userinfo. `netloc` withholds the token
        from `https://api.dingtalk.com:443/...` — i.e. exactly where it is
        required — and every item then reports `item_unavailable` on every run:
        a total outage wearing a per-item error code. Same precedent as
        `MinerUClient`'s upload/result host split.
        """
        api_host = _comparable_host(self._settings.DINGTALK_RECRUITMENT_BASE_URL)
        if not api_host or urlsplit(download_url).scheme != "https":
            return {}
        if _comparable_host(download_url) != api_host:
            return {}
        return {ACCESS_TOKEN_HEADER: await self._token()}
