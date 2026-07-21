"""WP3 Task 8: end-to-end async ingestion and crash-recovery integration tests.

Both tests drive the real, in-process `celery_worker` fixture (not a direct
`run_job(...)` call) so they exercise the actual production path: HTTP upload
-> Celery enqueue -> worker claim -> state machine -> job status endpoint.
`MinerUClient`/`ResumeExtractor` are monkeypatched at the
`backend.app.tasks.ingest` import site (the same pattern already used by
`test_tasks_ingest.py::test_celery_task_downloads_verified_object`) because
there is no real MinerU/newapi endpoint reachable in this environment; the
monkeypatch is visible to the worker's background thread since it shares the
same Python process/module namespace.
"""

from __future__ import annotations

import hashlib
import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from backend.app.models import Candidate, IngestionJob, Score
from backend.app.security.crypto import encrypt_pii
from backend.app.services.ingestion.states import IngestionState
from backend.app.services.parser.extractor import ExtractedResume
from backend.app.services.parser.mineru_client import ParseResult

pytestmark = pytest.mark.integration


def _stub_parser_and_extractor(monkeypatch, *, name: str, phone: str) -> None:
    """Replace the worker's parser/extractor with deterministic stubs.

    Patched at `backend.app.tasks.ingest.*` — the names `run_job` actually
    calls — not at the defining module, matching every other worker-path test
    in this suite.
    """
    monkeypatch.setattr(
        "backend.app.tasks.ingest.MinerUClient",
        lambda: SimpleNamespace(
            parse=AsyncMock(return_value=ParseResult(markdown="# e2e resume", source="stub"))
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


async def test_upload_returns_202_then_worker_completes(
    client,
    auth_headers,
    valid_pdf_bytes,
    minio_storage,
    celery_worker,
    monkeypatch,
):
    """The full async contract end to end: `POST /upload` returns `202` with
    a `job_id`; a real (in-process) Celery worker claims and drives the job
    through the state machine; the status endpoint reflects a terminal-
    success state with `candidate_id` populated once the worker is done.

    `enqueue_job` is monkeypatched to a recorder (the same seam every other
    upload test in `test_candidates_api.py` uses) and the actual hand-off to
    Celery is done explicitly, right after `await engine.dispose()` — the
    same "dispose the shared pool immediately before `.delay()`" pattern
    already established by `test_tasks_ingest.py::
    test_celery_task_downloads_verified_object` and by the `run_one_sweep`
    fixture. This is not a shortcut around the real worker: `enqueue_job`'s
    entire body is `parse_and_score_task.delay(job_id)`, so this reproduces
    exactly what the router does.

    Unlike an earlier version of this test, completion is awaited via
    `task_result.get(...)` rather than concurrent HTTP polling. Polling
    while the worker runs makes *repeated* main-loop queries against the
    shared async engine concurrently with the worker's own (separate
    background-thread) loop; asyncpg connections are bound to the loop that
    created them, so a connection the main loop's own commit just returned
    to the shared pool can get picked up by the worker's loop and crash it
    with "attached to a different loop" — confirmed by reproduction:
    `IngestionJobService.claim`'s first query raised exactly that
    `RuntimeError`, before the job ever left `queued`, which is why the old
    polling loop timed out and pytest then hung tearing down the corrupted
    pool/worker. `.get(timeout=...)` blocks the main thread until the
    worker's task fully completes with no concurrent main-loop DB access in
    between, so there is no hazard; only after that do we make a single
    `GET /jobs/{id}` call to exercise the status endpoint. Production is
    unaffected: a real worker process never shares this engine object with
    anything.
    """
    from backend.app.database import engine
    from backend.app.tasks.ingest import parse_and_score_task

    _stub_parser_and_extractor(monkeypatch, name="E2E Candidate", phone="13800000001")
    enqueued: list[int] = []
    monkeypatch.setattr(
        "backend.app.routers.candidates.enqueue_job", lambda job_id: enqueued.append(job_id)
    )
    headers = await auth_headers("hr")

    resp = await client.post(
        "/api/v1/candidates/upload",
        files={"file": ("r.pdf", valid_pdf_bytes, "application/pdf")},
        headers=headers,
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["state"] == "queued"
    job_id = body["job_id"]
    assert job_id is not None
    assert enqueued == [job_id]

    await engine.dispose()
    task_result = parse_and_score_task.delay(job_id)
    try:
        task_result.get(timeout=30)
    finally:
        task_result.forget()

    status_resp = await client.get(f"/api/v1/candidates/jobs/{job_id}", headers=headers)
    assert status_resp.status_code == 200, status_resp.text
    final = status_resp.json()
    assert final["state"] in {"ready", "completed"}
    assert final["candidate_id"] is not None


async def test_crash_recovery_no_duplicate_candidate(
    client,
    auth_headers,
    valid_pdf_bytes,
    db_session,
    minio_storage,
    celery_worker,
    run_one_sweep,
    monkeypatch,
):
    """Simulate a worker that crashed mid-parse: insert an `ingestion_jobs`
    row stuck in `parsing` with an EXPIRED lease, pointing at an object that
    is actually stored in MinIO (not the dummy keys `stored_job_factory`
    uses). One sweep pass reclaims the expired lease and re-enqueues the job
    (attempts=1 is below `INGESTION_MAX_ATTEMPTS`); the already-running
    worker then completes it. The durability property under test: exactly
    ONE candidate (and, if scoring ran, exactly ONE score) exists afterward
    — the crash + sweep + retry cycle must not double-create either row.

    Completion is awaited via the `AsyncResult`s `run_one_sweep` hands back
    (one per re-enqueued job) rather than concurrent HTTP polling, for the
    same cross-loop asyncpg reason documented on
    `test_upload_returns_202_then_worker_completes`: polling would issue
    repeated main-loop queries against the shared async engine while the
    worker (its own thread/loop) is still running the re-enqueued job,
    which can crash the worker task and leave the job stuck forever instead
    of exercising the crash-recovery path this test is actually about.
    """
    _stub_parser_and_extractor(monkeypatch, name="Crash Recovery Candidate", phone="13800000002")
    headers = await auth_headers("hr")

    body = valid_pdf_bytes
    sha256 = hashlib.sha256(body).hexdigest()
    object_key = "resumes/2026/07/crash-recovery-e2e"
    minio_storage.put_object(
        object_key,
        io.BytesIO(body),
        len(body),
        content_type="application/pdf",
        metadata={"sha256": sha256},
    )

    stuck_job = IngestionJob(
        state=IngestionState.PARSING.value,
        source="upload",
        source_external_id=None,
        jd_code=None,
        raw_file_key=object_key,
        raw_file_sha256=sha256,
        raw_file_size_bytes=len(body),
        raw_file_content_type="application/pdf",
        raw_file_original_name_cipher=encrypt_pii("crash-recovery.pdf"),
        attempts=1,
        lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        actor="test",
    )
    db_session.add(stuck_job)
    await db_session.commit()
    job_id = stuck_job.id

    report, async_results = await run_one_sweep()
    assert job_id in report.requeued

    for task_result in async_results:
        try:
            task_result.get(timeout=30)
        finally:
            task_result.forget()

    status_resp = await client.get(f"/api/v1/candidates/jobs/{job_id}", headers=headers)
    assert status_resp.status_code == 200, status_resp.text
    final = status_resp.json()
    assert final["candidate_id"] is not None

    # Fresh session: the engine's shared pool was disposed (by `run_one_sweep`
    # before `.delay()`, and again by the worker task's own end-of-task
    # `engine.dispose()`), so a session opened before that point may not be
    # safely reusable — same rationale as `verify_db` in
    # `test_tasks_ingest.py::test_celery_task_downloads_verified_object`.
    from backend.app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as verify_db:
        candidates = (
            (
                await verify_db.execute(
                    select(Candidate).where(Candidate.raw_file_sha256 == sha256)
                )
            )
            .scalars()
            .all()
        )
        assert len(candidates) == 1, "crash + sweep + retry must not double-create a candidate"

        if final["score_id"] is not None:
            scores = (
                (
                    await verify_db.execute(
                        select(Score).where(Score.candidate_id == candidates[0].id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(scores) == 1, "crash + sweep + retry must not double-create a score"
