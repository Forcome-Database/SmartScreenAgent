from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from backend.app.services.read.pagination import PageMeta


class RankedCandidateItem(BaseModel):
    candidate_id: int
    score_id: int
    total_score: float
    grade: str
    rule_version: str
    scored_at: datetime


class RankedCandidateList(PageMeta):
    items: list[RankedCandidateItem]


class CandidateListItem(BaseModel):
    candidate_id: int
    created_at: datetime
    latest_state: str | None
    scored_jd_codes: list[str]


class CandidateList(PageMeta):
    items: list[CandidateListItem]


class CandidateScoreSummary(BaseModel):
    score_id: int
    jd_code: str
    total_score: float
    grade: str
    rule_version: str


class CandidateDetail(BaseModel):
    candidate_id: int
    name: str
    phone: str | None
    email: str | None
    age: int | None
    education: str | None
    experiences: list[dict]
    source: str
    created_at: datetime
    scores: list[CandidateScoreSummary]


class ScoreDetail(BaseModel):
    score_id: int
    candidate_id: int
    jd_code: str
    rule_version: str
    total_score: float
    grade: str
    hard_filter_result: dict
    rule_dimensions: dict
    judge_dimensions: dict | None


class RawFileLink(BaseModel):
    url: str
    expires_in_seconds: int


class JDItem(BaseModel):
    code: str
    name: str
    status: str
    active_rule_version: str | None


class JDList(PageMeta):
    items: list[JDItem]


class JDDetail(BaseModel):
    code: str
    name: str
    description: str | None
    status: str
    active_rule_version: dict | None


class RuleVersionItem(BaseModel):
    id: int
    version: str
    published_at: datetime
    published_by_user_id: int | None
    notes: str | None
    golden_set_metrics: dict | None
    is_active: bool


class RuleVersionList(PageMeta):
    items: list[RuleVersionItem]


class RuleDiffChange(BaseModel):
    path: str
    kind: str
    before: dict | int | float | str | None
    after: dict | int | float | str | None


class RuleDiffResponse(BaseModel):
    jd_code: str
    from_version: str
    to_version: str
    changes: list[RuleDiffChange]
