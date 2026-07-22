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


class Confusion(BaseModel):
    tp: int
    fp: int
    tn: int
    fn: int


class MetricStats(BaseModel):
    labeled_total: int
    scored: int
    uncovered: int
    borderline_excluded: int
    confusion: Confusion
    precision: float | None
    recall: float | None
    f1: float | None
    accuracy: float | None


class JDMetrics(MetricStats):
    jd_code: str


class GoldenMetricsReport(BaseModel):
    overall: MetricStats
    by_jd: list[JDMetrics]
