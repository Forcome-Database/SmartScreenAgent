from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class SourceItem:
    """One changed candidate as the source describes it, before download."""

    external_id: str
    updated_at: datetime
    filename: str
    content_type: str
    jd_code: str | None


@dataclass(frozen=True)
class FetchedResume:
    content: bytes
    sha256: str
    filename: str
    content_type: str


class SourceUnavailable(Exception):
    """Listing failed: credentials, permission, or the provider itself."""


class ItemUnavailable(Exception):
    """One item could not be fetched; the rest of the run continues."""


class ResumeSourceAdapter(Protocol):
    """A resume origin. Implementations own endpoints; nothing else may."""

    source_name: str

    async def list_changed(self, since: datetime, limit: int) -> list[SourceItem]: ...

    async def fetch(self, item: SourceItem) -> FetchedResume: ...
