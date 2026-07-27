from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import JD, RuleVersion, Score
from backend.app.services.operations.reporting import (
    WINDOW_NAMES,
    InvalidOperationsWindow,
    summarize,
)

# The four tools of design §11.2. Each is a plain async function over an
# `AsyncSession`; the MCP server that publishes them is mounted elsewhere.
#
# Every tool reads the service layer directly rather than the REST app: the
# service role names no router tuple (design §11.3.2), so routing a tool call
# through HTTP would mean widening a credential nothing else needs.
#
# None of them may return a candidate name, phone, email, ciphertext, object
# key, evidence quote, or reasoning string.

WINDOWS: tuple[str, ...] = WINDOW_NAMES

# A model asking for "the top candidates" should not be able to page the whole
# table one call at a time.
MAX_TOP_CANDIDATES = 50

# Layer 1 of design §11.3, expressed as SQL.
#
# The judge payload is folded to `{id, tier, score}` inside PostgreSQL, so the
# evidence quotes, the reasoning, the confidence and the interview questions
# stored beside them are never sent to this process and cannot leak from it by
# accident. That is why this does not reuse
# `backend.app.services.read.candidates.get_score_detail`, which selects the
# whole `Score` row — quotes and all — and audits the read as the PII event it
# is.
#
# `hard_filter_result` is reduced the same way: only whether it says `rejected`
# crosses the wire, never the failed filter ids or their audit entries. The
# comparison is jsonb equality rather than a `::boolean` cast so a malformed
# stored value answers "not rejected" instead of raising.
SCORE_SUMMARY_SQL = text(
    """
    SELECT
        s.id AS score_id,
        j.code AS jd_code,
        s.total_score AS total_score,
        s.grade AS grade,
        COALESCE(s.hard_filter_result -> 'rejected' = 'true'::jsonb, FALSE)
            AS hard_filter_rejected,
        COALESCE(
            (
                SELECT jsonb_agg(
                           jsonb_build_object(
                               'id', d.value ->> 'id',
                               'tier', d.value ->> 'tier',
                               'score', d.value -> 'score'
                           )
                           ORDER BY d.ordinality
                       )
                FROM jsonb_array_elements(
                         CASE
                             WHEN jsonb_typeof(s.judge_dimensions -> 'dimensions') = 'array'
                             THEN s.judge_dimensions -> 'dimensions'
                             ELSE '[]'::jsonb
                         END
                     ) WITH ORDINALITY AS d(value, ordinality)
                WHERE jsonb_typeof(d.value) = 'object'
                  AND jsonb_typeof(d.value -> 'id') = 'string'
            ),
            '[]'::jsonb
        ) AS dimensions
    FROM scores AS s
    JOIN jds AS j ON j.id = s.jd_id
    WHERE s.id = :score_id
    """
)


def project_dimensions(payload: dict | None) -> list[dict[str, Any]]:
    """Reduce persisted judge dimensions to the three comparable fields.

    Quotes and reasoning are dropped here, at the boundary, so no tool can
    return them even by accident. `SCORE_SUMMARY_SQL` has already dropped them
    in the database; this is the same rule stated once more in Python, for any
    caller holding a payload that did not come through that statement.
    """
    entries = (payload or {}).get("dimensions")
    if not isinstance(entries, list):
        return []
    return [
        {"id": entry.get("id"), "tier": entry.get("tier"), "score": entry.get("score")}
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    ]


async def list_jds(db: AsyncSession) -> list[dict[str, Any]]:
    """Every JD with the rule version currently scoring against it."""
    rows = (
        await db.execute(
            select(JD.code, JD.name, RuleVersion.version)
            .outerjoin(RuleVersion, RuleVersion.id == JD.active_rule_version_id)
            .order_by(JD.code)
        )
    ).all()
    return [
        {"jd_code": code, "name": name, "active_rule_version": version}
        for code, name, version in rows
    ]


async def top_candidates(
    db: AsyncSession, *, jd_code: str, n: int = 10, days: int = 7
) -> list[dict[str, Any]]:
    """The best-scoring candidates for one JD over the last `days` days.

    Identified by id alone. Resolving an id to a person is a PII read, and the
    service role cannot reach the route that performs one.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        await db.execute(
            select(Score.candidate_id, Score.total_score, Score.grade, Score.created_at)
            .join(JD, JD.id == Score.jd_id)
            .where(JD.code == jd_code, Score.created_at >= since)
            .order_by(Score.total_score.desc(), Score.id.desc())
            .limit(min(max(n, 1), MAX_TOP_CANDIDATES))
        )
    ).all()
    return [
        {
            "candidate_id": candidate_id,
            "total_score": str(total_score),
            "grade": grade,
            "scored_at": created_at.isoformat(),
        }
        for candidate_id, total_score, grade, created_at in rows
    ]


async def score_summary(db: AsyncSession, *, score_id: int) -> dict[str, Any] | None:
    """One scorecard, reduced to what two candidates can be compared on.

    Returns `None` when no such score exists. Reads only; unlike the operator
    route this never records an audit event, because there is nothing here that
    an auditor would need to know had been read.
    """
    row = (await db.execute(SCORE_SUMMARY_SQL, {"score_id": score_id})).mappings().first()
    if row is None:
        return None
    return {
        "score_id": row["score_id"],
        "jd_code": row["jd_code"],
        "total_score": str(row["total_score"]),
        "grade": row["grade"],
        "hard_filter_rejected": bool(row["hard_filter_rejected"]),
        "dimensions": project_dimensions({"dimensions": row["dimensions"]}),
    }


async def operations_summary(db: AsyncSession, *, window: str = "7d") -> dict[str, Any]:
    """Spend and budget state for one window, from the WP7 report.

    Money is a string, as everywhere else on the wire: the report carries
    `Decimal`, and rounding it to a float on the way to a language model would
    be a silent loss.
    """
    if window not in WINDOWS:
        raise InvalidOperationsWindow(f"unsupported operations window: {window!r}")
    summary = await summarize(db, window=window, now=datetime.now(timezone.utc))
    return {
        "window": summary.window,
        "known_cost_cny": str(summary.current.known_cost_cny),
        "attempt_count": summary.current.attempt_count,
        "budgets": [
            {"scope": budget.scope, "state": budget.state, "spend_cny": str(budget.spend_cny)}
            for budget in summary.budgets
        ],
    }
