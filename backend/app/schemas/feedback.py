# backend/app/schemas/feedback.py
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from backend.app.services.read.pagination import PageMeta

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


class AgreementStats(BaseModel):
    total: int
    agreed: int
    disagreed: int
    hold: int
    agreement_rate: float | None


class JDAgreement(AgreementStats):
    jd_code: str


class DisagreementItem(BaseModel):
    feedback_id: int
    score_id: int
    candidate_id: int
    jd_code: str
    decision: str
    reason: str | None
    reviewer_display_name: str
    updated_at: datetime | None


class DisagreementPage(PageMeta):
    items: list[DisagreementItem]


class FeedbackReport(BaseModel):
    overall: AgreementStats
    by_jd: list[JDAgreement]
    disagreements: DisagreementPage
