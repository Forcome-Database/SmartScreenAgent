from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class LLMResponse:
    content: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    call_group_id: UUID
    prompt_version: str = ""
    latency_ms: int = 0
    used_fallback: bool = False
