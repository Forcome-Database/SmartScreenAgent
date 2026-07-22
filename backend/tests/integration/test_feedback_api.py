# backend/tests/integration/test_feedback_api.py
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from backend.app.models import JD, Candidate, Feedback, RuleVersion, Score
from backend.app.security.crypto import encrypt_pii

pytestmark = pytest.mark.integration


async def _seed(db, grade="L4", *, jd_code="FT", pii_hash="h1"):
    jd = JD(code=jd_code, name="Foreign Trade", description="", status="active")
    db.add(jd)
    await db.flush()
    rv = RuleVersion(
        jd_id=jd.id, version="v1", schema_json={}, published_at=datetime.now(timezone.utc)
    )
    db.add(rv)
    await db.flush()
    cand = Candidate(
        source="upload", name_cipher=encrypt_pii("张三"), pii_hash=pii_hash, extracted_json={}
    )
    db.add(cand)
    await db.flush()
    score = Score(candidate_id=cand.id, jd_id=jd.id, rule_version_id=rv.id, total_score=80,
                  grade=grade, hard_filter_result={}, rule_dimensions={}, is_suspicious=False)
    db.add(score)
    await db.commit()
    return cand, score


async def test_upsert_creates_then_updates_one_row(client, db_session, auth_headers):
    cand, score = await _seed(db_session, grade="L4")
    base = f"/api/v1/candidates/{cand.id}/scores/{score.id}/feedback"
    # auth_headers() mints a brand-new User row on every call, so the same
    # reviewer must reuse one set of headers across both requests — calling
    # it twice would create two distinct reviewers and defeat the upsert.
    headers = await auth_headers("hr")
    r1 = await client.put(base, json={"decision": "advance"}, headers=headers)
    assert r1.status_code == 200 and r1.json()["ai_agreed"] is True
    r2 = await client.put(base, json={"decision": "reject", "reason": "经验不符"}, headers=headers)
    assert r2.status_code == 200 and r2.json()["ai_agreed"] is False
    rows = await db_session.execute(select(Feedback).where(Feedback.score_id == score.id))
    assert len(rows.all()) == 1  # same reviewer upserts


async def test_disagreement_requires_reason(client, db_session, auth_headers):
    cand, score = await _seed(db_session, grade="L4")  # AI advance
    r = await client.put(
        f"/api/v1/candidates/{cand.id}/scores/{score.id}/feedback",
        json={"decision": "reject"},
        headers=await auth_headers("hr"),
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "feedback_reason_required"


async def test_disagreement_with_whitespace_only_reason_requires_reason(
    client, db_session, auth_headers
):
    cand, score = await _seed(db_session, grade="L4")  # AI advance
    r = await client.put(
        f"/api/v1/candidates/{cand.id}/scores/{score.id}/feedback",
        json={"decision": "reject", "reason": "   "},
        headers=await auth_headers("hr"),
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "feedback_reason_required"


async def test_list_and_auth(client, db_session, auth_headers):
    cand, score = await _seed(db_session)
    feedback_url = f"/api/v1/candidates/{cand.id}/scores/{score.id}/feedback"
    await client.put(
        feedback_url, json={"decision": "hold"}, headers=await auth_headers("hr")
    )
    lst = await client.get(feedback_url, headers=await auth_headers("hr"))
    assert lst.status_code == 200 and lst.json()[0]["decision"] == "hold"
    assert lst.json()[0]["ai_agreed"] is None
    noauth = await client.put(feedback_url, json={"decision": "hold"})
    assert noauth.status_code == 401


async def test_score_not_owned_by_candidate_or_unknown_returns_404(
    client, db_session, auth_headers
):
    cand_a, score_a = await _seed(db_session, jd_code="FT", pii_hash="h1")
    cand_b, score_b = await _seed(db_session, jd_code="FT2", pii_hash="h2")

    # candidate A's URL with candidate B's score_id: mismatched ownership.
    mismatched = await client.put(
        f"/api/v1/candidates/{cand_a.id}/scores/{score_b.id}/feedback",
        json={"decision": "hold"},
        headers=await auth_headers("hr"),
    )
    assert mismatched.status_code == 404
    assert mismatched.json()["detail"]["code"] == "not_found"

    # candidate A's URL with a score_id that doesn't exist at all.
    unknown_put = await client.put(
        f"/api/v1/candidates/{cand_a.id}/scores/999999/feedback",
        json={"decision": "hold"},
        headers=await auth_headers("hr"),
    )
    assert unknown_put.status_code == 404
    assert unknown_put.json()["detail"]["code"] == "not_found"

    unknown_get = await client.get(
        f"/api/v1/candidates/{cand_a.id}/scores/999999/feedback",
        headers=await auth_headers("hr"),
    )
    assert unknown_get.status_code == 404
    assert unknown_get.json()["detail"]["code"] == "not_found"

    # score_a itself is untouched/reachable — confirms the 404s above were
    # about candidate/score ownership, not e.g. a broken route.
    still_ok = await client.get(
        f"/api/v1/candidates/{cand_a.id}/scores/{score_a.id}/feedback",
        headers=await auth_headers("hr"),
    )
    assert still_ok.status_code == 200
