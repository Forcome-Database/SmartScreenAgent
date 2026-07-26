from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import AsyncSessionLocal, get_db
from backend.app.deps import require_roles
from backend.app.models import User
from backend.app.schemas.quality import (
    QualityReleaseDetail,
    QualityReleaseList,
    QualityReleasePreview,
)
from backend.app.services.quality.releases import (
    ActiveRuleMissing,
    GoldenSetEmpty,
    InvalidActiveRule,
    InvalidReleaseWindow,
    ReleaseInputChanged,
    ReleaseRequest,
    ReleaseTransactionConflict,
    ReleaseWindowTooLarge,
    create_release,
    get_release,
    list_releases,
    preview_release,
)
from backend.app.services.read.pagination import Page, page_params

router = APIRouter(prefix="/api/v1", tags=["quality"])
WRITE_ROLES = ("hr_lead", "admin")
READ_ROLES = ("hr", "hr_lead", "admin")

_INVALID_WINDOW = {
    "code": "invalid_release_window",
    "message": "Release window must be timezone-aware, ordered, and end no later than now",
}
_WINDOW_TOO_LARGE = {
    "code": "release_window_too_large",
    "message": "Release window cannot exceed 365 days",
}
_PRECONDITIONS: tuple[tuple[type[Exception], str, str], ...] = (
    (GoldenSetEmpty, "golden_set_empty", "No golden labels for the selected JDs"),
    (ActiveRuleMissing, "active_rule_missing", "A selected JD has no active rule version"),
    (InvalidActiveRule, "invalid_active_rule", "A selected JD has an invalid active rule"),
    (ReleaseInputChanged, "release_input_changed", "Release inputs changed since preview"),
)


class ReleaseBody(BaseModel):
    window_start: datetime | None = None
    window_end: datetime | None = None
    jd_codes: list[str] | None = None
    expected_input_fingerprint: str | None = None

    def to_request(self) -> ReleaseRequest:
        return ReleaseRequest(
            window_start=self.window_start,
            window_end=self.window_end,
            jd_codes=self.jd_codes,
            expected_input_fingerprint=self.expected_input_fingerprint,
        )


def _translate(exc: Exception) -> HTTPException:
    """Map a domain failure onto its stable, documented HTTP contract."""
    if isinstance(exc, InvalidReleaseWindow):
        return HTTPException(status_code=422, detail=_INVALID_WINDOW)
    if isinstance(exc, ReleaseWindowTooLarge):
        return HTTPException(status_code=422, detail=_WINDOW_TOO_LARGE)
    if isinstance(exc, ReleaseTransactionConflict):
        return HTTPException(
            status_code=503,
            detail={
                "code": "release_transaction_conflict",
                "message": "Release creation could not complete; retry",
            },
        )
    for kind, code, message in _PRECONDITIONS:
        if isinstance(exc, kind):
            return HTTPException(
                status_code=409, detail={"code": code, "message": message}
            )
    raise exc


@router.post("/quality/releases/preview", response_model=QualityReleasePreview)
async def preview(
    body: ReleaseBody,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_roles(*READ_ROLES)),
) -> QualityReleasePreview:
    try:
        payload = await preview_release(db, body.to_request(), datetime.now(UTC))
    except Exception as exc:
        raise _translate(exc) from exc
    return QualityReleasePreview.model_validate(payload)


@router.post("/quality/releases", response_model=QualityReleaseDetail, status_code=201)
async def create(
    body: ReleaseBody,
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> QualityReleaseDetail:
    try:
        payload = await create_release(
            AsyncSessionLocal, body.to_request(), user.id, datetime.now(UTC)
        )
    except Exception as exc:
        raise _translate(exc) from exc
    return QualityReleaseDetail.model_validate(payload)


@router.get("/quality/releases", response_model=QualityReleaseList)
async def list_all(
    jd_code: str | None = Query(None),
    status: str | None = Query(None),
    page: Page = Depends(page_params),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_roles(*READ_ROLES)),
) -> QualityReleaseList:
    items, total = await list_releases(
        db,
        offset=page.offset,
        limit=page.page_size,
        jd_code=jd_code,
        status=status,
    )
    return QualityReleaseList.model_validate(
        {"items": items, "page": page.page, "page_size": page.page_size, "total": total}
    )


@router.get("/quality/releases/{release_id}", response_model=QualityReleaseDetail)
async def detail(
    release_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_roles(*READ_ROLES)),
) -> QualityReleaseDetail:
    payload = await get_release(db, release_id)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "release_not_found", "message": "Quality release not found"},
        )
    return QualityReleaseDetail.model_validate(payload)
