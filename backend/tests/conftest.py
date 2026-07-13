import os

from backend.tests.test_bootstrap import apply_test_environment

apply_test_environment(os.environ)

import pytest


@pytest.fixture(autouse=True)
async def _dispose_db_engine_between_async_tests():
    """Per-test event loop + pooled asyncpg connections don't mix on Windows.

    Each async test gets its own loop; SQLAlchemy's async engine caches
    connections bound to the previous loop, which then raise
    "Event loop is closed" on cleanup. Disposing after each test
    forces fresh connections per loop.
    """
    yield
    try:
        from backend.app.database import engine

        await engine.dispose()
    except Exception:  # noqa: BLE001
        pass
