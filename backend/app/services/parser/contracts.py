from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

TaskState = Literal["pending", "processing", "completed", "failed"]


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class MinerUHealth(_ContractModel):
    status: Literal["healthy"]
    version: str = Field(min_length=1, max_length=64)
    protocol_version: StrictInt
    queued_tasks: StrictInt = Field(default=0, ge=0)
    processing_tasks: StrictInt = Field(default=0, ge=0)
    completed_tasks: StrictInt = Field(default=0, ge=0)
    failed_tasks: StrictInt = Field(default=0, ge=0)
    max_concurrent_requests: StrictInt = Field(default=1, ge=1)


class _TaskPayload(_ContractModel):
    task_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    status: TaskState
    backend: str = Field(min_length=1, max_length=128)
    file_names: list[str] = Field(min_length=1, max_length=16)
    created_at: str = Field(min_length=1, max_length=64)
    started_at: str | None = Field(default=None, max_length=64)
    completed_at: str | None = Field(default=None, max_length=64)
    error: str | None = Field(default=None, max_length=2048)
    status_url: str = Field(min_length=1, max_length=2048)
    result_url: str = Field(min_length=1, max_length=2048)
    queued_ahead: StrictInt | None = Field(default=None, ge=0)

    @field_validator("file_names")
    @classmethod
    def _validate_file_names(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 255 for value in values):
            raise ValueError("invalid task file name")
        return values


class MinerUSubmission(_TaskPayload):
    message: str = Field(min_length=1, max_length=512)


class MinerUTaskStatus(_TaskPayload):
    pass


@dataclass(frozen=True)
class ParseResult:
    markdown: str
    content_list: list[dict[str, Any]] | None = None
    task_id: str = ""
    backend: str = ""
    service_version: str = ""
    protocol_version: int = 0
    duration_ms: int = 0
    compressed_bytes: int = 0
    uncompressed_bytes: int = 0
    source: str = "http"
