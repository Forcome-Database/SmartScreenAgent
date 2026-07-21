from datetime import datetime, timezone

import pytest

from backend.app.models import JD, RuleVersion

pytestmark = pytest.mark.integration


async def _seed_two_versions(db):
    jd = JD(code="QC", name="Quality", description="d", status="active")
    db.add(jd)
    await db.flush()
    v1 = RuleVersion(
        jd_id=jd.id,
        version="v1",
        published_at=datetime.now(timezone.utc),
        schema_json={"passing_threshold": 40, "rule_dimensions": [{"id": "a", "weight": 10}]},
    )
    v2 = RuleVersion(
        jd_id=jd.id,
        version="v2",
        published_at=datetime.now(timezone.utc),
        schema_json={"passing_threshold": 50, "rule_dimensions": [{"id": "a", "weight": 20}]},
    )
    db.add_all([v1, v2])
    await db.flush()
    jd.active_rule_version_id = v2.id
    await db.commit()
    return jd


async def test_jd_list_and_detail(client, db_session, auth_headers):
    jd = await _seed_two_versions(db_session)
    lst = await client.get("/api/v1/jds", headers=await auth_headers("hr"))
    assert lst.status_code == 200 and any(i["code"] == "QC" for i in lst.json()["items"])
    detail = await client.get(f"/api/v1/jds/{jd.code}", headers=await auth_headers("hr"))
    assert detail.status_code == 200 and detail.json()["active_rule_version"]["version"] == "v2"


async def test_rule_versions_and_diff(client, db_session, auth_headers):
    jd = await _seed_two_versions(db_session)
    versions = await client.get(
        f"/api/v1/jds/{jd.code}/rule-versions", headers=await auth_headers("hr")
    )
    assert versions.status_code == 200 and versions.json()["total"] == 2
    diff = await client.get(
        f"/api/v1/jds/{jd.code}/rule-versions/v1/diff/v2", headers=await auth_headers("hr")
    )
    assert diff.status_code == 200
    paths = {(c["path"], c["kind"]) for c in diff.json()["changes"]}
    assert ("passing_threshold", "changed") in paths
    assert ("rule_dimensions[a]", "changed") in paths


async def test_unknown_jd_404(client, auth_headers):
    resp = await client.get("/api/v1/jds/NOPE", headers=await auth_headers("hr"))
    assert resp.status_code == 404
