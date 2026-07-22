# backend/app/schemas/feedback.py
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Decision = Literal["advance", "reject", "hold"]


class FeedbackUpsertRequest(BaseModel):
    decision: Decision
    reason: str | None = None


class FeedbackItem(BaseModel):
    id: int
    score_id: int
    reviewer_user_id: int
    reviewer_display_name: str
    decision: str
    reason: str | None
    ai_agreed: bool | None
    created_at: datetime
    updated_at: datetime | None
