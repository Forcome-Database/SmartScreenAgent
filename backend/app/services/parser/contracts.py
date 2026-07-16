from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

OfficialTaskState = Literal[
    "waiting-file",
    "pending",
    "running",
    "converting",
    "done",
    "failed",
]

_OPAQUE_ID_PATTERN = r"^[A-Za-z0-9_.-]+$"


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class MinerUOfficialUploadData(_ContractModel):
    batch_id: str = Field(min_length=1, max_length=128, pattern=_OPAQUE_ID_PATTERN)
    file_urls: list[str] = Field(min_length=1, max_length=200)

    @field_validator("file_urls")
    @classmethod
    def _bounded_urls(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 4096 for value in values):
            raise ValueError("invalid MinerU upload URL")
        return values


class MinerUOfficialUploadResponse(_ContractModel):
    code: StrictInt
    msg: str = Field(min_length=1, max_length=1024)
    trace_id: str = Field(min_length=1, max_length=128, pattern=_OPAQUE_ID_PATTERN)
    data: MinerUOfficialUploadData


class MinerUOfficialProgress(_ContractModel):
    extracted_pages: StrictInt = Field(ge=0)
    total_pages: StrictInt = Field(ge=0)
    start_time: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _page_counts_are_consistent(self) -> MinerUOfficialProgress:
        if self.total_pages < 1 or self.extracted_pages > self.total_pages:
            raise ValueError("invalid MinerU extraction progress")
        return self


class MinerUOfficialExtractResult(_ContractModel):
    file_name: str = Field(min_length=1, max_length=255)
    state: OfficialTaskState
    err_msg: str = Field(default="", max_length=2048)
    full_zip_url: str | None = Field(default=None, min_length=1, max_length=4096)
    data_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=_OPAQUE_ID_PATTERN,
    )
    extract_progress: MinerUOfficialProgress | None = None

    @model_validator(mode="after")
    def _state_fields_are_consistent(self) -> MinerUOfficialExtractResult:
        if self.state == "done":
            if not self.full_zip_url or self.err_msg:
                raise ValueError("invalid completed MinerU result")
        elif self.state == "failed":
            if not self.err_msg or self.full_zip_url is not None:
                raise ValueError("invalid failed MinerU result")
        elif self.full_zip_url is not None:
            raise ValueError("incomplete MinerU result cannot have a ZIP URL")
        return self


class MinerUOfficialBatchData(_ContractModel):
    batch_id: str = Field(min_length=1, max_length=128, pattern=_OPAQUE_ID_PATTERN)
    extract_result: list[MinerUOfficialExtractResult] = Field(min_length=1, max_length=200)


class MinerUOfficialBatchResponse(_ContractModel):
    code: StrictInt
    msg: str = Field(min_length=1, max_length=1024)
    trace_id: str = Field(min_length=1, max_length=128, pattern=_OPAQUE_ID_PATTERN)
    data: MinerUOfficialBatchData


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
    source: str = "official"
