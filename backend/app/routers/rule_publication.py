from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db
from backend.app.deps import require_roles
from backend.app.models import JD, RuleVersion, User
from backend.app.schemas.rule_publication import CreateDraftRequest, RuleVersionRef
from backend.app.services.rule_publication import (
    InvalidRuleSchema,
    NotADraft,
    RegressionNotRecorded,
    VersionExists,
    create_draft,
    publish_draft,
)

router = APIRouter(prefix="/api/v1/jds", tags=["rule-publication"])
WRITE_ROLES = ("hr_lead", "admin")


async def _load_jd(db: AsyncSession, code: str) -> JD:
    jd = (await db.execute(select(JD).where(JD.code == code))).scalar_one_or_none()
    if jd is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "JD not found"},
        )
    return jd


async def _load_version(db: AsyncSession, jd: JD, version: str) -> RuleVersion:
    rule_version = (
        await db.execute(
            select(RuleVersion).where(
                RuleVersion.jd_id == jd.id,
                RuleVersion.version == version,
            )
        )
    ).scalar_one_or_none()
    if rule_version is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "rule version not found"},
        )
    return rule_version


@router.post("/{code}/rule-versions", response_model=RuleVersionRef)
async def create(
    code: str,
    payload: CreateDraftRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_roles(*WRITE_ROLES)),
) -> RuleVersionRef:
    jd = await _load_jd(db, code)
    try:
        draft = await create_draft(
            db,
            jd=jd,
            schema_json=payload.rule_schema,
            notes=payload.notes,
        )
    except InvalidRuleSchema as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_rule_schema", "message": "规则 schema 不合法"},
        ) from exc
    except VersionExists as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "version_exists", "message": "该版本号已存在"},
        ) from exc
    return RuleVersionRef(
        id=draft.id,
        version=draft.version,
        status=draft.status,
        notes=draft.notes,
    )


@router.post("/{code}/rule-versions/{version}/publish", response_model=RuleVersionRef)
async def publish(
    code: str,
    version: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> RuleVersionRef:
    jd = await _load_jd(db, code)
    draft = await _load_version(db, jd, version)
    try:
        published = await publish_draft(
            db,
            jd=jd,
            draft=draft,
            publisher_id=user.id,
        )
    except NotADraft as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "not_a_draft", "message": "只能发布草稿版本"},
        ) from exc
    except RegressionNotRecorded as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "regression_not_recorded",
                "message": "发布前必须先运行 What-If 评估",
            },
        ) from exc
    return RuleVersionRef(
        id=published.id,
        version=published.version,
        status=published.status,
        notes=published.notes,
    )
