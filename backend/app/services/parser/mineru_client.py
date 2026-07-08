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


class MinerUParseError(RuntimeError):
    pass


def _response_payload(data: dict) -> dict:
    for key in ("data", "result"):
        nested = data.get(key)
        if isinstance(nested, dict):
            return nested
    return data


def _parse_response(data: dict) -> ParseResult:
    payload = _response_payload(data)
    markdown = payload.get("markdown")
    if not isinstance(markdown, str) or not markdown.strip():
        markdown = payload.get("md_content")
    if not isinstance(markdown, str) or not markdown.strip():
        raise MinerUParseError("missing markdown in MinerU response")
    layout = payload.get("layout", {})
    if layout is None:
        layout = {}
    if not isinstance(layout, dict):
        raise MinerUParseError("invalid layout in MinerU response")
    return ParseResult(markdown=markdown, layout=layout, source="http")


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
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                with file_path.open("rb") as f:
                    resp = await client.post(
                        url, headers=headers, files={"file": (file_path.name, f)}
                    )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            raise MinerUParseError(
                "MinerU HTTP parse failed: "
                f"mode=http url={url} status_code={e.response.status_code}"
            ) from e
        except (httpx.HTTPError, OSError) as e:
            raise MinerUParseError(
                f"MinerU HTTP parse failed: mode=http url={url} error={type(e).__name__}"
            ) from e
        except ValueError as e:
            raise MinerUParseError(
                f"invalid json response from MinerU: mode=http url={url}"
            ) from e
        if not isinstance(data, dict):
            raise MinerUParseError(
                f"invalid json response from MinerU: mode=http url={url}"
            )
        return _parse_response(data)
