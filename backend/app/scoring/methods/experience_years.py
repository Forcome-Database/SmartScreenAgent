from __future__ import annotations

from datetime import date, datetime

from dateutil.parser import parse as parse_date

from backend.app.rules.schema import RuleDimension
from backend.app.scoring.rule_engine import register


def _to_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value


def _years_between(start: str | None, end: str | None, today: str | None = None) -> float:
    if not start:
        return 0.0
    try:
        s = _to_date(parse_date(start, default=datetime(1970, 1, 1)))
    except (ValueError, OverflowError):
        return 0.0
    if end:
        try:
            e = _to_date(parse_date(end, default=datetime(1970, 1, 1)))
        except (ValueError, OverflowError):
            e = _to_date(parse_date(today)) if today else date.today()
    else:
        e = _to_date(parse_date(today)) if today else date.today()
    delta = (e - s).days
    return max(delta / 365.25, 0.0)


def total_years_for_keywords(
    candidate: dict, keywords: list[str], today: str | None = None
) -> float:
    total = 0.0
    for exp in candidate.get("experiences", []):
        blob = f"{exp.get('title', '')} {exp.get('description', '')}"
        if any(all(tok in blob for tok in kw.split()) for kw in keywords):
            total += _years_between(exp.get("start"), exp.get("end"), today)
    return total


@register("experience_years")
def experience_years(candidate: dict, dim: RuleDimension) -> dict:
    total_years = sum(
        _years_between(e.get("start"), e.get("end"))
        for e in candidate.get("experiences", [])
    )
    full_text = " ".join(
        f"{e.get('title', '')} {e.get('description', '')}"
        for e in candidate.get("experiences", [])
    )

    for tier in sorted(dim.tiers, key=lambda t: t.score or 0, reverse=True):
        if tier.min_years is None:
            continue
        if total_years < tier.min_years:
            continue
        if tier.max_years is not None and total_years > tier.max_years:
            continue
        if tier.required_keywords:
            missing = [kw for kw in tier.required_keywords if kw not in full_text]
            if missing:
                continue
        return {
            "score": tier.score or 0,
            "tier": tier.label,
            "evidence_quotes": [f"累计 {round(total_years, 1)} 年外贸经历"],
            "reasoning": f"years={round(total_years, 1)} meets tier {tier.label}",
        }
    return {
        "score": 0,
        "tier": "low",
        "evidence_quotes": [f"累计 {round(total_years, 1)} 年外贸经历"],
        "reasoning": "no tier matched",
    }
