from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.database import get_db
from backend.app.deps import get_current_user
from backend.app.main import app
from backend.app.services.storage.resume_storage import StoredResume
from backend.app.services.upload.validation import UploadArtifact


@pytest.fixture
def fake_upload_env(monkeypatch, tmp_path):
    """Doubles for auth, upload validation, storage, and the DB dependency.

    Keeps this a pure unit test: no real JWT, MinIO, or Postgres round-trip.
    `enqueue_job` is intentionally left alone here — individual tests
    monkeypatch it so they can assert on what got enqueued.
    """
    fake_user = SimpleNamespace(id=1, role="hr")

    async def _fake_current_user() -> SimpleNamespace:
        return fake_user

    app.dependency_overrides[get_current_user] = _fake_current_user

    fake_db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    async def _fake_get_db():
        yield fake_db

    app.dependency_overrides[get_db] = _fake_get_db

    pdf_bytes = b"%PDF-1.4 fake pdf body"
    local_path = tmp_path / "resume.pdf"
    local_path.write_bytes(pdf_bytes)

    async def _fake_validate(upload) -> UploadArtifact:
        return UploadArtifact(
            path=local_path,
            original_filename=upload.filename or "resume.pdf",
            content_type="application/pdf",
            size_bytes=len(pdf_bytes),
            sha256="a" * 64,
        )

    monkeypatch.setattr(
        "backend.app.routers.candidates.UploadValidator",
        lambda: SimpleNamespace(validate=_fake_validate),
    )
    monkeypatch.setattr(
        "backend.app.routers.candidates.get_malware_scanner",
        lambda mode: SimpleNamespace(scan=AsyncMock()),
    )

    stored = StoredResume(
        object_key="resumes/fake/object",
        sha256="a" * 64,
        size_bytes=len(pdf_bytes),
        content_type="application/pdf",
    )
    fake_storage = SimpleNamespace(store=AsyncMock(return_value=stored), delete=AsyncMock())
    monkeypatch.setattr(
        "backend.app.routers.candidates.ResumeStorageService", lambda: fake_storage
    )

    fake_job = SimpleNamespace(id=42, batch_id=None, state="queued")

    async def _fake_create_or_reuse(**kwargs):
        return fake_job, True

    fake_svc = SimpleNamespace(create_or_reuse=_fake_create_or_reuse)
    monkeypatch.setattr(
        "backend.app.routers.candidates.IngestionJobService", lambda db: fake_svc
    )

    env = SimpleNamespace(
        pdf_bytes=pdf_bytes,
        auth_headers={"Authorization": "Bearer unit-test-token"},
        fake_job=fake_job,
        fake_storage=fake_storage,
        fake_db=fake_db,
    )
    try:
        yield env
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_upload_returns_202_and_enqueues(monkeypatch, fake_upload_env):
    enqueued = []
    monkeypatch.setattr(
        "backend.app.routers.candidates.enqueue_job", lambda job_id: enqueued.append(job_id)
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/candidates/upload",
            files={"file": ("r.pdf", fake_upload_env.pdf_bytes, "application/pdf")},
            headers=fake_upload_env.auth_headers,
        )
    assert resp.status_code == 202
    body = resp.json()
    assert body["state"] == "queued" and body["job_id"] and enqueued == [body["job_id"]]
    assert body["job_id"] == fake_upload_env.fake_job.id
    assert body["batch_id"] is None
    fake_upload_env.fake_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_upload_does_not_enqueue_when_job_already_existed(monkeypatch, fake_upload_env):
    """sha256 idempotency: `created=False` must skip enqueue and delete the redundant object."""
    enqueued = []
    monkeypatch.setattr(
        "backend.app.routers.candidates.enqueue_job", lambda job_id: enqueued.append(job_id)
    )

    async def _fake_create_or_reuse(**kwargs):
        return fake_upload_env.fake_job, False

    monkeypatch.setattr(
        "backend.app.routers.candidates.IngestionJobService",
        lambda db: SimpleNamespace(create_or_reuse=_fake_create_or_reuse),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/candidates/upload",
            files={"file": ("r.pdf", fake_upload_env.pdf_bytes, "application/pdf")},
            headers=fake_upload_env.auth_headers,
        )

    assert resp.status_code == 202
    assert resp.json()["job_id"] == fake_upload_env.fake_job.id
    assert enqueued == []
    fake_upload_env.fake_storage.delete.assert_awaited_once_with("resumes/fake/object")
    fake_upload_env.fake_db.commit.assert_awaited_once()
