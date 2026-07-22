from datetime import datetime, timezone

import pytest

from backend.app.models import JD, RuleVersion

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
