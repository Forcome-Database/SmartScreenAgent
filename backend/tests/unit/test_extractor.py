import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.app.services.parser.extractor import ResumeExtractor, ExtractedResume
from backend.app.services.llm.schemas import LLMResponse

SAMPLE = (Path(__file__).parents[1] / "fixtures" / "sample_resume.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_extract_returns_structured():
    fake_payload = {
        "name": "张三",
        "phone": "138-0000-1234",
        "email": "zhangsan@example.com",
        "education": "本科",
        "age": 30,
        "experiences": [
            {
                "company": "ABC 外贸公司",
                "title": "外贸业务员",
                "start": "2021-03",
                "end": "2024-05",
                "description": "独立负责美国五金客户开发，报关、订舱、单证",
            }
        ],
    }
    gateway = AsyncMock()
    gateway.extract.return_value = LLMResponse(
        content=json.dumps(fake_payload, ensure_ascii=False),
        model="deepseek-v4",
        input_tokens=100,
        output_tokens=50,
    )
    extractor = ResumeExtractor(gateway=gateway)
    result = await extractor.extract(SAMPLE)
    assert isinstance(result, ExtractedResume)
    assert result.name == "张三"
    assert result.experiences[0].company == "ABC 外贸公司"
    assert result.experiences[0].start == "2021-03"


@pytest.mark.asyncio
async def test_extract_retries_once_on_invalid_json():
    bad_then_good = [
        LLMResponse(content="not json", model="x", input_tokens=1, output_tokens=1),
        LLMResponse(
            content=json.dumps(
                {
                    "name": "张三",
                    "phone": None,
                    "email": None,
                    "education": "本科",
                    "age": None,
                    "experiences": [],
                }
            ),
            model="x",
            input_tokens=1,
            output_tokens=1,
        ),
    ]
    gateway = AsyncMock()
    gateway.extract.side_effect = bad_then_good
    extractor = ResumeExtractor(gateway=gateway)
    result = await extractor.extract(SAMPLE)
    assert result.name == "张三"
    assert gateway.extract.call_count == 2
