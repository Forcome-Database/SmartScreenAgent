from __future__ import annotations

from pydantic import BaseModel, Field

from backend.app.schemas.golden_set import Confusion


class CreateDraftRequest(BaseModel):
    rule_schema: dict = Field(alias="schema_json")
    notes: str | None = None


class RuleVersionRef(BaseModel):
    id: int
    version: str
    status: str
    notes: str | None


class RuleMetrics(BaseModel):
    confusion: Confusion
    precision: float | None
    recall: float | None
    f1: float | None
    accuracy: float | None
    evaluated: int
    indeterminate: int
    borderline_excluded: int
    uncovered: int


class EvaluateResponse(BaseModel):
    draft: RuleMetrics
    baseline: RuleMetrics | None
    judge_dimensions_changed: bool
