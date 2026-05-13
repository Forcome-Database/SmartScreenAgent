from __future__ import annotations

from datetime import date, datetime

from dateutil.parser import parse as parse_date


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
