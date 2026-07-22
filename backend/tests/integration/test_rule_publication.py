from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from backend.app.models import JD, Candidate, GoldenSet, RuleVersion, Score, User
from backend.app.security.crypto import encrypt_pii

pytestmark = pytest.mark.integration


def _schema(version: str) -> dict:
    return {
        "version": version,
        "jd_code": "FT",
        "total_score": 10.0,
        "passing_threshold": 6.0,
        "hard_filters": [],
        "rule_dimensions": [
            {
                "id": "exp",
                "name": "experience",
                "weight": 4.0,
                "method": "experience_years",
                "tiers": [{"label": "high", "score": 4.0, "min_years": 0.0}],
            }
        ],
        "judge_dimensions": [
            {
                "id": "fit",
                "name": "fit",
                "weight": 6.0,
                "prompt_hint": "fit",
                "tiers": [{"label": "high", "score": 6.0}],
            }
        ],
        "grade_thresholds": [{"grade": "L1", "min": 6.0, "label": "pass"}],
    }


async def _seed_jd_with_active(db) -> JD:
    jd = JD(code="FT", name="Foreign Trade", description="", status="active")
    db.add(jd)
    await db.flush()
    active = RuleVersion(
        jd_id=jd.id,
        version="v1",
        schema_json=_schema("v1"),
        status="published",
        published_at=datetime.now(timezone.utc),
    )
    db.add(active)
    await db.flush()
    jd.active_rule_version_id = active.id
    await db.commit()
    return jd


async def test_create_draft_validates_and_dedupes(client, db_session, auth_headers) -> None:
    await _seed_jd_with_active(db_session)
    base = "/api/v1/jds/FT/rule-versions"
    lead_headers = await auth_headers("hr_lead")

    created = await client.post(
        base,
        json={"schema_json": _schema("v2")},
        headers=lead_headers,
    )
    assert created.status_code == 200
    assert created.json()["status"] == "draft"

    duplicate = await client.post(
        base,
        json={"schema_json": _schema("v2")},
        headers=await auth_headers("admin"),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "version_exists"

    invalid = await client.post(
        base,
        json={"schema_json": {"version": "v3"}},
        headers=await auth_headers("admin"),
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_rule_schema"

    forbidden = await client.post(
        base,
        json={"schema_json": _schema("v4")},
        headers=await auth_headers("hr"),
    )
    assert forbidden.status_code == 403
    assert (await client.post(base, json={"schema_json": _schema("v5")})).status_code == 401


async def test_publish_requires_recorded_metrics(client, db_session, auth_headers) -> None:
    await _seed_jd_with_active(db_session)
    lead_headers = await auth_headers("hr_lead")
    await client.post(
        "/api/v1/jds/FT/rule-versions",
        json={"schema_json": _schema("v2")},
        headers=lead_headers,
    )

    early = await client.post(
        "/api/v1/jds/FT/rule-versions/v2/publish",
        headers=lead_headers,
    )
    assert early.status_code == 409
    assert early.json()["detail"]["code"] == "regression_not_recorded"


async def test_list_includes_status(client, db_session, auth_headers) -> None:
    await _seed_jd_with_active(db_session)
    await client.post(
        "/api/v1/jds/FT/rule-versions",
        json={"schema_json": _schema("v2")},
        headers=await auth_headers("hr_lead"),
    )

    response = await client.get(
        "/api/v1/jds/FT/rule-versions",
        headers=await auth_headers("hr"),
    )
    assert response.status_code == 200
    statuses = {item["version"]: item["status"] for item in response.json()["items"]}
    assert statuses == {"v1": "published", "v2": "draft"}


async def _seed_scored_golden(
    db,
    jd: JD,
    *,
    label: str,
    extracted: dict,
    total: float,
    rule_subtotal: float,
    judge_dimensions: dict | None,
    pii_hash: str,
    importer_id: int,
) -> Candidate:
    candidate = Candidate(
        source="upload",
        name_cipher=encrypt_pii("张三"),
        pii_hash=pii_hash,
        extracted_json=extracted,
    )
    db.add(candidate)
    await db.flush()
    score = Score(
        candidate_id=candidate.id,
        jd_id=jd.id,
        rule_version_id=jd.active_rule_version_id,
        total_score=total,
        grade="rejected" if judge_dimensions is None else "L1",
        hard_filter_result={},
        rule_dimensions={"subtotal": rule_subtotal},
        judge_dimensions=judge_dimensions,
        is_suspicious=False,
    )
    db.add(score)
    await db.flush()
    db.add(
        GoldenSet(
            candidate_id=candidate.id,
            jd_id=jd.id,
            label=label,
            imported_at=datetime.now(timezone.utc),
            imported_by_user_id=importer_id,
        )
    )
    await db.flush()
    return candidate


async def test_evaluate_then_publish_switches_active(
    client,
    db_session,
    auth_headers,
) -> None:
    jd = await _seed_jd_with_active(db_session)
    lead_headers = await auth_headers("hr_lead")
    importer = (
        await db_session.execute(select(User).where(User.role == "hr_lead"))
    ).scalar_one()

    await _seed_scored_golden(
        db_session,
        jd,
        label="advance",
        extracted={"experiences": [{"start": "2019-01", "end": "2024-01"}]},
        total=10,
        rule_subtotal=4,
        judge_dimensions={"dimensions": []},
        pii_hash="c1",
        importer_id=importer.id,
    )
    await _seed_scored_golden(
        db_session,
        jd,
        label="reject",
        extracted={"experiences": [{"start": "2019-01", "end": "2024-01"}]},
        total=0,
        rule_subtotal=0,
        judge_dimensions=None,
        pii_hash="c2",
        importer_id=importer.id,
    )
    borderline = await _seed_scored_golden(
        db_session,
        jd,
        label="borderline",
        extracted={},
        total=0,
        rule_subtotal=0,
        judge_dimensions=None,
        pii_hash="c3",
        importer_id=importer.id,
    )
    uncovered = Candidate(
        source="upload",
        name_cipher=encrypt_pii("李四"),
        pii_hash="c4",
        extracted_json={},
    )
    db_session.add(uncovered)
    await db_session.flush()
    db_session.add(
        GoldenSet(
            candidate_id=uncovered.id,
            jd_id=jd.id,
            label="reject",
            imported_at=datetime.now(timezone.utc),
            imported_by_user_id=importer.id,
        )
    )
    await db_session.commit()

    draft_schema = _schema("v2")
    draft_schema["judge_dimensions"][0]["prompt_hint"] = "changed fit"
    created = await client.post(
        "/api/v1/jds/FT/rule-versions",
        json={"schema_json": draft_schema},
        headers=lead_headers,
    )
    assert created.status_code == 200

    evaluate_url = "/api/v1/jds/FT/rule-versions/v2/evaluate"
    forbidden = await client.post(evaluate_url, headers=await auth_headers("hr"))
    assert forbidden.status_code == 403
    evaluated = await client.post(evaluate_url, headers=lead_headers)
    assert evaluated.status_code == 200
    body = evaluated.json()
    assert body["draft"]["confusion"] == {"tp": 1, "fp": 0, "tn": 0, "fn": 0}
    assert body["draft"]["evaluated"] == 1
    assert body["draft"]["indeterminate"] == 1
    assert body["draft"]["borderline_excluded"] == 1
    assert body["draft"]["uncovered"] == 1
    assert body["judge_dimensions_changed"] is True
    assert body["baseline"] is not None
    assert "name_cipher" not in evaluated.text
    assert "张三" not in evaluated.text
    assert "李四" not in evaluated.text
    assert str(borderline.id) not in evaluated.text

    published = await client.post(
        "/api/v1/jds/FT/rule-versions/v2/publish",
        headers=await auth_headers("admin"),
    )
    assert published.status_code == 200
    assert published.json()["status"] == "published"

    db_session.expire_all()
    jd_row = (await db_session.execute(select(JD).where(JD.code == "FT"))).scalar_one()
    v2 = (
        await db_session.execute(
            select(RuleVersion).where(
                RuleVersion.jd_id == jd_row.id,
                RuleVersion.version == "v2",
            )
        )
    ).scalar_one()
    v1 = (
        await db_session.execute(
            select(RuleVersion).where(
                RuleVersion.jd_id == jd_row.id,
                RuleVersion.version == "v1",
            )
        )
    ).scalar_one()
    assert jd_row.active_rule_version_id == v2.id
    assert v2.published_by_user_id is not None
    assert v1.status == "archived"

    not_a_draft = await client.post(evaluate_url, headers=lead_headers)
    assert not_a_draft.status_code == 409
    assert not_a_draft.json()["detail"]["code"] == "not_a_draft"
