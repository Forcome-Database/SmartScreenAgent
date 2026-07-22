# backend/tests/integration/test_golden_set_api.py
import pytest

from backend.app.models import JD, Candidate
from backend.app.security.crypto import encrypt_pii

pytestmark = pytest.mark.integration


async def _seed_candidate(db, *, name="张三", pii_hash="h1"):
    cand = Candidate(
        source="upload", name_cipher=encrypt_pii(name), pii_hash=pii_hash, extracted_json={}
    )
    db.add(cand)
    await db.flush()
    return cand


async def _seed_jd(db, *, code="FT"):
    jd = JD(code=code, name="Foreign Trade", description="", status="active")
    db.add(jd)
    await db.flush()
    return jd


def _csv_bytes(*rows: str) -> bytes:
    return ("candidate_id,jd_code,label\n" + "\n".join(rows) + "\n").encode("utf-8")


async def test_import_creates_then_updates_one_row(client, db_session, auth_headers):
    cand = await _seed_candidate(db_session)
    await _seed_jd(db_session)
    await db_session.commit()
    headers = await auth_headers("hr_lead")
    r1 = await client.post(
        "/api/v1/golden-set/import",
        files={"file": ("g.csv", _csv_bytes(f"{cand.id},FT,advance"), "text/csv")},
        headers=headers,
    )
    assert r1.status_code == 200 and r1.json()["created"] == 1 and r1.json()["updated"] == 0
    r2 = await client.post(
        "/api/v1/golden-set/import",
        files={"file": ("g.csv", _csv_bytes(f"{cand.id},FT,reject"), "text/csv")},
        headers=headers,
    )
    assert r2.status_code == 200 and r2.json()["created"] == 0 and r2.json()["updated"] == 1


async def test_import_row_errors_and_auth(client, db_session, auth_headers):
    cand = await _seed_candidate(db_session)
    await _seed_jd(db_session)
    await db_session.commit()
    body = _csv_bytes(f"{cand.id},NOPE,advance", "999999,FT,advance", f"{cand.id},FT,advance")
    r = await client.post(
        "/api/v1/golden-set/import",
        files={"file": ("g.csv", body, "text/csv")},
        headers=await auth_headers("admin"),
    )
    assert r.status_code == 200
    reasons = {(e["row"], e["reason"]) for e in r.json()["errors"]}
    assert reasons == {(1, "unknown_jd_code"), (2, "unknown_candidate")}
    assert r.json()["created"] == 1 and r.json()["total"] == 3
    # plain hr may not import
    forbidden = await client.post(
        "/api/v1/golden-set/import",
        files={"file": ("g.csv", _csv_bytes(f"{cand.id},FT,advance"), "text/csv")},
        headers=await auth_headers("hr"),
    )
    assert forbidden.status_code == 403
    noauth = await client.post(
        "/api/v1/golden-set/import",
        files={"file": ("g.csv", _csv_bytes(f"{cand.id},FT,advance"), "text/csv")},
    )
    assert noauth.status_code == 401


async def test_invalid_csv_returns_422(client, db_session, auth_headers):
    r = await client.post(
        "/api/v1/golden-set/import",
        files={"file": ("g.csv", b"a,b,c\n1,2,3\n", "text/csv")},
        headers=await auth_headers("admin"),
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_csv"


async def test_list_golden_set(client, db_session, auth_headers):
    cand = await _seed_candidate(db_session)
    await _seed_jd(db_session)
    await db_session.commit()
    await client.post(
        "/api/v1/golden-set/import",
        files={"file": ("g.csv", _csv_bytes(f"{cand.id},FT,advance"), "text/csv")},
        headers=await auth_headers("hr_lead"),
    )
    lst = await client.get("/api/v1/golden-set", headers=await auth_headers("hr"))
    assert lst.status_code == 200
    body = lst.json()
    assert body["total"] == 1 and body["items"][0]["label"] == "advance"
    assert body["items"][0]["jd_code"] == "FT" and "name_cipher" not in lst.text
