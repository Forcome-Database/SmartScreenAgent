from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import httpx

from backend.app.config import get_settings


@dataclass
class ParseResult:
    markdown: str
    layout: dict = field(default_factory=dict)
    source: str = "stub"  # "stub" | "http"


class MinerUClient:
    """Thin client for the self-hosted mineru-api service.

    See docs/specs/research/mineru.md §3 for the architectural choice (HTTP over
    library embedding) and §5 for deployment shape. The exact /file_parse
    request/response schema is still TBD-verify-with-runtime; this client
    assumes a minimal {markdown, layout} response and will need a follow-up
    pass once the live container is reachable.
    """

    def __init__(self) -> None:
        self.settings = get_settings()

    async def parse(self, file_path: Path) -> ParseResult:
        mode = self.settings.MINERU_MODE
        if mode == "stub":
            return self._parse_stub(file_path)
        if mode == "http":
            return await self._parse_http(file_path)
        raise NotImplementedError(f"MINERU_MODE={mode!r} not supported")

    def _parse_stub(self, file_path: Path) -> ParseResult:
        return ParseResult(
            markdown=(
                f"# Stub Resume from {file_path.name}\n\n"
                "姓名：张三\n电话：13800001234\n"
            ),
            layout={},
            source="stub",
        )

    async def _parse_http(self, file_path: Path) -> ParseResult:
        url = f"{self.settings.MINERU_BASE_URL.rstrip('/')}/file_parse"
        headers: dict[str, str] = {}
        if self.settings.MINERU_API_KEY:
            headers["Authorization"] = f"Bearer {self.settings.MINERU_API_KEY}"
        async with httpx.AsyncClient(timeout=120) as client:
            with file_path.open("rb") as f:
                resp = await client.post(
                    url, headers=headers, files={"file": (file_path.name, f)}
                )
            resp.raise_for_status()
            data = resp.json()
        return ParseResult(
            markdown=data.get("markdown", ""),
            layout=data.get("layout", {}),
            source="http",
        )
