from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from backend.app.models import JD, AuditLog, Candidate, IngestionJob, RuleVersion, Score
from backend.app.scoring.llm_judge import JudgeResult
from backend.app.security.crypto import encrypt_pii
from backend.app.services.llm.errors import LLMInvalidOutputError, LLMUnavailableError
from backend.app.services.parser.pii import compute_pii_hash
from backend.app.services.storage import StorageError


@pytest.mark.integration
@pytest.mark.asyncio
async def test_service_identity_remains_public(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "smartscreen-agent"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_returns_candidate_id(
    client,
    db_session,
    auth_headers,
    minio_storage,
    valid_pdf_bytes,
    monkeypatch,
):
    """Upload endpoint validates, stores, and enqueues — returns 202 with job_id.

    Parsing/extraction/scoring now happen in the Celery worker
    (see `backend/tests/integration/test_tasks_ingest.py`), not here.
    """
    enqueued: list[int] = []
    monkeypatch.setattr(
        "backend.app.routers.candidates.enqueue_job", lambda job_id: enqueued.append(job_id)
    )

    files = {"file": ("r.pdf", valid_pdf_bytes, "application/pdf")}
    resp = await client.post(
        "/api/v1/candidates/upload", files=files, headers=await auth_headers()
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["state"] == "queued"
    assert body["job_id"] is not None
    assert body["batch_id"] is None
    assert enqueued == [body["job_id"]]

    job = (
        await db_session.execute(
            select(IngestionJob).where(IngestionJob.id == body["job_id"])
        )
    ).scalar_one()
    assert job.state == "queued"
    assert job.raw_file_key is not None
    assert job.raw_file_sha256 is not None
    assert job.raw_file_size_bytes == len(valid_pdf_bytes)
    assert job.raw_file_content_type == "application/pdf"
    assert job.raw_file_original_name_cipher is not None
    stat = minio_storage.stat_object(job.raw_file_key)
    assert stat.size == len(valid_pdf_bytes)
    assert stat.metadata["x-amz-meta-sha256"] == job.raw_file_sha256


@pytest.mark.integration
@pytest.mark.asyncio
async def test_score_endpoint_returns_total(client, db_session, auth_headers, monkeypatch):
    """Score endpoint: given existing candidate + JD with active rule, returns total + grade."""
    import json
    from datetime import datetime, timezone

    rule_data = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "sample_rule_v1.json").read_text(encoding="utf-8")
    )
    jd = JD(code="FOREIGN_TRADE", name="外贸业务", description="", status="active")
    db_session.add(jd)
    await db_session.flush()
    rv = RuleVersion(
        jd_id=jd.id,
        version="v1",
        schema_json=rule_data,
        published_at=datetime.now(tz=timezone.utc),
        notes="test",
    )
    db_session.add(rv)
    await db_session.flush()
    jd.active_rule_version_id = rv.id

    from backend.app.services.parser.pii import compute_pii_hash, encrypt_pii

    cand = Candidate(
        source="upload",
        name_cipher=encrypt_pii("张三"),
        pii_hash=compute_pii_hash(name="张三", phone="13800001234"),
        parsed_markdown="独立负责北美 五金",
        extracted_json={
            "age": 30,
            "education": "本科",
            "experiences": [
                {
                    "title": "外贸",
                    "description": "北美 五金 全流程报关、订舱、单证",
                    "start": "2019-01",
                    "end": "2024-01",
                }
            ],
        },
    )
    db_session.add(cand)
    await db_session.commit()

    monkeypatch.setattr(
        "backend.app.scoring.pipeline.LLMJudge.score",
        AsyncMock(
            return_value=JudgeResult(
                dimensions=[],
                model="mock",
                tokens=0,
                prompt_version="resume_judge_v1",
            )
        ),
    )

    resp = await client.post(
        f"/api/v1/candidates/{cand.id}/score",
        json={"jd_code": "FOREIGN_TRADE"},
        headers=await auth_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "total_score" in body
    assert "grade" in body


@pytest.mark.integration
@pytest.mark.asyncio
async def test_score_endpoint_unknown_jd_returns_404(client, db_session, auth_headers):
    resp = await client.post(
        "/api/v1/candidates/999/score",
        json={"jd_code": "NONEXISTENT"},
        headers=await auth_headers(),
    )
    assert resp.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (
            LLMUnavailableError("provider URL contains secret-token"),
            503,
            {"code": "ai_service_unavailable", "message": "AI service is unavailable"},
        ),
        (
            LLMInvalidOutputError("completion contains private resume text"),
            502,
            {"code": "ai_invalid_output", "message": "AI service output is invalid"},
        ),
    ],
)
async def test_rescore_ai_failure_preserves_existing_score_and_rolls_back_partial_rows(
    client,
    db_session,
    auth_headers,
    monkeypatch,
    caplog,
    error,
    status_code,
    detail,
):
    from datetime import datetime, timezone

    jd = JD(code="ROLLBACK", name="Rollback", description="", status="active")
    db_session.add(jd)
    await db_session.flush()
    rule_version = RuleVersion(
        jd_id=jd.id,
        version="v1",
        schema_json={},
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(rule_version)
    await db_session.flush()
    jd.active_rule_version_id = rule_version.id
    candidate = Candidate(
        source="upload",
        name_cipher=encrypt_pii("Existing Candidate"),
        pii_hash=compute_pii_hash(name="Existing Candidate", phone="13900000000"),
        parsed_markdown="private resume text",
        extracted_json={"age": 30, "education": "bachelor", "experiences": []},
    )
    db_session.add(candidate)
    await db_session.flush()
    existing_score = Score(
        candidate_id=candidate.id,
        jd_id=jd.id,
        rule_version_id=rule_version.id,
        total_score=60,
        grade="B",
        hard_filter_result={"passed": True},
        rule_dimensions={},
        is_suspicious=False,
    )
    db_session.add(existing_score)
    await db_session.commit()
    existing_score_id = existing_score.id

    async def fail_after_partial_write(pipeline, *, candidate_id: int, jd_id: int):
        pipeline.db.add(
            AuditLog(
                event_type="score",
                actor="system",
                target_type="candidate",
                target_id=candidate_id,
                payload={"private": "must roll back"},
                rule_version_id=rule_version.id,
            )
        )
        await pipeline.db.flush()
        raise error

    monkeypatch.setattr(
        "backend.app.routers.candidates.ScoringPipeline.run",
        fail_after_partial_write,
    )

    response = await client.post(
        f"/api/v1/candidates/{candidate.id}/score",
        json={"jd_code": jd.code},
        headers=await auth_headers(),
    )

    assert response.status_code == status_code
    assert response.json()["detail"] == detail
    public_output = f"{response.text}\n{caplog.text}"
    assert "secret-token" not in public_output
    assert "private resume text" not in public_output
    candidate_ids = (await db_session.execute(select(Candidate.id))).scalars().all()
    assert candidate_ids == [candidate.id]
    scores = (await db_session.execute(select(Score))).scalars().all()
    assert [score.id for score in scores] == [existing_score_id]
    assert (await db_session.execute(select(AuditLog))).scalars().all() == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_requires_bearer_before_parser(client, monkeypatch):
    parser = AsyncMock()
    monkeypatch.setattr("backend.app.tasks.ingest.MinerUClient", parser)

    resp = await client.post(
        "/api/v1/candidates/upload",
        files={"file": ("r.pdf", b"%PDF-1.4 dummy", "application/pdf")},
    )

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Missing Bearer token"
    assert resp.headers["www-authenticate"] == "Bearer"
    parser.assert_not_called()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_rejects_invalid_token(client):
    resp = await client.post(
        "/api/v1/candidates/upload",
        files={"file": ("r.pdf", b"ignored", "application/pdf")},
        headers={"Authorization": "Bearer invalid"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid token"
    assert "signature" not in resp.text.lower()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["hr", "hr_lead", "admin"])
async def test_allowed_database_roles_reach_candidate_route(
    client, db_session, auth_headers, role
):
    resp = await client.post(
        "/api/v1/candidates/999/score",
        json={"jd_code": "NONEXISTENT"},
        headers=await auth_headers(role),
    )
    assert resp.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_role_overrides_token_role(client, db_session, auth_headers):
    resp = await client.post(
        "/api/v1/candidates/999/score",
        json={"jd_code": "NONEXISTENT"},
        headers=await auth_headers("dept_head", token_role="admin"),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Forbidden"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_rejects_unsupported_media_with_stable_shape(
    client, db_session, auth_headers
):
    resp = await client.post(
        "/api/v1/candidates/upload",
        files={"file": ("resume.exe", b"MZ", "application/octet-stream")},
        headers=await auth_headers(),
    )
    assert resp.status_code == 415
    assert resp.json()["detail"] == {
        "code": "unsupported_media_type",
        "message": "Unsupported resume file type",
    }


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "body", "content_type", "status", "code"),
    [
        ("resume.pdf", b"", "application/pdf", 400, "invalid_upload"),
        ("resume.pdf", b"%PDF-broken", "application/pdf", 422, "invalid_document"),
    ],
)
async def test_upload_maps_validation_errors(
    client,
    db_session,
    auth_headers,
    filename,
    body,
    content_type,
    status,
    code,
):
    response = await client.post(
        "/api/v1/candidates/upload",
        files={"file": (filename, body, content_type)},
        headers=await auth_headers(),
    )
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_maps_size_limit_to_413(
    client, db_session, auth_headers, monkeypatch
):
    from backend.app.services.upload.validation import UploadValidator as RealValidator

    monkeypatch.setattr(
        "backend.app.routers.candidates.UploadValidator",
        lambda: RealValidator(max_bytes=5, chunk_bytes=2),
    )
    response = await client.post(
        "/api/v1/candidates/upload",
        files={"file": ("resume.png", b"\x89PNG\r\n\x1a\nbody", "image/png")},
        headers=await auth_headers(),
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "file_too_large"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_maps_storage_failure_to_503(
    client, db_session, auth_headers, valid_pdf_bytes, monkeypatch
):
    storage = SimpleNamespace(
        store=AsyncMock(side_effect=StorageError(operation="put", key="opaque"))
    )
    monkeypatch.setattr(
        "backend.app.routers.candidates.ResumeStorageService", lambda: storage
    )
    resp = await client.post(
        "/api/v1/candidates/upload",
        files={"file": ("resume.pdf", valid_pdf_bytes, "application/pdf")},
        headers=await auth_headers(),
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == {
        "code": "object_storage_unavailable",
        "message": "Resume storage is unavailable",
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_duplicate_upload_is_explicit_and_leaves_one_object(
    client,
    db_session,
    auth_headers,
    minio_storage,
    valid_pdf_bytes,
    monkeypatch,
):
    """sha256 idempotency: resubmitting identical bytes reuses the same job,
    leaves exactly one MinIO object, and enqueues exactly once."""
    enqueued: list[int] = []
    monkeypatch.setattr(
        "backend.app.routers.candidates.enqueue_job", lambda job_id: enqueued.append(job_id)
    )
    headers = await auth_headers()
    request = {
        "files": {"file": ("resume.pdf", valid_pdf_bytes, "application/pdf")},
        "headers": headers,
    }

    first = await client.post("/api/v1/candidates/upload", **request)
    second = await client.post("/api/v1/candidates/upload", **request)

    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert first.json()["state"] == "queued"
    assert second.json()["job_id"] == first.json()["job_id"]
    assert second.json()["state"] == "queued"
    assert enqueued == [first.json()["job_id"]]

    jobs = (await db_session.execute(select(IngestionJob))).scalars().all()
    assert len(jobs) == 1
    assert minio_storage.list_object_keys(prefix="resumes/") == [jobs[0].raw_file_key]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_batch_records_per_file_outcomes(
    client,
    db_session,
    auth_headers,
    minio_storage,
    valid_pdf_bytes,
    monkeypatch,
):
    enqueued: list[int] = []
    monkeypatch.setattr(
        "backend.app.routers.candidates.enqueue_job", lambda job_id: enqueued.append(job_id)
    )
    files = [
        ("files", ("good.pdf", valid_pdf_bytes, "application/pdf")),
        ("files", ("bad.exe", b"MZ", "application/octet-stream")),
    ]
    resp = await client.post(
        "/api/v1/candidates/batch", files=files, headers=await auth_headers()
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["batch_id"]
    assert len(body["jobs"]) == 2

    queued = [result for result in body["jobs"] if result["state"] == "queued"]
    failed = [result for result in body["jobs"] if result["state"] == "terminal_failed"]
    assert len(queued) == 1
    assert len(failed) == 1
    assert queued[0]["job_id"] is not None
    assert queued[0]["error_code"] is None
    assert failed[0]["job_id"] is None
    assert failed[0]["error_code"] == "unsupported_media_type"
    assert enqueued == [queued[0]["job_id"]]

    jobs = (await db_session.execute(select(IngestionJob))).scalars().all()
    assert len(jobs) == 1
    assert jobs[0].id == queued[0]["job_id"]
    assert str(jobs[0].batch_id) == body["batch_id"]
    assert minio_storage.list_object_keys(prefix="resumes/") == [jobs[0].raw_file_key]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_batch_rejects_too_many_files(
    client, db_session, auth_headers, monkeypatch
):
    from backend.app.config import get_settings

    fake_settings = get_settings().model_copy(update={"INGESTION_BATCH_MAX_FILES": 1})
    monkeypatch.setattr(
        "backend.app.routers.candidates.get_settings", lambda: fake_settings
    )

    files = [
        ("files", ("a.pdf", b"%PDF-1.4 a", "application/pdf")),
        ("files", ("b.pdf", b"%PDF-1.4 b", "application/pdf")),
    ]
    resp = await client.post(
        "/api/v1/candidates/batch", files=files, headers=await auth_headers()
    )
    assert resp.status_code == 413
    assert resp.json()["detail"]["code"] == "batch_too_large"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_job_returns_status(
    client, db_session, auth_headers, minio_storage, valid_pdf_bytes, monkeypatch
):
    monkeypatch.setattr("backend.app.routers.candidates.enqueue_job", lambda job_id: None)
    resp = await client.post(
        "/api/v1/candidates/upload",
        files={"file": ("r.pdf", valid_pdf_bytes, "application/pdf")},
        headers=await auth_headers(),
    )
    job_id = resp.json()["job_id"]

    status_resp = await client.get(
        f"/api/v1/candidates/jobs/{job_id}", headers=await auth_headers()
    )
    assert status_resp.status_code == 200
    assert status_resp.json() == {
        "state": "queued",
        "attempts": 0,
        "last_error_code": None,
        "candidate_id": None,
        "score_id": None,
        "batch_id": None,
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_job_unknown_returns_404(client, db_session, auth_headers):
    resp = await client.get(
        "/api/v1/candidates/jobs/999999", headers=await auth_headers()
    )
    assert resp.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_job_requires_auth(client, db_session):
    resp = await client.get("/api/v1/candidates/jobs/1")
    assert resp.status_code == 401


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_batch_returns_counts(
    client, db_session, auth_headers, minio_storage, valid_pdf_bytes, monkeypatch
):
    monkeypatch.setattr("backend.app.routers.candidates.enqueue_job", lambda job_id: None)
    files = [
        ("files", ("a.pdf", valid_pdf_bytes, "application/pdf")),
        ("files", ("b.exe", b"MZ", "application/octet-stream")),
    ]
    resp = await client.post(
        "/api/v1/candidates/batch", files=files, headers=await auth_headers()
    )
    batch_id = resp.json()["batch_id"]

    status_resp = await client.get(
        f"/api/v1/candidates/batches/{batch_id}", headers=await auth_headers()
    )
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["total"] == 1
    assert body["by_state"] == {"queued": 1}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_batch_unknown_or_invalid_returns_404(client, db_session, auth_headers):
    from uuid import uuid4

    headers = await auth_headers()
    resp = await client.get(f"/api/v1/candidates/batches/{uuid4()}", headers=headers)
    assert resp.status_code == 404

    resp2 = await client.get("/api/v1/candidates/batches/not-a-uuid", headers=headers)
    assert resp2.status_code == 404
