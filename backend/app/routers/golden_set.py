# backend/app/routers/golden_set.py
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.database import get_db
from backend.app.deps import require_roles
from backend.app.models import User
from backend.app.schemas.golden_set import (
    GoldenImportError,
    GoldenImportResult,
    GoldenSetItem,
    GoldenSetList,
)
from backend.app.services.golden_set import (
    GoldenImportTooLarge,
    InvalidCSV,
    RowError,
    import_golden_set,
    list_golden_set,
    parse_golden_csv,
)
from backend.app.services.read.pagination import Page, page_params

router = APIRouter(prefix="/api/v1", tags=["golden-set"])
IMPORT_ROLES = ("hr_lead", "admin")
READ_ROLES = ("hr", "hr_lead", "admin")


def _err(e: RowError) -> GoldenImportError:
    return GoldenImportError(
        row=e.row, candidate_id=e.candidate_id, jd_code=e.jd_code, reason=e.reason
    )


@router.post("/golden-set/import", response_model=GoldenImportResult)
async def import_golden(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*IMPORT_ROLES)),
) -> GoldenImportResult:
    settings = get_settings()
    content = await file.read()
    try:
        parsed, fmt_errors = parse_golden_csv(content, max_rows=settings.GOLDEN_IMPORT_MAX_ROWS)
    except InvalidCSV as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_csv", "message": "无法解析 CSV 或缺少必需表头"},
        ) from exc
    except GoldenImportTooLarge as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "golden_import_too_large",
                "message": f"单次导入不超过 {settings.GOLDEN_IMPORT_MAX_ROWS} 行",
            },
        ) from exc
    created, updated, db_errors = await import_golden_set(db, parsed=parsed, importer_id=user.id)
    all_errors = sorted([*fmt_errors, *db_errors], key=lambda e: e.row)
    return GoldenImportResult(
        total=len(parsed) + len(fmt_errors),
        created=created,
        updated=updated,
        errors=[_err(e) for e in all_errors],
    )


@router.get("/golden-set", response_model=GoldenSetList)
async def list_entries(
    jd_code: str | None = None,
    page: Page = Depends(page_params),
    db: AsyncSession = Depends(get_db),
    _u: User = Depends(require_roles(*READ_ROLES)),
) -> GoldenSetList:
    rows, total = await list_golden_set(db, jd_code=jd_code, page=page)
    items = [
        GoldenSetItem(
            id=g.id,
            candidate_id=g.candidate_id,
            jd_code=code,
            label=g.label,
            imported_at=g.imported_at,
            imported_by_display_name=name,
        )
        for g, code, name in rows
    ]
    return GoldenSetList(items=items, page=page.page, page_size=page.page_size, total=total)
