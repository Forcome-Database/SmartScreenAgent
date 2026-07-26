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
    SourceItem,
    SourceUnavailable,
)

# UNVERIFIED — design §8.2, no oas-ref, absent from the OAS read on 2026-07-27.
CANDIDATES_PATH = "/v1.0/recruitment/candidates"
# VERIFIED — the DingTalk v1.0 credential header, same one `oauth.py` uses.
ACCESS_TOKEN_HEADER = "x-acs-dingtalk-access-token"
REQUEST_TIMEOUT_SECONDS = 30.0

# `upload/validation.py` accepts exactly these, and the sync path gives it no
# content-type header, so it decides on the filename suffix ALONE.
ACCEPTED_SUFFIXES = frozenset({".pdf", ".docx", ".png", ".jpg", ".jpeg"})

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
    `hasMore` and `nextCursor` are documented but deliberately unread — the
    runner caps a run and the cursor picks up the remainder next time.

    The URLs are returned separately so `SourceItem` carries no transport
    detail; the adapter keeps them and the runner never sees them.

    A missing field raises. Yielding `None` would produce a candidate with no
    external id, which breaks deduplication for every subsequent run.
    """
    rows = payload.get("list")
    if not isinstance(rows, list):
        raise ValueError("recruitment page is missing list")

    items: list[SourceItem] = []
    urls: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("recruitment row is not an object")
        resume = row.get("resume")
        if not isinstance(resume, dict):
            raise ValueError("recruitment row is missing resume")
        external_id = str(_require(row, "candidateId"))
        download_url = str(_require(resume, "downloadUrl"))
        content_type = str(_require(resume, "fileType"))
        items.append(
            SourceItem(
                external_id=external_id,
                updated_at=_parse_updated_at(_require(row, "updateTime")),
                filename=_resolve_filename(
                    str(_require(resume, "fileName")), content_type, download_url
                ),
                content_type=content_type,
                jd_code=row.get("jobCode") or None,
            )
        )
        urls[external_id] = download_url
    return items, urls


class DingTalkRecruitmentAdapter:
    """DingTalk recruitment source, satisfying `ResumeSourceAdapter`.

    This is the ONLY class that knows the recruitment endpoints. The binding is
    provisional until the live probe runs — see the module docstring, design
    §2.2 and §16.1.

    Nothing candidate-supplied is ever logged or put in an exception message: a
    `fileName`, a `downloadUrl`, and a response body all carry a real person's
    identity. Failures carry a fixed string and chain the cause.
    """

    source_name = "dingtalk"

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
        return items[:limit]

    async def fetch(self, item: SourceItem) -> FetchedResume:
        download_url = self._download_urls.get(item.external_id)
        if not download_url:
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
            raise ItemUnavailable("attachment download failed") from exc
        if not content:
            raise ItemUnavailable("attachment download returned no bytes")
        return FetchedResume(
            content=content,
            sha256=sha256(content).hexdigest(),
            filename=item.filename,
            content_type=item.content_type,
        )

    async def _token(self) -> str:
        if self._access_token is not None:
            return self._access_token
        return await self._token_client.get_token()

    async def _download_headers(self, download_url: str) -> dict[str, str]:
        """Credential the download only while it stays on the API origin.

        Whether a recruitment `downloadUrl` points at DingTalk or at a
        pre-signed object-store link on a host we do not control is UNVERIFIED.
        Sending the corp access token to the latter would hand our application
        credential to a third party, so it is sent only on a same-origin URL.
        Same precedent as `MinerUClient`'s upload/result host split.
        """
        api_host = urlsplit(self._settings.DINGTALK_RECRUITMENT_BASE_URL).netloc.lower()
        if urlsplit(download_url).netloc.lower() != api_host:
            return {}
        return {ACCESS_TOKEN_HEADER: await self._token()}
