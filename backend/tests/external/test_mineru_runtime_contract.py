from pathlib import Path

import httpx
import pytest

from backend.app.config import get_settings
from backend.app.services.parser.contracts import MinerUHealth
from backend.app.services.parser.extractor import ResumeExtractor
from backend.app.services.parser.mineru_client import MinerUClient
from backend.tests.fixtures.resumes.synthetic_resume import build_synthetic_resume

pytestmark = pytest.mark.external_contract


@pytest.mark.asyncio
async def test_deployed_mineru_health_and_openapi_contract() -> None:
    settings = get_settings()
    headers = (
        {"Authorization": f"Bearer {settings.MINERU_API_KEY}"}
        if settings.MINERU_API_KEY
        else {}
    )
    async with httpx.AsyncClient(timeout=settings.MINERU_HTTP_TIMEOUT_SECONDS) as client:
        health_response = await client.get(
            f"{settings.MINERU_BASE_URL.rstrip('/')}/health", headers=headers
        )
        health_response.raise_for_status()
        health = MinerUHealth.model_validate(health_response.json())
        openapi_response = await client.get(
            f"{settings.MINERU_BASE_URL.rstrip('/')}/openapi.json", headers=headers
        )
        openapi_response.raise_for_status()

    assert health.protocol_version == settings.MINERU_EXPECTED_PROTOCOL_VERSION
    paths = openapi_response.json()["paths"]
    assert {"/health", "/tasks", "/tasks/{task_id}", "/tasks/{task_id}/result"} <= set(
        paths
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [".pdf", ".docx", ".png", ".jpg"])
async def test_four_supported_formats_parse_and_extract(
    tmp_path: Path, suffix: str
) -> None:
    source = build_synthetic_resume(tmp_path / f"synthetic-resume{suffix}")

    parsed = await MinerUClient().parse(source)
    extracted = await ResumeExtractor().extract(parsed.markdown)

    assert parsed.markdown.strip()
    assert parsed.protocol_version == 2
    assert parsed.service_version
    assert extracted.schema_version == 1
    assert extracted.prompt_version == "resume_extract_v1"
    assert extracted.model
