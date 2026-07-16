import pytest
from pydantic import ValidationError

from backend.app.config import get_settings
from backend.app.services.parser.contracts import (
    MinerUHealth,
    MinerUSubmission,
    MinerUTaskStatus,
)


def test_wp2_mineru_settings_have_locked_defaults() -> None:
    settings = get_settings()

    assert settings.MINERU_EXPECTED_PROTOCOL_VERSION == 2
    assert settings.MINERU_BACKEND == "hybrid-engine"
    assert settings.MINERU_EFFORT == "medium"
    assert settings.MINERU_LANGUAGE == "ch"
    assert settings.MINERU_PARSE_METHOD == "auto"
    assert settings.MINERU_RESULT_MAX_BYTES > 0
    assert settings.MINERU_RESULT_MAX_UNCOMPRESSED_BYTES >= settings.MINERU_RESULT_MAX_BYTES
    assert settings.MINERU_RESULT_MAX_MEMBERS > 0
    assert settings.LLM_STRUCTURED_OUTPUT_MODE in {"json_schema", "json_object"}


def test_health_contract_accepts_official_protocol_payload() -> None:
    health = MinerUHealth.model_validate(
        {
            "status": "healthy",
            "version": "3.4.4",
            "protocol_version": 2,
            "queued_tasks": 0,
            "processing_tasks": 1,
            "completed_tasks": 2,
            "failed_tasks": 0,
            "max_concurrent_requests": 4,
        }
    )

    assert health.version == "3.4.4"
    assert health.protocol_version == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "unhealthy", "version": "3.4.4", "protocol_version": 2},
        {"status": "healthy", "version": "", "protocol_version": 2},
        {"status": "healthy", "version": "3.4.4", "protocol_version": True},
        {
            "status": "healthy",
            "version": "3.4.4",
            "protocol_version": 2,
            "unexpected": "value",
        },
    ],
)
def test_health_contract_rejects_invalid_payload(payload: dict) -> None:
    with pytest.raises(ValidationError):
        MinerUHealth.model_validate(payload)


def test_submission_contract_accepts_official_payload() -> None:
    submission = MinerUSubmission.model_validate(
        {
            "task_id": "abc-123",
            "status": "pending",
            "backend": "hybrid-engine",
            "file_names": ["resume.pdf"],
            "created_at": "2026-07-16T00:00:00Z",
            "started_at": None,
            "completed_at": None,
            "error": None,
            "status_url": "https://mineru.example/tasks/abc-123",
            "result_url": "https://mineru.example/tasks/abc-123/result",
            "queued_ahead": 0,
            "message": "Task submitted successfully",
        }
    )

    assert submission.task_id == "abc-123"
    assert submission.status == "pending"


@pytest.mark.parametrize("task_id", ["", "../escape", "has space", "x" * 129])
def test_submission_contract_rejects_unsafe_task_id(task_id: str) -> None:
    with pytest.raises(ValidationError):
        MinerUSubmission.model_validate(
            {
                "task_id": task_id,
                "status": "pending",
                "backend": "pipeline",
                "file_names": ["resume.pdf"],
                "created_at": "now",
                "status_url": "https://mineru.example/status",
                "result_url": "https://mineru.example/result",
                "message": "submitted",
            }
        )


def test_status_contract_rejects_unknown_state_and_extra_fields() -> None:
    base = {
        "task_id": "abc-123",
        "backend": "pipeline",
        "file_names": ["resume.pdf"],
        "created_at": "now",
        "started_at": None,
        "completed_at": None,
        "error": None,
        "status_url": "https://mineru.example/tasks/abc-123",
        "result_url": "https://mineru.example/tasks/abc-123/result",
    }

    with pytest.raises(ValidationError):
        MinerUTaskStatus.model_validate({**base, "status": "mystery"})
    with pytest.raises(ValidationError):
        MinerUTaskStatus.model_validate({**base, "status": "completed", "extra": 1})
