from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.engine import make_url

CELERY_QUEUE = "smartscreen-wp0-test"
CELERY_BINDING_KEY = f"_kombu.binding.{CELERY_QUEUE}"
CELERY_RESULT_PREFIX = f"{CELERY_QUEUE}:"


@dataclass(frozen=True)
class MigrationDatabaseUrls:
    admin_dsn: str
    async_url: str
    sync_url: str


class RedisKeyClient(Protocol):
    def scan_iter(self, *, match: str) -> Iterable[bytes]: ...

    def delete(self, *keys: str | bytes) -> object: ...


def migration_database_urls(
    configured_async_url: str, database_name: str
) -> MigrationDatabaseUrls:
    configured = make_url(configured_async_url)
    admin = configured.set(drivername="postgresql", database="postgres")
    temporary_async = configured.set(database=database_name)
    temporary_sync = configured.set(drivername="postgresql", database=database_name)
    return MigrationDatabaseUrls(
        admin_dsn=admin.render_as_string(hide_password=False),
        async_url=temporary_async.render_as_string(hide_password=False),
        sync_url=temporary_sync.render_as_string(hide_password=False),
    )


def quote_postgres_identifier(identifier: str) -> str:
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def cleanup_celery_keys(client: RedisKeyClient) -> None:
    result_keys = tuple(client.scan_iter(match=f"{CELERY_RESULT_PREFIX}*"))
    client.delete(CELERY_QUEUE, CELERY_BINDING_KEY, *result_keys)
