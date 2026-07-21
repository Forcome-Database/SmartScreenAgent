from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from backend.app.models import JD, AuditLog, Candidate, RuleVersion, Score
from backend.app.security.crypto import encrypt_pii
from backend.app.services.storage import StorageError

pytestmark = pytest.mark.integration


async def _seed(db, *, raw_file_key: str | None = None):
    jd = JD(code="FT", name="Foreign Trade", description="", status="active")
    db.add(jd)
    await db.flush()
    rv = RuleVersion(
        jd_id=jd.id, version="v1", schema_json={}, published_at=datetime.now(timezone.utc)
    )
    db.add(rv)
    await db.flush()
    jd.active_rule_version_id = rv.id
    cand = Candidate(
        source="upload",
        name_cipher=encrypt_pii("张三"),
        phone_cipher=encrypt_pii("13800000000"),
        pii_hash="h1",
        extracted_json={"age": 30, "education": "本科", "experiences": []},
        raw_file_key=raw_file_key,
    )
    db.add(cand)
    await db.flush()
    score = Score(
        candidate_id=cand.id,
        jd_id=jd.id,
        rule_version_id=rv.id,
        total_score=80,
        grade="L4",
        hard_filter_result={"passed": True},
        rule_dimensions={},
        is_suspicious=False,
    )
    db.add(score)
    await db.commit()
    return jd, cand, score


async def test_ranked_list_no_pii_no_audit(client, db_session, auth_headers):
    jd, cand, score = await _seed(db_session)
    before = (await db_session.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    resp = await client.get(f"/api/v1/jds/{jd.code}/candidates", headers=await auth_headers("hr"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["candidate_id"] == cand.id
    assert "name" not in body["items"][0] and "张三" not in resp.text
    after = (await db_session.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    assert after == before  # list writes no audit


async def test_ranked_list_unknown_jd_returns_404(client, db_session, auth_headers):
    resp = await client.get(
        "/api/v1/jds/NOPE/candidates", headers=await auth_headers("hr")
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == {"code": "not_found", "message": "JD not found"}


async def test_candidate_list_no_pii_no_audit(client, db_session, auth_headers):
    jd, cand, score = await _seed(db_session)
    before = (await db_session.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    resp = await client.get("/api/v1/candidates", headers=await auth_headers("hr"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["candidate_id"] == cand.id
    assert "name" not in body["items"][0] and "张三" not in resp.text
    after = (await db_session.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    assert after == before


async def test_detail_decrypts_and_writes_one_audit(client, db_session, auth_headers):
    jd, cand, score = await _seed(db_session)
    before = (await db_session.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    resp = await client.get(f"/api/v1/candidates/{cand.id}", headers=await auth_headers("hr"))
    assert resp.status_code == 200
    assert resp.json()["name"] == "张三"
    after = (
        await db_session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.event_type == "pii_decrypt")
        )
    ).scalar_one()
    assert after == before + 1


async def test_detail_unknown_candidate_returns_404(client, db_session, auth_headers):
    resp = await client.get("/api/v1/candidates/999999", headers=await auth_headers("hr"))
    assert resp.status_code == 404
    assert resp.json()["detail"] == {"code": "not_found", "message": "candidate not found"}


async def test_score_detail_and_unknown(client, db_session, auth_headers):
    jd, cand, score = await _seed(db_session)
    resp = await client.get(
        f"/api/v1/candidates/{cand.id}/scores/{score.id}", headers=await auth_headers("hr")
    )
    assert resp.status_code == 200 and resp.json()["grade"] == "L4"
    missing = await client.get(
        f"/api/v1/candidates/{cand.id}/scores/999999", headers=await auth_headers("hr")
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == {"code": "not_found", "message": "score not found"}


async def test_raw_file_returns_presigned_url_and_writes_one_audit(
    client, db_session, auth_headers, monkeypatch
):
    jd, cand, score = await _seed(db_session, raw_file_key="resumes/2024/01/abc")
    before = (
        await db_session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.event_type == "raw_file_access")
        )
    ).scalar_one()

    class FakeStorage:
        async def presigned_get_url(self, key: str, *, expires_seconds: int) -> str:
            assert key == "resumes/2024/01/abc"
            return f"https://minio.local/{key}?ttl={expires_seconds}"

    monkeypatch.setattr(
        "backend.app.routers.candidates_read.ResumeStorageService", FakeStorage
    )
    resp = await client.get(
        f"/api/v1/candidates/{cand.id}/raw-file", headers=await auth_headers("hr")
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["url"] == "https://minio.local/resumes/2024/01/abc?ttl=300"
    assert body["expires_in_seconds"] == 300
    after = (
        await db_session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.event_type == "raw_file_access")
        )
    ).scalar_one()
    assert after == before + 1


async def test_raw_file_storage_unavailable_returns_503(
    client, db_session, auth_headers, monkeypatch
):
    jd, cand, score = await _seed(db_session, raw_file_key="resumes/2024/01/abc")

    class FailingStorage:
        async def presigned_get_url(self, key: str, *, expires_seconds: int) -> str:
            raise StorageError(operation="presign", key=key)

    monkeypatch.setattr(
        "backend.app.routers.candidates_read.ResumeStorageService", FailingStorage
    )
    resp = await client.get(
        f"/api/v1/candidates/{cand.id}/raw-file", headers=await auth_headers("hr")
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == {
        "code": "object_storage_unavailable",
        "message": "Resume storage is unavailable",
    }


async def test_raw_file_unknown_candidate_returns_404(client, db_session, auth_headers):
    jd, cand, score = await _seed(db_session)  # no raw_file_key
    resp = await client.get(
        f"/api/v1/candidates/{cand.id}/raw-file", headers=await auth_headers("hr")
    )
    assert resp.status_code == 404

    missing = await client.get(
        "/api/v1/candidates/999999/raw-file", headers=await auth_headers("hr")
    )
    assert missing.status_code == 404


async def test_read_requires_auth(client):
    resp = await client.get("/api/v1/candidates")
    assert resp.status_code == 401
