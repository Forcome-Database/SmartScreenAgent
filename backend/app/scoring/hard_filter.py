from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from backend.app.rules.schema import HardFilter


@dataclass
class HardFilterResult:
    rejected: bool
    failed_filter_ids: list[str] = field(default_factory=list)
    unknown_filter_ids: list[str] = field(default_factory=list)
    audit_entries: list[dict[str, Any]] = field(default_factory=list)


_AGE_RULE = re.compile(r"age\s*<=\s*(\d+)")
_EDU_RANKS = {"高中": 1, "大专": 2, "专升本": 3, "本科": 4, "硕士": 5, "博士": 6}


def _eval_filter(rule: str, candidate: dict[str, Any]) -> bool | None:
    """Return True=pass, False=fail, None=unknown."""
    m = _AGE_RULE.match(rule.strip())
    if m:
        age = candidate.get("age")
        if age is None:
            return None
        return age <= int(m.group(1))
    if rule.startswith("education >="):
        target = rule.split(">=")[1].strip().strip("'\"")
        edu = candidate.get("education")
        if not edu:
            return None
        return _EDU_RANKS.get(str(edu), 0) >= _EDU_RANKS.get(target, 0)
    if rule.startswith("total_score >="):
        threshold = float(rule.split(">=")[1].strip())
        ts = candidate.get("total_score")
        if ts is None:
            return None
        return ts >= threshold
    return None


def run_hard_filters(
    *, candidate: dict[str, Any], filters: list[HardFilter]
) -> HardFilterResult:
    result = HardFilterResult(rejected=False)
    for f in filters:
        outcome = _eval_filter(f.rule, candidate)
        if outcome is None:
            result.unknown_filter_ids.append(f.id)
            continue
        if not outcome:
            result.rejected = True
            result.failed_filter_ids.append(f.id)
            result.audit_entries.append(
                {"filter_id": f.id, "audit_tag": f.audit_tag, "rule": f.rule}
            )
    return result
