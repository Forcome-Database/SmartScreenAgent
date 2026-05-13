from __future__ import annotations

import re
from typing import Any

from backend.app.rules.schema import RuleDimension
from backend.app.scoring.methods.experience_years import total_years_for_keywords
from backend.app.scoring.rule_engine import register


def _match(text: str, kw: str) -> bool:
    """Multi-token keyword: split by whitespace, all tokens must appear."""
    tokens = [t for t in re.split(r"\s+", kw) if t]
    return all(t in text for t in tokens)


@register("tiered_keyword_match")
def tiered_keyword_match(candidate: dict[str, Any], dim: RuleDimension) -> dict[str, Any]:
    blobs = [
        f"{e.get('title', '')} {e.get('description', '')}"
        for e in candidate.get("experiences", [])
    ]
    full_text = " ".join(blobs)

    # 按 tiers 从高到低尝试
    high_to_low = sorted(
        dim.tiers,
        key=lambda t: t.score or 0,
        reverse=True,
    )
    for tier in high_to_low:
        if not tier.keywords:
            continue
        hits = [kw for kw in tier.keywords if _match(full_text, kw)]
        if not hits:
            continue
        if tier.min_years is not None:
            years = total_years_for_keywords(candidate, hits)
            if years < tier.min_years:
                continue
        return {
            "score": tier.score or 0,
            "tier": tier.label,
            "evidence_quotes": _find_quotes(candidate, hits)[:3],
            "reasoning": f"hit {hits} in tier {tier.label}",
        }
    # 全部没命中，取最低档
    fallback = min(dim.tiers, key=lambda t: t.score or 0, default=None)
    return {
        "score": fallback.score if fallback and fallback.score is not None else 0,
        "tier": fallback.label if fallback else "low",
        "evidence_quotes": [],
        "reasoning": "no keyword hits",
    }


def _find_quotes(candidate: dict[str, Any], hits: list[str]) -> list[str]:
    quotes: list[str] = []
    for e in candidate.get("experiences", []):
        desc = e.get("description", "")
        for kw in hits:
            if _match(desc, kw):
                quotes.append(desc[:120])
                break
    return quotes
