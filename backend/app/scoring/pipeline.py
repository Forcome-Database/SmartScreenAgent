from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import JD, AuditLog, Candidate, RuleVersion, Score
from backend.app.rules.schema import RuleSchema
from backend.app.scoring.hard_filter import run_hard_filters
from backend.app.scoring.llm_judge import LLMJudge
from backend.app.scoring.rule_engine import score_dimensions


@dataclass
class PipelineResult:
    score_id: int
    total_score: float
    grade: str
    rejected: bool


def _grade_from(score: float, schema: RuleSchema) -> str:
    """Highest-threshold-first match; below all thresholds → 'rejected'."""
    for t in sorted(schema.grade_thresholds, key=lambda g: g.min, reverse=True):
        if score >= t.min:
            return t.grade
    return "rejected"


class ScoringPipeline:
    """Three-stage scoring orchestrator.

    Stage A: hard filters (reject early, emit audit row per failure).
    Stage B: deterministic rule engine over `rule_dimensions`.
    Stage C: LLM judge over `judge_dimensions`.

    All stages flush one `Score` row plus audit entries. The application-service
    caller owns commit/rollback so candidate ingestion can be atomic.
    """

    def __init__(self, db: AsyncSession, judge: LLMJudge | None = None) -> None:
        self.db = db
        self.judge = judge or LLMJudge()

    async def run(self, *, candidate_id: int, jd_id: int) -> PipelineResult:
        candidate = (
            await self.db.execute(select(Candidate).where(Candidate.id == candidate_id))
        ).scalar_one()
        jd = (await self.db.execute(select(JD).where(JD.id == jd_id))).scalar_one()
        if not jd.active_rule_version_id:
            raise ValueError(f"JD {jd.code} has no active rule version")
        rv = (
            await self.db.execute(
                select(RuleVersion).where(RuleVersion.id == jd.active_rule_version_id)
            )
        ).scalar_one()
        schema = RuleSchema.model_validate(rv.schema_json)
        extracted: dict[str, Any] = candidate.extracted_json or {}

        # Stage A — hard filter
        hf = run_hard_filters(candidate=extracted, filters=schema.hard_filters)
        if hf.rejected:
            for entry in hf.audit_entries:
                self.db.add(
                    AuditLog(
                        event_type="hard_filter_reject",
                        actor="system",
                        target_type="candidate",
                        target_id=candidate.id,
                        payload={
                            **entry,
                            "jd_code": jd.code,
                            "rule_version": rv.version,
                        },
                        rule_version_id=rv.id,
                    )
                )
            score_row = Score(
                candidate_id=candidate.id,
                jd_id=jd.id,
                rule_version_id=rv.id,
                total_score=0,
                grade="rejected",
                hard_filter_result={
                    "rejected": True,
                    "failed_filter_ids": hf.failed_filter_ids,
                    "audit_entries": hf.audit_entries,
                },
                rule_dimensions={},
                judge_dimensions=None,
                is_suspicious=False,
            )
            self.db.add(score_row)
            await self.db.flush()
            await self.db.refresh(score_row)
            return PipelineResult(
                score_id=score_row.id,
                total_score=0,
                grade="rejected",
                rejected=True,
            )

        # Stage B — deterministic rule engine
        rule_results = score_dimensions(extracted, schema.rule_dimensions)
        rule_total = sum((r.get("score") or 0) for r in rule_results)

        # Stage C — LLM judge
        judge_payload = await self.judge.score(
            resume_text=candidate.parsed_markdown or "",
            dims=schema.judge_dimensions,
        )
        judge_total = sum(
            (d.get("score") or 0) for d in judge_payload.get("dimensions", [])
        )

        total = rule_total + judge_total
        grade = _grade_from(total, schema)

        score_row = Score(
            candidate_id=candidate.id,
            jd_id=jd.id,
            rule_version_id=rv.id,
            total_score=total,
            grade=grade,
            hard_filter_result={
                "passed": True,
                "unknown_filter_ids": hf.unknown_filter_ids,
            },
            rule_dimensions={"items": rule_results, "subtotal": rule_total},
            judge_dimensions=judge_payload,
            cross_engine_diff=None,
            is_suspicious=False,
            llm_model_main=judge_payload.get("model"),
            cost_tokens=judge_payload.get("tokens", 0) or 0,
        )
        self.db.add(score_row)
        self.db.add(
            AuditLog(
                event_type="score",
                actor="system",
                target_type="candidate",
                target_id=candidate.id,
                payload={
                    "jd_code": jd.code,
                    "rule_version": rv.version,
                    "total": total,
                    "grade": grade,
                },
                rule_version_id=rv.id,
            )
        )
        await self.db.flush()
        await self.db.refresh(score_row)
        return PipelineResult(
            score_id=score_row.id,
            total_score=float(total),
            grade=grade,
            rejected=False,
        )
