from dataclasses import dataclass


@dataclass(frozen=True)
class LLMResponse:
    content: str
    model: str
    input_tokens: int
    output_tokens: int
    prompt_version: str = ""
    latency_ms: int = 0
    used_fallback: bool = False
