from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import JD, AuditLog, Candidate, RuleVersion, Score
from backend.app.rules.schema import RuleSchema
from backend.app.scoring.hard_filter import run_hard_filters
from backend.app.scoring.llm_judge import LLMJudge
from backend.app.scoring.rule_engine import score_dimensions
from backend.app.services.llm.usage import LLMCallContext


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

    Database reads are snapshotted and committed before the LLM call so the
    provider wait never holds a business transaction or checked-out connection.
    Score and audit writes are committed in a fresh transaction before return.
    """

    def __init__(self, db: AsyncSession, judge: LLMJudge | None = None) -> None:
        self.db = db
        self.judge = judge or LLMJudge()

    async def _upsert_score(self, **values: Any) -> tuple[int, bool]:
        """Insert a `Score` row idempotently on `uq_scores_candidate_jd_rule`.

        Returns `(score_id, created)`. On conflict, `created` is `False` and
        `score_id` is the id of the pre-existing row — a retried scoring run
        for the same (candidate, jd, rule_version) returns that row instead
        of raising `IntegrityError`.
        """
        stmt = (
            pg_insert(Score)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["candidate_id", "jd_id", "rule_version_id"]
            )
            .returning(Score.id)
        )
        score_id = (await self.db.execute(stmt)).scalar_one_or_none()
        if score_id is not None:
            return score_id, True
        score_id = (
            await self.db.execute(
                select(Score.id).where(
                    Score.candidate_id == values["candidate_id"],
                    Score.jd_id == values["jd_id"],
                    Score.rule_version_id == values["rule_version_id"],
                )
            )
        ).scalar_one()
        return score_id, False

    async def run(
        self,
        *,
        candidate_id: int,
        jd_id: int,
        ingestion_job_id: int | None = None,
        trace_id: str | None = None,
    ) -> PipelineResult:
        candidate = (
            await self.db.execute(select(Candidate).where(Candidate.id == candidate_id))
        ).scalar_one()
        jd = (await self.db.execute(select(JD).where(JD.id == jd_id))).scalar_one()
        if not jd.active_rule_version_id:
            await self.db.rollback()
            raise ValueError(f"JD {jd.code} has no active rule version")
        rv = (
            await self.db.execute(
                select(RuleVersion).where(RuleVersion.id == jd.active_rule_version_id)
            )
        ).scalar_one()

        existing = (
            await self.db.execute(
                select(Score).where(
                    Score.candidate_id == candidate.id,
                    Score.jd_id == jd.id,
                    Score.rule_version_id == rv.id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            result = PipelineResult(
                score_id=existing.id,
                total_score=float(existing.total_score),
                grade=existing.grade,
                rejected=existing.grade == "rejected",
            )
            await self.db.commit()
            return result

        snapshot_candidate_id = candidate.id
        snapshot_parsed_markdown = candidate.parsed_markdown or ""
        extracted: dict[str, Any] = deepcopy(candidate.extracted_json or {})
        snapshot_jd_id = jd.id
        snapshot_jd_code = jd.code
        snapshot_rule_version_id = rv.id
        snapshot_rule_version = rv.version
        schema = RuleSchema.model_validate(deepcopy(rv.schema_json))
        extraction_meta = extracted.get("_meta")
        extraction_model = (
            extraction_meta.get("model")
            if isinstance(extraction_meta, dict)
            and isinstance(extraction_meta.get("model"), str)
            else None
        )
        await self.db.commit()

        # Stage A — hard filter
        hf = run_hard_filters(candidate=extracted, filters=schema.hard_filters)
        if hf.rejected:
            score_id, created = await self._upsert_score(
                candidate_id=snapshot_candidate_id,
                jd_id=snapshot_jd_id,
                rule_version_id=snapshot_rule_version_id,
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
                llm_model_extract=extraction_model,
                llm_judge_call_group_id=None,
            )
            if created:
                for entry in hf.audit_entries:
                    self.db.add(
                        AuditLog(
                            event_type="hard_filter_reject",
                            actor="system",
                            target_type="candidate",
                            target_id=snapshot_candidate_id,
                            payload={
                                **entry,
                                "jd_code": snapshot_jd_code,
                                "rule_version": snapshot_rule_version,
                            },
                            rule_version_id=snapshot_rule_version_id,
                        )
                    )
                await self.db.flush()
            persisted = await self.db.get(Score, score_id)
            assert persisted is not None
            result = PipelineResult(
                score_id=persisted.id,
                total_score=float(persisted.total_score),
                grade=persisted.grade,
                rejected=persisted.grade == "rejected",
            )
            await self.db.commit()
            return result

        # Stage B — deterministic rule engine
        rule_results = score_dimensions(extracted, schema.rule_dimensions)
        rule_total = sum((r.get("score") or 0) for r in rule_results)

        # Stage C — LLM judge
        judge_context = LLMCallContext(
            operation="judge",
            call_group_id=uuid4(),
            trace_id=trace_id,
            ingestion_job_id=ingestion_job_id,
            jd_id=snapshot_jd_id,
            rule_version_id=snapshot_rule_version_id,
        )
        judge_result = await self.judge.score(
            resume_text=snapshot_parsed_markdown,
            dims=schema.judge_dimensions,
            context=judge_context,
        )
        judge_total = sum((dimension.score or 0) for dimension in judge_result.dimensions)
        judge_payload = judge_result.model_dump(exclude={"call_group_id"})
        judge_call_group_id = (
            judge_result.call_group_id or judge_context.call_group_id
            if schema.judge_dimensions
            else None
        )

        total = rule_total + judge_total
        grade = _grade_from(total, schema)

        score_id, created = await self._upsert_score(
            candidate_id=snapshot_candidate_id,
            jd_id=snapshot_jd_id,
            rule_version_id=snapshot_rule_version_id,
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
            llm_model_main=judge_result.model or None,
            llm_model_extract=extraction_model,
            llm_judge_call_group_id=judge_call_group_id,
            cost_tokens=judge_result.tokens,
        )
        if created:
            self.db.add(
                AuditLog(
                    event_type="score",
                    actor="system",
                    target_type="candidate",
                    target_id=snapshot_candidate_id,
                    payload={
                        "jd_code": snapshot_jd_code,
                        "rule_version": snapshot_rule_version,
                        "total": total,
                        "grade": grade,
                    },
                    rule_version_id=snapshot_rule_version_id,
                )
            )
            await self.db.flush()
        persisted = await self.db.get(Score, score_id)
        assert persisted is not None
        result = PipelineResult(
            score_id=persisted.id,
            total_score=float(persisted.total_score),
            grade=persisted.grade,
            rejected=persisted.grade == "rejected",
        )
        await self.db.commit()
        return result
