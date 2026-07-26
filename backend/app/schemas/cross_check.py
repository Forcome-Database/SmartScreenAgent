from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, PlainSerializer

from backend.app.schemas.quality import _as_json_number

Number = Annotated[Decimal | None, PlainSerializer(_as_json_number, when_used="json")]

# Metadata only: no candidate name, ciphertext, object key, evidence quote, or
# reasoning may be declared here. Secondary dimensions arrive pre-sanitized.


class SuspiciousItem(BaseModel):
    cross_check_id: int
    score_id: int
    candidate_id: int
    jd_code: str
    primary_total_score: Number
    secondary_total_score: Number
    absolute_diff: Number
    threshold: Number
    secondary_dimensions: list[dict[str, Any]]
    sample_reasons: list[str]
    secondary_model: str
    completed_at: datetime | None


class SuspiciousPage(BaseModel):
    items: list[SuspiciousItem]
    page: int
    page_size: int
    total: int


class BackfillResult(BaseModel):
    dry_run: bool
    selected: int
    already_existing: int
    would_queue: int
    newly_queued: int
