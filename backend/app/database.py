import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.config import get_settings

settings = get_settings()

# Tests share this module-level engine across several event loops: pytest-asyncio
# opens one per test, and the in-thread Celery worker runs task bodies on its own.
# asyncpg connections are loop-affine, so a POOLED connection reused from another
# loop breaks — and it breaks later, in an unrelated test. Production keeps the
# pool; only the test bootstrap sets this flag.
if os.environ.get("SMARTSCREEN_DB_NULLPOOL") == "1":
    engine = create_async_engine(
        settings.DATABASE_URL,
        poolclass=NullPool,
        echo=False,
    )
else:
    engine = create_async_engine(
        settings.DATABASE_URL,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        echo=False,
    )

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
