from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import JD, Candidate, RuleVersion, Score, ScoreCrossCheck
from backend.app.security.crypto import encrypt_pii

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SUSPICIOUS = "/api/v1/cross-checks/suspicious"
BACKFILL = "/api/v1/cross-checks/backfill"
NOW = datetime.now(UTC)


async def _seed_score(db: AsyncSession, *, total: float = 70) -> tuple[Score, JD]:
    code = f"XC_{uuid4().hex[:6]}"
    jd = JD(code=code, name=code, description="", status="active")
    db.add(jd)
    await db.flush()
    version = RuleVersion(
        jd_id=jd.id, version="v1", published_at=NOW, schema_json={"jd_code": code}
    )
    db.add(version)
    await db.flush()
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
        total_score=total,
        grade="L1",
        hard_filter_result={},
        rule_dimensions={"subtotal": 60},
        judge_dimensions={"dimensions": []},
    )
    db.add(score)
    await db.flush()
    return score, jd


async def _seed_check(
    db: AsyncSession,
    score: Score,
    *,
    diff: Decimal,
    threshold: Decimal = Decimal("10"),
    state: str = "completed",
    reasons: list[str] | None = None,
    prompt: str = "resume_judge_v1",
) -> ScoreCrossCheck:
    row = ScoreCrossCheck(
        score_id=score.id,
        secondary_model="test-secondary",
        prompt_version=prompt,
        sample_reasons=reasons or ["deterministic_sample"],
        state=state,
        attempts=1,
        threshold_snapshot=threshold,
        secondary_total_score=Decimal(str(score.total_score)) - diff,
        secondary_dimensions=[
            {"id": "independence", "tier": "low", "score": 1, "confidence": 0.5}
        ],
        absolute_diff=diff,
        completed_at=NOW - timedelta(hours=1) if state == "completed" else None,
    )
    db.add(row)
    await db.flush()
    return row


async def test_suspicious_requires_authentication_and_allows_read_roles(
    client, db_session, auth_headers
):
    assert (await client.get(SUSPICIOUS)).status_code == 401
    for role in ("hr", "hr_lead", "admin"):
        assert (
            await client.get(SUSPICIOUS, headers=await auth_headers(role))
        ).status_code == 200


async def test_only_current_completed_over_threshold_rows_are_listed(
    client, db_session, auth_headers
):
    over, _jd = await _seed_score(db_session)
    await _seed_check(db_session, over, diff=Decimal("30"))

    under, _jd2 = await _seed_score(db_session)
    await _seed_check(db_session, under, diff=Decimal("2"))

    unfinished, _jd3 = await _seed_score(db_session)
    await _seed_check(db_session, unfinished, diff=Decimal("40"), state="queued")

    # A superseded configuration must not speak for its score.
    superseded, _jd4 = await _seed_score(db_session)
    await _seed_check(db_session, superseded, diff=Decimal("50"))
    await _seed_check(
        db_session, superseded, diff=Decimal("1"), prompt="resume_judge_v2"
    )
    await db_session.commit()

    body = (await client.get(SUSPICIOUS, headers=await auth_headers("hr"))).json()

    assert [item["score_id"] for item in body["items"]] == [over.id]
    assert body["total"] == 1


async def test_filters_narrow_the_suspicious_list(client, db_session, auth_headers):
    score, jd = await _seed_score(db_session)
    await _seed_check(
        db_session, score, diff=Decimal("30"), reasons=["golden_error"]
    )
    other, _jd = await _seed_score(db_session)
    await _seed_check(db_session, other, diff=Decimal("15"))
    await db_session.commit()

    headers = await auth_headers("hr")
    for query, expected in (
        (f"jd_code={jd.code}", 1),
        ("min_diff=20", 1),
        ("reason=golden_error", 1),
        ("min_diff=1", 2),
    ):
        body = (await client.get(f"{SUSPICIOUS}?{query}", headers=headers)).json()
        assert body["total"] == expected, query


async def test_suspicious_body_carries_no_candidate_content(
    client, db_session, auth_headers
):
    score, _jd = await _seed_score(db_session)
    await _seed_check(db_session, score, diff=Decimal("30"))
    await db_session.commit()

    raw = (await client.get(SUSPICIOUS, headers=await auth_headers("hr"))).text

    for forbidden in ("private-name", "name_cipher", "evidence_quotes", "reasoning"):
        assert forbidden not in raw


async def test_backfill_is_admin_only(client, db_session, auth_headers):
    assert (await client.post(f"{BACKFILL}?limit=10")).status_code == 401
    for role in ("hr", "hr_lead"):
        assert (
            await client.post(f"{BACKFILL}?limit=10", headers=await auth_headers(role))
        ).status_code == 403
    assert (
        await client.post(f"{BACKFILL}?limit=10", headers=await auth_headers("admin"))
    ).status_code == 200


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("limit=0", "invalid_cross_check_limit"),
        ("limit=100000", "invalid_cross_check_limit"),
        ("limit=10&from=2026-07-01T00:00:00", "invalid_cross_check_window"),
        (
            "limit=10&from=2026-01-01T00:00:00%2B00:00&to=2026-07-01T00:00:00%2B00:00",
            "cross_check_window_too_large",
        ),
    ],
)
async def test_backfill_validation(client, db_session, auth_headers, query, code):
    response = await client.post(
        f"{BACKFILL}?{query}", headers=await auth_headers("admin")
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == code


async def test_dry_run_then_confirm_is_consistent_and_idempotent(
    client, db_session, auth_headers
):
    headers = await auth_headers("admin")
    for _ in range(3):
        score, _jd = await _seed_score(db_session)
        score.created_at = NOW - timedelta(hours=1)
    await db_session.commit()

    dry = (
        await client.post(f"{BACKFILL}?limit=50&dry_run=true", headers=headers)
    ).json()
    assert dry["dry_run"] is True
    assert dry["newly_queued"] == 0
    assert (
        await db_session.execute(select(func.count()).select_from(ScoreCrossCheck))
    ).scalar_one() == 0

    confirmed = (
        await client.post(f"{BACKFILL}?limit=50&dry_run=false", headers=headers)
    ).json()

    assert confirmed["selected"] == dry["selected"]
    assert confirmed["already_existing"] == dry["already_existing"]
    assert confirmed["newly_queued"] == dry["would_queue"]

    # Running it again queues nothing new.
    again = (
        await client.post(f"{BACKFILL}?limit=50&dry_run=false", headers=headers)
    ).json()
    assert again["newly_queued"] == 0
    assert again["already_existing"] == confirmed["newly_queued"]
