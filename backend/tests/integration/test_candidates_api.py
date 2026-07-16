from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from backend.app.models import JD, AuditLog, Candidate, RuleVersion, Score
from backend.app.scoring.llm_judge import JudgeResult
from backend.app.security.crypto import encrypt_pii
from backend.app.services.llm.errors import LLMInvalidOutputError, LLMUnavailableError
from backend.app.services.parser.errors import (
    MinerUContractError,
    MinerUResultError,
    MinerUUnavailableError,
)
from backend.app.services.parser.extractor import Experience, ExtractedResume
from backend.app.services.parser.mineru_client import ParseResult
from backend.app.services.parser.pii import compute_pii_hash
from backend.app.services.storage import StorageError


def _mock_resume_pipeline(monkeypatch, *, name: str = "张三", phone: str = "13800001234"):
    monkeypatch.setattr(
        "backend.app.tasks.ingest.MinerUClient",
        lambda: SimpleNamespace(
            parse=AsyncMock(
                return_value=ParseResult(markdown=f"# r\n{name}", source="stub")
            )
        ),
    )
    monkeypatch.setattr(
        "backend.app.tasks.ingest.ResumeExtractor",
        lambda: SimpleNamespace(
            extract=AsyncMock(
                return_value=ExtractedResume(
                    name=name,
                    phone=phone,
                    email=None,
                    education="本科",
                    age=30,
                    experiences=[],
                )
            )
        ),
    )


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
    """Upload endpoint synchronously parses + extracts, returns 200 with candidate_id."""
    monkeypatch.setenv("MINERU_MODE", "stub")
    monkeypatch.setattr(
        "backend.app.tasks.ingest.MinerUClient",
        lambda: SimpleNamespace(
            parse=AsyncMock(
                return_value=ParseResult(markdown="# r\n张三", source="stub")
            )
        ),
    )
    monkeypatch.setattr(
        "backend.app.tasks.ingest.ResumeExtractor",
        lambda: SimpleNamespace(
            extract=AsyncMock(
                return_value=ExtractedResume(
                    name="张三",
                    phone="13800001234",
                    email=None,
                    education="本科",
                    age=30,
                    experiences=[
                        Experience(
                            company="X",
                            title="外贸",
                            description="北美 五金",
                            start="2020-01",
                            end="2024-01",
                        )
                    ],
                )
            )
        ),
    )
    files = {"file": ("r.pdf", valid_pdf_bytes, "application/pdf")}
    resp = await client.post(
        "/api/v1/candidates/upload", files=files, headers=await auth_headers()
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["candidate_id"] is not None
    assert body["status"] == "parsed"
    candidate = (
        await db_session.execute(
            select(Candidate).where(Candidate.id == body["candidate_id"])
        )
    ).scalar_one()
    assert candidate.raw_file_key is not None
    assert candidate.raw_file_sha256 is not None
    assert candidate.raw_file_size_bytes == len(valid_pdf_bytes)
    assert candidate.raw_file_content_type == "application/pdf"
    assert candidate.raw_file_original_name_cipher is not None
    stat = minio_storage.stat_object(candidate.raw_file_key)
    assert stat.size == len(valid_pdf_bytes)
    assert stat.metadata["x-amz-meta-sha256"] == candidate.raw_file_sha256


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_parser_failure_returns_502(
    client,
    db_session,
    auth_headers,
    minio_storage,
    valid_pdf_bytes,
    monkeypatch,
):
    monkeypatch.setattr(
        "backend.app.tasks.ingest.MinerUClient",
        lambda: SimpleNamespace(
            parse=AsyncMock(side_effect=MinerUResultError("missing markdown"))
        ),
    )
    resp = await client.post(
        "/api/v1/candidates/upload",
        files={"file": ("r.pdf", valid_pdf_bytes, "application/pdf")},
        headers=await auth_headers(),
    )
    assert resp.status_code == 502
    assert resp.json()["detail"] == {
        "code": "resume_parser_failed",
        "message": "Resume parser failed",
    }
    assert "missing markdown" not in resp.text
    assert "mineru.example.com" not in resp.text
    assert "r.pdf" not in resp.text
    assert minio_storage.list_object_keys(prefix="resumes/") == []


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (
            MinerUUnavailableError("https://secret@mineru.internal/tasks/42"),
            503,
            {
                "code": "resume_parser_unavailable",
                "message": "Resume parser is unavailable",
            },
        ),
        (
            MinerUContractError(
                "provider body contains candidate PII 13800000000 at C:/private/resume.pdf"
            ),
            502,
            {
                "code": "resume_parser_contract_invalid",
                "message": "Resume parser response is invalid",
            },
        ),
    ],
)
async def test_upload_maps_parser_boundary_errors_and_rolls_back(
    client,
    db_session,
    auth_headers,
    minio_storage,
    valid_pdf_bytes,
    monkeypatch,
    caplog,
    error,
    status_code,
    detail,
):
    monkeypatch.setattr(
        "backend.app.tasks.ingest.MinerUClient",
        lambda: SimpleNamespace(parse=AsyncMock(side_effect=error)),
    )

    response = await client.post(
        "/api/v1/candidates/upload",
        files={"file": ("private-resume.pdf", valid_pdf_bytes, "application/pdf")},
        headers=await auth_headers(),
    )

    assert response.status_code == status_code
    assert response.json()["detail"] == detail
    public_output = f"{response.text}\n{caplog.text}"
    for sentinel in (
        "secret@mineru.internal",
        "candidate PII",
        "13800000000",
        "C:/private/resume.pdf",
        "private-resume.pdf",
    ):
        assert sentinel not in public_output
    assert (await db_session.execute(select(Candidate))).scalars().all() == []
    assert minio_storage.list_object_keys(prefix="resumes/") == []


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (
            LLMUnavailableError("provider timeout with prompt body"),
            503,
            {"code": "ai_service_unavailable", "message": "AI service is unavailable"},
        ),
        (
            LLMInvalidOutputError("completion contains fabricated evidence"),
            502,
            {"code": "ai_invalid_output", "message": "AI service output is invalid"},
        ),
    ],
)
async def test_upload_maps_ai_boundary_errors_and_rolls_back(
    client,
    db_session,
    auth_headers,
    minio_storage,
    valid_pdf_bytes,
    monkeypatch,
    caplog,
    error,
    status_code,
    detail,
):
    monkeypatch.setattr(
        "backend.app.tasks.ingest.MinerUClient",
        lambda: SimpleNamespace(
            parse=AsyncMock(return_value=ParseResult(markdown="# private", source="stub"))
        ),
    )
    monkeypatch.setattr(
        "backend.app.tasks.ingest.ResumeExtractor",
        lambda: SimpleNamespace(extract=AsyncMock(side_effect=error)),
    )

    response = await client.post(
        "/api/v1/candidates/upload",
        files={"file": ("private-resume.pdf", valid_pdf_bytes, "application/pdf")},
        headers=await auth_headers(),
    )

    assert response.status_code == status_code
    assert response.json()["detail"] == detail
    public_output = f"{response.text}\n{caplog.text}"
    for sentinel in ("prompt body", "fabricated evidence", "private-resume.pdf"):
        assert sentinel not in public_output
    assert (await db_session.execute(select(Candidate))).scalars().all() == []
    assert minio_storage.list_object_keys(prefix="resumes/") == []


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
            Score(
                candidate_id=candidate_id,
                jd_id=jd_id,
                rule_version_id=rule_version.id,
                total_score=99,
                grade="A",
                hard_filter_result={"passed": True},
                rule_dimensions={},
                is_suspicious=False,
            )
        )
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
    _mock_resume_pipeline(monkeypatch)
    headers = await auth_headers()
    request = {
        "files": {"file": ("resume.pdf", valid_pdf_bytes, "application/pdf")},
        "headers": headers,
    }

    first = await client.post("/api/v1/candidates/upload", **request)
    second = await client.post("/api/v1/candidates/upload", **request)

    assert first.status_code == 200, first.text
    assert first.json()["status"] == "parsed"
    assert second.status_code == 200, second.text
    assert second.json() == {
        "candidate_id": first.json()["candidate_id"],
        "status": "duplicate",
    }
    candidates = (await db_session.execute(select(Candidate))).scalars().all()
    assert len(candidates) == 1
    assert minio_storage.list_object_keys(prefix="resumes/") == [
        candidates[0].raw_file_key
    ]
    audits = (await db_session.execute(select(AuditLog))).scalars().all()
    assert {audit.event_type for audit in audits} == {
        "candidate_upload",
        "candidate_duplicate",
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_legacy_duplicate_returns_conflict_and_cleans_new_object(
    client,
    db_session,
    auth_headers,
    minio_storage,
    valid_pdf_bytes,
    monkeypatch,
):
    _mock_resume_pipeline(monkeypatch)
    candidate = Candidate(
        source="upload",
        name_cipher=encrypt_pii("张三"),
        pii_hash=compute_pii_hash(name="张三", phone="13800001234"),
        raw_file_key="C:/legacy/deleted.pdf",
    )
    db_session.add(candidate)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/candidates/upload",
        files={"file": ("resume.pdf", valid_pdf_bytes, "application/pdf")},
        headers=await auth_headers(),
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "candidate_file_conflict"
    assert minio_storage.list_object_keys(prefix="resumes/") == []
    candidates = (await db_session.execute(select(Candidate))).scalars().all()
    assert len(candidates) == 1
    assert candidates[0].raw_file_key == "C:/legacy/deleted.pdf"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_extractor_failure_rolls_back_candidate_and_object(
    client,
    db_session,
    auth_headers,
    minio_storage,
    valid_pdf_bytes,
    monkeypatch,
):
    monkeypatch.setattr(
        "backend.app.tasks.ingest.MinerUClient",
        lambda: SimpleNamespace(
            parse=AsyncMock(
                return_value=ParseResult(markdown="# resume", source="stub")
            )
        ),
    )
    monkeypatch.setattr(
        "backend.app.tasks.ingest.ResumeExtractor",
        lambda: SimpleNamespace(extract=AsyncMock(side_effect=RuntimeError("extract failed"))),
    )

    with pytest.raises(RuntimeError, match="extract failed"):
        await client.post(
            "/api/v1/candidates/upload",
            files={"file": ("resume.pdf", valid_pdf_bytes, "application/pdf")},
            headers=await auth_headers(),
        )

    assert (await db_session.execute(select(Candidate))).scalars().all() == []
    assert minio_storage.list_object_keys(prefix="resumes/") == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_optional_scoring_failure_rolls_back_candidate_and_object(
    client,
    db_session,
    auth_headers,
    minio_storage,
    valid_pdf_bytes,
    monkeypatch,
):
    from datetime import datetime, timezone

    _mock_resume_pipeline(monkeypatch, name="评分失败候选人", phone="13700000000")
    jd = JD(code="FAIL_SCORE", name="Fail Score", description="", status="active")
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
    await db_session.commit()
    monkeypatch.setattr(
        "backend.app.tasks.ingest.ScoringPipeline.run",
        AsyncMock(side_effect=LLMInvalidOutputError("completion leaked private resume")),
    )

    response = await client.post(
        "/api/v1/candidates/upload",
        params={"jd_code": "FAIL_SCORE"},
        files={"file": ("private-resume.pdf", valid_pdf_bytes, "application/pdf")},
        headers=await auth_headers(),
    )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "ai_invalid_output"
    assert "completion leaked private resume" not in response.text
    assert "private-resume.pdf" not in response.text
    assert (await db_session.execute(select(Candidate))).scalars().all() == []
    assert (await db_session.execute(select(Score))).scalars().all() == []
    assert (await db_session.execute(select(AuditLog))).scalars().all() == []
    assert minio_storage.list_object_keys(prefix="resumes/") == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_insert_failure_rolls_back_object(
    client,
    db_session,
    auth_headers,
    minio_storage,
    valid_pdf_bytes,
    monkeypatch,
):
    from backend.app.database import get_db
    from backend.app.main import app

    _mock_resume_pipeline(monkeypatch, name="数据库失败", phone="13600000000")
    original_execute = db_session.execute

    async def fail_candidate_insert(statement, *args, **kwargs):
        if getattr(statement, "is_insert", False) and getattr(
            getattr(statement, "table", None), "name", None
        ) == "candidates":
            raise RuntimeError("candidate insert failed")
        return await original_execute(statement, *args, **kwargs)

    async def override_db():
        yield db_session

    monkeypatch.setattr(db_session, "execute", fail_candidate_insert)
    app.dependency_overrides[get_db] = override_db
    try:
        with pytest.raises(RuntimeError, match="candidate insert failed"):
            await client.post(
                "/api/v1/candidates/upload",
                files={"file": ("resume.pdf", valid_pdf_bytes, "application/pdf")},
                headers=await auth_headers(),
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert (await original_execute(select(Candidate))).scalars().all() == []
    assert minio_storage.list_object_keys(prefix="resumes/") == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cleanup_failure_returns_503_and_logs_trace(
    client,
    db_session,
    auth_headers,
    valid_pdf_bytes,
    monkeypatch,
):
    from unittest.mock import Mock

    from backend.app.services.storage import StoredResume

    parser_error = MinerUUnavailableError("parser unavailable")
    monkeypatch.setattr(
        "backend.app.tasks.ingest.MinerUClient",
        lambda: SimpleNamespace(parse=AsyncMock(side_effect=parser_error)),
    )
    stored = StoredResume(
        object_key="resumes/opaque-cleanup-failure",
        sha256="a" * 64,
        size_bytes=len(valid_pdf_bytes),
        content_type="application/pdf",
    )
    storage = SimpleNamespace(
        store=AsyncMock(return_value=stored),
        delete=AsyncMock(
            side_effect=StorageError(operation="delete", key=stored.object_key)
        ),
    )
    critical = Mock()
    monkeypatch.setattr(
        "backend.app.routers.candidates.ResumeStorageService", lambda: storage
    )
    monkeypatch.setattr("backend.app.tasks.ingest.logger.critical", critical)

    response = await client.post(
        "/api/v1/candidates/upload",
        files={"file": ("secret-name.pdf", valid_pdf_bytes, "application/pdf")},
        headers={**(await auth_headers()), "x-trace-id": "wp1-cleanup-trace"},
    )

    assert response.status_code == 503
    critical.assert_called_once()
    _, kwargs = critical.call_args
    assert kwargs["trace_id"] == "wp1-cleanup-trace"
    assert kwargs["object_key"] == stored.object_key
    assert kwargs["sha256"] == stored.sha256
    assert "secret-name.pdf" not in str(critical.call_args)
