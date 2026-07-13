from sqlalchemy.engine import make_url

from backend.tests.integration.isolation import (
    CELERY_BINDING_KEY,
    CELERY_QUEUE,
    CELERY_RESULT_PREFIX,
    cleanup_celery_keys,
    migration_database_urls,
    quote_postgres_identifier,
)


class _FakeRedis:
    def __init__(self, scanned_keys: list[bytes]) -> None:
        self.scanned_keys = scanned_keys
        self.scan_patterns: list[str] = []
        self.deleted: tuple[str | bytes, ...] = ()

    def scan_iter(self, *, match: str):
        self.scan_patterns.append(match)
        return iter(self.scanned_keys)

    def delete(self, *keys: str | bytes) -> None:
        self.deleted = keys


def test_migration_database_urls_target_only_temporary_database() -> None:
    urls = migration_database_urls(
        "postgresql+asyncpg://user:p%40ss@db.example:5433/application?ssl=require",
        "smartscreen_migration_abc123",
    )

    admin = make_url(urls.admin_dsn)
    temporary_async = make_url(urls.async_url)
    temporary_sync = make_url(urls.sync_url)

    assert admin.drivername == "postgresql"
    assert admin.database == "postgres"
    assert temporary_async.drivername == "postgresql+asyncpg"
    assert temporary_async.database == "smartscreen_migration_abc123"
    assert temporary_sync.drivername == "postgresql"
    assert temporary_sync.database == "smartscreen_migration_abc123"
    assert temporary_async.username == admin.username == "user"
    assert temporary_async.password == admin.password == "p@ss"
    assert temporary_async.host == admin.host == "db.example"
    assert temporary_async.port == admin.port == 5433
    assert temporary_async.query == admin.query == {"ssl": "require"}


def test_postgres_identifier_quoting_escapes_double_quotes() -> None:
    assert quote_postgres_identifier('name"suffix') == '"name""suffix"'


def test_cleanup_celery_keys_selects_only_wp0_namespace() -> None:
    result_keys = [
        f"{CELERY_RESULT_PREFIX}celery-task-meta-1".encode(),
        f"{CELERY_RESULT_PREFIX}celery-task-meta-2".encode(),
    ]
    client = _FakeRedis(result_keys)

    cleanup_celery_keys(client)

    assert client.scan_patterns == [f"{CELERY_RESULT_PREFIX}*"]
    assert client.deleted == (CELERY_QUEUE, CELERY_BINDING_KEY, *result_keys)
