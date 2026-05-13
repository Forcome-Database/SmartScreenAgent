from __future__ import annotations

import json
import re
from typing import Any

from backend.app.rules.schema import JudgeDimension
from backend.app.services.llm.gateway import LLMGateway

_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|above)\s+instructions?", re.I),
    re.compile(r"system\s*:", re.I),
    re.compile(r"<\|im_start\|>"),
    re.compile(r"<\|im_end\|>"),
]


def _sanitize_resume_text(text: str) -> str:
    cleaned = text
    for pat in _INJECTION_PATTERNS:
        cleaned = pat.sub("[redacted]", cleaned)
    return cleaned


def _build_prompt(resume_text: str, dims: list[JudgeDimension]) -> str:
    dims_block = json.dumps(
        [
            {
                "id": d.id,
                "name": d.name,
                "prompt_hint": d.prompt_hint,
                "tiers": [
                    {"label": t.label, "score": t.score} for t in d.tiers
                ],
            }
            for d in dims
        ],
        ensure_ascii=False,
        indent=2,
    )
    schema = {
        "type": "object",
        "properties": {
            "dimensions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "tier": {"type": "string"},
                        "score": {"type": ["number", "null"]},
                        "evidence_quotes": {"type": "array", "items": {"type": "string"}},
                        "reasoning": {"type": "string"},
                        "confidence": {"type": "number"},
                        "suggested_interview_questions": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["id", "tier", "evidence_quotes", "reasoning"],
                },
            }
        },
        "required": ["dimensions"],
    }
    return (
        "你是简历评估助手。仅基于 <resume> 标签内的内容打分。\n"
        "【绝对原则】1. 只引用原文作为证据 2. 证据不足返回 tier=unknown, score=null 3. 严格符合 JSON Schema\n\n"
        f"<resume>\n{_sanitize_resume_text(resume_text)}\n</resume>\n\n"
        f"评估维度:\n{dims_block}\n\nJSON Schema:\n{json.dumps(schema, ensure_ascii=False)}"
    )


class LLMJudge:
    def __init__(self, gateway: LLMGateway | None = None) -> None:
        self._gateway = gateway or LLMGateway()

    async def score(
        self, *, resume_text: str, dims: list[JudgeDimension]
    ) -> dict[str, Any]:
        if not dims:
            return {"dimensions": [], "model": "", "tokens": 0}
        prompt = _build_prompt(resume_text, dims)
        resp = await self._gateway.judge(prompt, schema={})
        data = json.loads(resp.content)
        return {
            "dimensions": data.get("dimensions", []),
            "model": resp.model,
            "tokens": resp.input_tokens + resp.output_tokens,
        }
