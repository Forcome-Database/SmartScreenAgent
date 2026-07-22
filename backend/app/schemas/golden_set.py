# backend/app/schemas/golden_set.py
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from backend.app.services.read.pagination import PageMeta


class GoldenImportError(BaseModel):
    row: int
    candidate_id: int | None
    jd_code: str | None
    reason: str


class GoldenImportResult(BaseModel):
    total: int
    created: int
    updated: int
    errors: list[GoldenImportError]


class GoldenSetItem(BaseModel):
    id: int
    candidate_id: int
    jd_code: str
    label: str
    imported_at: datetime
    imported_by_display_name: str


class GoldenSetList(PageMeta):
    items: list[GoldenSetItem]
