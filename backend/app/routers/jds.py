from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db
from backend.app.deps import require_roles
from backend.app.models import User
from backend.app.schemas.read import JDDetail, JDList, RuleDiffResponse, RuleVersionList
from backend.app.services.read.jds import (
    get_jd_detail,
    list_jds,
    list_rule_versions,
    rule_version_diff,
)
from backend.app.services.read.pagination import Page, page_params

router = APIRouter(prefix="/api/v1/jds", tags=["jds"])
READ_ROLES = ("hr", "hr_lead", "admin")


def _not_found(resource: str) -> HTTPException:
    return HTTPException(
        status_code=404, detail={"code": "not_found", "message": f"{resource} not found"}
    )


@router.get("", response_model=JDList)
async def jds_list(
    status: str | None = None,
    page: Page = Depends(page_params),
    db: AsyncSession = Depends(get_db),
    _u: User = Depends(require_roles(*READ_ROLES)),
) -> JDList:
    items, total = await list_jds(db, status, page)
    return JDList(items=items, page=page.page, page_size=page.page_size, total=total)


@router.get("/{code}", response_model=JDDetail)
async def jd_detail(
    code: str,
    db: AsyncSession = Depends(get_db),
    _u: User = Depends(require_roles(*READ_ROLES)),
) -> JDDetail:
    detail = await get_jd_detail(db, code)
    if detail is None:
        raise _not_found("JD")
    return detail


@router.get("/{code}/rule-versions", response_model=RuleVersionList)
async def rule_versions(
    code: str,
    page: Page = Depends(page_params),
    db: AsyncSession = Depends(get_db),
    _u: User = Depends(require_roles(*READ_ROLES)),
) -> RuleVersionList:
    result = await list_rule_versions(db, code, page)
    if result is None:
        raise _not_found("JD")
    items, total = result
    return RuleVersionList(items=items, page=page.page, page_size=page.page_size, total=total)


@router.get(
    "/{code}/rule-versions/{from_version}/diff/{to_version}",
    response_model=RuleDiffResponse,
)
async def rule_diff(
    code: str,
    from_version: str,
    to_version: str,
    db: AsyncSession = Depends(get_db),
    _u: User = Depends(require_roles(*READ_ROLES)),
) -> RuleDiffResponse:
    result = await rule_version_diff(db, code, from_version, to_version)
    if result is None:
        raise _not_found("JD or rule version")
    return result
