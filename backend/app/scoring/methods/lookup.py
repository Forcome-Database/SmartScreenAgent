from __future__ import annotations

from typing import Any

from backend.app.rules.schema import RuleDimension
from backend.app.scoring.rule_engine import register


@register("lookup")
def lookup(candidate: dict[str, Any], dim: RuleDimension) -> dict[str, Any]:
    """字典查表（学历等枚举字段）。tier 与其它方法对齐用 high/low，
    便于前端评分卡统一渲染."""
    table = dim.table or {}
    edu = candidate.get("education")
    score = table.get(str(edu), 0.0) if edu else 0.0
    return {
        "score": score,
        "tier": "high" if score > 0 else "low",
        "evidence_quotes": [f"学历={edu}"] if edu else [],
        "reasoning": f"lookup hit {edu}={score}" if edu else "无学历字段",
    }
