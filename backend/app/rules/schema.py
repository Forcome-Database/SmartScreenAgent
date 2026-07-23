from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, model_validator


def _reject_boolean_weight(value: object) -> object:
    if isinstance(value, bool):
        raise ValueError("weight must be numeric, not boolean")
    return value


Weight = Annotated[
    float,
    BeforeValidator(_reject_boolean_weight),
    Field(ge=0, allow_inf_nan=False),
]


class Tier(BaseModel):
    label: Literal["high", "mid", "low", "unknown"]
    score: float | None = None
    keywords: list[str] = Field(default_factory=list)
    min_years: float | None = None
    max_years: float | None = None
    required_keywords: list[str] = Field(default_factory=list)
    note: str | None = None


class HardFilter(BaseModel):
    id: str
    rule: str
    action: Literal["reject"]
    audit_tag: str
    applies_to: list[str] = Field(default_factory=list)


class RuleDimension(BaseModel):
    id: str
    name: str
    weight: Weight
    method: Literal["tiered_keyword_match", "experience_years", "lookup"]
    tiers: list[Tier] = Field(default_factory=list)
    table: dict[str, float] | None = None


class JudgeDimension(BaseModel):
    id: str
    name: str
    weight: Weight
    prompt_hint: str
    tiers: list[Tier]


class GradeThreshold(BaseModel):
    grade: str
    min: float
    label: str


class RuleSchema(BaseModel):
    version: str
    jd_code: str
    total_score: float
    passing_threshold: float
    hard_filters: list[HardFilter]
    rule_dimensions: list[RuleDimension]
    judge_dimensions: list[JudgeDimension]
    grade_thresholds: list[GradeThreshold]

    @model_validator(mode="after")
    def _weights_sum_to_total(self) -> RuleSchema:
        s = sum(d.weight for d in self.rule_dimensions) + sum(
            d.weight for d in self.judge_dimensions
        )
        if abs(s - self.total_score) > 0.5:
            raise ValueError(f"weights sum {s} != total_score {self.total_score}")
        return self
