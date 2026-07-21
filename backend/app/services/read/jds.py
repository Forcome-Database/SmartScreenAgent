from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import JD, RuleVersion
from backend.app.schemas.read import (
    JDDetail,
    JDItem,
    RuleDiffChange,
    RuleDiffResponse,
    RuleVersionItem,
)
from backend.app.services.read.pagination import Page
from backend.app.services.read.rule_diff import diff_schemas


async def _active_version(db: AsyncSession, jd: JD) -> RuleVersion | None:
    if not jd.active_rule_version_id:
        return None
    return (
        await db.execute(select(RuleVersion).where(RuleVersion.id == jd.active_rule_version_id))
    ).scalar_one_or_none()


async def list_jds(db: AsyncSession, status: str | None, page: Page) -> tuple[list[JDItem], int]:
    base = select(JD)
    if status is not None:
        base = base.where(JD.status == status)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    jds = (
        await db.execute(base.order_by(JD.code).offset(page.offset).limit(page.page_size))
    ).scalars().all()
    items = []
    for jd in jds:
        active = await _active_version(db, jd)
        items.append(
            JDItem(
                code=jd.code,
                name=jd.name,
                status=jd.status,
                active_rule_version=active.version if active else None,
            )
        )
    return items, total


async def get_jd_detail(db: AsyncSession, code: str) -> JDDetail | None:
    jd = (await db.execute(select(JD).where(JD.code == code))).scalar_one_or_none()
    if jd is None:
        return None
    active = await _active_version(db, jd)
    return JDDetail(
        code=jd.code,
        name=jd.name,
        description=jd.description,
        status=jd.status,
        active_rule_version=(
            {
                "id": active.id,
                "version": active.version,
                "published_at": active.published_at.isoformat(),
            }
            if active
            else None
        ),
    )


async def list_rule_versions(
    db: AsyncSession, code: str, page: Page
) -> tuple[list[RuleVersionItem], int] | None:
    jd = (await db.execute(select(JD).where(JD.code == code))).scalar_one_or_none()
    if jd is None:
        return None
    base = select(RuleVersion).where(RuleVersion.jd_id == jd.id)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    versions = (
        await db.execute(
            base.order_by(RuleVersion.published_at.desc()).offset(page.offset).limit(page.page_size)
        )
    ).scalars().all()
    items = [
        RuleVersionItem(
            id=v.id,
            version=v.version,
            published_at=v.published_at,
            published_by_user_id=v.published_by_user_id,
            notes=v.notes,
            golden_set_metrics=v.golden_set_metrics,
            is_active=(v.id == jd.active_rule_version_id),
        )
        for v in versions
    ]
    return items, total


async def rule_version_diff(
    db: AsyncSession, code: str, from_version: str, to_version: str
) -> RuleDiffResponse | None:
    jd = (await db.execute(select(JD).where(JD.code == code))).scalar_one_or_none()
    if jd is None:
        return None

    async def _load(version: str) -> RuleVersion | None:
        return (
            await db.execute(
                select(RuleVersion).where(
                    RuleVersion.jd_id == jd.id, RuleVersion.version == version
                )
            )
        ).scalar_one_or_none()

    a, b = await _load(from_version), await _load(to_version)
    if a is None or b is None:
        return None
    changes = [RuleDiffChange(**c) for c in diff_schemas(a.schema_json, b.schema_json)]
    return RuleDiffResponse(
        jd_code=code, from_version=from_version, to_version=to_version, changes=changes
    )
