from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from pathlib import Path

import typer
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.config import get_settings
from backend.app.models import JD, RuleVersion
from backend.app.rules.excel_importer import import_workbook

cli = typer.Typer(help="SmartScreen admin CLI", no_args_is_help=True)


@cli.callback()
def _root() -> None:
    """SmartScreen admin CLI root group (keeps subcommand routing explicit)."""


async def _apply_rules(rules: list, version_label: str, xlsx_name: str) -> None:
    """Open a fresh engine bound to the current loop, then persist all rules."""
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True, echo=False)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    try:
        async with session_factory() as db:
            for rule in rules:
                # 创建 JD（已存在则跳过）
                await db.execute(
                    pg_insert(JD)
                    .values(
                        code=rule.jd_code,
                        name=rule.jd_code.replace("_", " ").title(),
                        description="",
                        status="active",
                    )
                    .on_conflict_do_nothing(index_elements=["code"])
                )
                await db.commit()
                jd = (
                    await db.execute(select(JD).where(JD.code == rule.jd_code))
                ).scalar_one()

                rv = RuleVersion(
                    jd_id=jd.id,
                    version=version_label,
                    schema_json=rule.model_dump(),
                    published_at=datetime.now(tz=timezone.utc),
                    notes=f"imported from {xlsx_name}",
                )
                db.add(rv)
                await db.flush()
                jd.active_rule_version_id = rv.id
                await db.commit()
                typer.echo(f"✓ {rule.jd_code} → rule_version={rv.id}")
    finally:
        await engine.dispose()


def _run_async(coro_factory) -> None:
    """Run an async coroutine via asyncio.run, falling back to a worker thread
    when a loop is already running (e.g. under pytest-asyncio + CliRunner)."""
    try:
        asyncio.get_running_loop()
        loop_running = True
    except RuntimeError:
        loop_running = False

    if not loop_running:
        asyncio.run(coro_factory())
        return

    error: list[BaseException] = []

    def _worker() -> None:
        try:
            asyncio.run(coro_factory())
        except BaseException as e:  # noqa: BLE001
            error.append(e)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join()
    if error:
        raise error[0]


@cli.command("import-rules")
def import_rules(
    xlsx_path: Path = typer.Argument(..., exists=True, file_okay=True, dir_okay=False),
    version_label: str = "v1",
) -> None:
    """从 Excel 一次性导入全部岗位规则并发布为 active 版本."""
    rules = import_workbook(xlsx_path)
    _run_async(lambda: _apply_rules(rules, version_label, xlsx_path.name))


if __name__ == "__main__":
    cli()
