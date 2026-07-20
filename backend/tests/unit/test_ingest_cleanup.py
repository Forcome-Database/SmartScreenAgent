import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.app.tasks.ingest import RawFileReference, run_parse_and_score


@pytest.mark.asyncio
async def test_cancellation_rolls_back_and_deletes_owned_object(monkeypatch) -> None:
    parser = SimpleNamespace(parse=AsyncMock(side_effect=asyncio.CancelledError()))
    monkeypatch.setattr("backend.app.tasks.ingest.MinerUClient", lambda: parser)
    db = SimpleNamespace(rollback=AsyncMock())
    storage = SimpleNamespace(delete=AsyncMock())
    raw_file = RawFileReference(
        object_key="resumes/opaque-object",
        sha256="a" * 64,
        size_bytes=100,
        content_type="application/pdf",
        original_name_cipher="encrypted-name",
    )

    with pytest.raises(asyncio.CancelledError):
        await run_parse_and_score(
            db=db,
            local_file_path="synthetic.pdf",
            raw_file=raw_file,
            storage=storage,
            source="upload",
            source_external_id=None,
            jd_code=None,
        )

    db.rollback.assert_awaited_once_with()
    storage.delete.assert_awaited_once_with(raw_file.object_key)

