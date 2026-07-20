import pytest
from pydantic import ValidationError

from backend.app.config import get_settings
from backend.app.services.parser.contracts import (
    MinerUOfficialBatchResponse,
    MinerUOfficialExtractResult,
    MinerUOfficialUploadResponse,
)


def test_wp2_mineru_settings_have_official_v4_defaults() -> None:
    settings = get_settings()

    assert settings.MINERU_EXPECTED_PROTOCOL_VERSION == 4
    assert settings.MINERU_MODEL_VERSION == "vlm"
    assert settings.MINERU_LANGUAGE == "ch"
    assert settings.mineru_upload_hosts == ("mineru.oss-cn-shanghai.aliyuncs.com",)
    assert settings.mineru_result_hosts == ("cdn-mineru.openxlab.org.cn",)
    assert settings.MINERU_RESULT_MAX_BYTES > 0
    assert settings.MINERU_RESULT_MAX_UNCOMPRESSED_BYTES >= settings.MINERU_RESULT_MAX_BYTES
    assert settings.MINERU_RESULT_MAX_MEMBERS > 0


def test_upload_contract_accepts_official_v4_payload() -> None:
    response = MinerUOfficialUploadResponse.model_validate(
        {
            "code": 0,
            "msg": "ok",
            "trace_id": "trace-123",
            "data": {
                "batch_id": "batch-123",
                "file_urls": ["https://mineru.oss-cn-shanghai.aliyuncs.com/upload"],
            },
        }
    )

    assert response.data.batch_id == "batch-123"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "code": True,
            "msg": "ok",
            "trace_id": "trace-123",
            "data": {"batch_id": "batch-123", "file_urls": ["https://example"]},
        },
        {
            "code": 0,
            "msg": "ok",
            "trace_id": "trace-123",
            "data": {"batch_id": "../escape", "file_urls": ["https://example"]},
        },
        {
            "code": 0,
            "msg": "ok",
            "trace_id": "trace-123",
            "data": {"batch_id": "batch-123", "file_urls": []},
        },
        {
            "code": 0,
            "msg": "ok",
            "trace_id": "trace-123",
            "data": {
                "batch_id": "batch-123",
                "file_urls": ["https://example"],
                "extra": "value",
            },
        },
    ],
)
def test_upload_contract_rejects_invalid_payload(payload: dict) -> None:
    with pytest.raises(ValidationError):
        MinerUOfficialUploadResponse.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "file_name": "resume.pdf",
            "state": "done",
            "err_msg": "",
            "full_zip_url": None,
        },
        {
            "file_name": "resume.pdf",
            "state": "failed",
            "err_msg": "",
        },
        {
            "file_name": "resume.pdf",
            "state": "pending",
            "err_msg": "",
            "full_zip_url": "https://example/result.zip",
        },
        {
            "file_name": "resume.pdf",
            "state": "mystery",
            "err_msg": "",
        },
    ],
)
def test_extract_result_rejects_invalid_state_fields(payload: dict) -> None:
    with pytest.raises(ValidationError):
        MinerUOfficialExtractResult.model_validate(payload)


def test_batch_result_contract_accepts_completed_payload() -> None:
    response = MinerUOfficialBatchResponse.model_validate(
        {
            "code": 0,
            "msg": "ok",
            "trace_id": "trace-123",
            "data": {
                "batch_id": "batch-123",
                "extract_result": [
                    {
                        "file_name": "resume.pdf",
                        "data_id": "data-123",
                        "state": "done",
                        "err_msg": "",
                        "full_zip_url": "https://cdn.example/result.zip",
                    }
                ],
            },
        }
    )

    assert response.data.extract_result[0].state == "done"
