from pathlib import Path

import pytest

from backend.app.services.parser.mineru_client import MinerUClient
from backend.tests.fixtures.resumes.synthetic_resume import build_synthetic_resume

pytestmark = pytest.mark.external_contract


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [".pdf", ".docx", ".png", ".jpg"])
async def test_official_v4_parses_all_supported_formats(tmp_path: Path, suffix: str) -> None:
    source = build_synthetic_resume(tmp_path / f"synthetic-resume{suffix}")

    parsed = await MinerUClient().parse(source)

    assert parsed.markdown.strip()
    assert parsed.protocol_version == 4
    assert parsed.service_version == "official-api-v4"
    assert parsed.backend in {"pipeline", "vlm"}
    assert parsed.source == "official"
    assert parsed.task_id
    assert parsed.compressed_bytes > 0
    assert parsed.uncompressed_bytes > 0
