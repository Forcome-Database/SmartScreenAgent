from __future__ import annotations

from sqlalchemy.pool import NullPool

from backend.app.database import engine


def test_tests_run_without_connection_pooling() -> None:
    """asyncpg connections are event-loop affine, so tests must not pool them.

    pytest-asyncio gives each test its own event loop, and the in-thread Celery
    worker runs task bodies on yet another loop against this same module-level
    engine. A pooled connection handed to a different loop than the one that
    opened it corrupts mid-flight ("attached to a different loop"), surfacing as
    unrelated failures or a hung suite much later in the run.
    """
    assert isinstance(engine.pool, NullPool)
