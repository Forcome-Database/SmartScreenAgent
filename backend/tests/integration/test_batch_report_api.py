from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import JD, Candidate, RuleVersion, Score
from backend.app.security.crypto import encrypt_pii

pytestmark = pytest.mark.integration

REPORT = "/api/v1/reports/batch"
FIXTURE = Path(__file__).parents[1] / "fixtures" / "sample_rule_v1.json"
NOW = datetime.now(UTC)
IN_WINDOW = NOW - timedelta(days=1)


async def _seed_jd(db: AsyncSession, code: str) -> tuple[JD, RuleVersion]:
    schema = json.loads(FIXTURE.read_text(encoding="utf-8"))
    schema["jd_code"] = code
    jd = JD(code=code, name=code, description="", status="active")
    db.add(jd)
    await db.flush()
    version = RuleVersion(
        jd_id=jd.id,
        version="v1",
        published_at=NOW - timedelta(days=60),
        schema_json=schema,
    )
    db.add(version)
    await db.flush()
    jd.active_rule_version_id = version.id
    await db.flush()
    return jd, version


async def _seed_score(
    db: AsyncSession,
    jd: JD,
    version: RuleVersion,
    *,
    grade: str,
    hard_tags: list[str] | None = None,
    rule_score: float = 60,
    created_at: datetime | None = None,
) -> Score:
    candidate = Candidate(
        source="upload",
        name_cipher=encrypt_pii("private-name"),
        pii_hash=uuid4().hex,
        extracted_json={},
    )
    db.add(candidate)
    await db.flush()
    score = Score(
        candidate_id=candidate.id,
        jd_id=jd.id,
        rule_version_id=version.id,
        total_score=10 if grade == "rejected" else 80,
        grade=grade,
        hard_filter_result={
            "rejected": bool(hard_tags),
            "audit_entries": [
                {"filter_id": tag, "audit_tag": tag, "rule": {}}
                for tag in (hard_tags or [])
            ],
        },
        rule_dimensions={"items": [{"id": "north_america", "score": rule_score}]},
        judge_dimensions=None,
        created_at=created_at or IN_WINDOW,
    )
    db.add(score)
    await db.flush()
    return score


async def test_report_requires_a_filter(client, db_session, auth_headers):
    response = await client.get(REPORT, headers=await auth_headers("hr"))

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "batch_filter_required"


async def test_role_matrix(client, db_session, auth_headers):
    jd, _version = await _seed_jd(db_session, f"BR_{uuid4().hex[:6]}")
    await db_session.commit()

    assert (await client.get(f"{REPORT}?jd_code={jd.code}")).status_code == 401
    for role in ("hr", "hr_lead", "admin"):
        response = await client.get(
            f"{REPORT}?jd_code={jd.code}", headers=await auth_headers(role)
        )
        assert response.status_code == 200


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("from=2026-07-01T00:00:00&to=2026-07-02T00:00:00%2B00:00", "invalid_batch_window"),
        (
            "from=2026-07-02T00:00:00%2B00:00&to=2026-07-01T00:00:00%2B00:00",
            "invalid_batch_window",
        ),
        (
            "from=2026-01-01T00:00:00%2B00:00&to=2026-07-01T00:00:00%2B00:00",
            "batch_window_too_large",
        ),
    ],
)
async def test_window_errors(client, db_session, auth_headers, query: str, code: str):
    response = await client.get(f"{REPORT}?{query}", headers=await auth_headers("hr"))

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == code


async def test_report_counts_grades_and_deterministic_reasons(
    client, db_session, auth_headers
):
    jd, version = await _seed_jd(db_session, f"BR_{uuid4().hex[:6]}")
    await _seed_score(db_session, jd, version, grade="rejected", hard_tags=["no_degree"])
    await _seed_score(
        db_session, jd, version, grade="rejected", hard_tags=["no_degree"], rule_score=1
    )
    await _seed_score(db_session, jd, version, grade="L1")
    # Outside the default 30-day window.
    await _seed_score(
        db_session,
        jd,
        version,
        grade="rejected",
        hard_tags=["stale"],
        created_at=NOW - timedelta(days=45),
    )
    await db_session.commit()

    body = (
        await client.get(f"{REPORT}?jd_code={jd.code}", headers=await auth_headers("hr"))
    ).json()

    assert body["total_scored"] == 3
    assert body["total_rejected"] == 2
    assert body["grade_counts"] == {"rejected": 2, "L1": 1}
    assert body["percentages_may_overlap"] is True
    reasons = {(r["reason_type"], r["reason_key"]): r for r in body["reasons"]}
    assert reasons[("hard_filter", "no_degree")]["affected_scores"] == 2
    assert reasons[("hard_filter", "no_degree")]["percentage"] == 100
    assert reasons[("rule_low", "north_america")]["percentage"] == 50
    assert "stale" not in body["reasons"].__str__()


async def test_report_body_has_no_candidate_content(client, db_session, auth_headers):
    jd, version = await _seed_jd(db_session, f"BR_{uuid4().hex[:6]}")
    await _seed_score(db_session, jd, version, grade="rejected", hard_tags=["no_degree"])
    await db_session.commit()

    raw = (
        await client.get(f"{REPORT}?jd_code={jd.code}", headers=await auth_headers("hr"))
    ).text

    for forbidden in ("private-name", "name_cipher", "evidence_quotes", "reasoning"):
        assert forbidden not in raw
