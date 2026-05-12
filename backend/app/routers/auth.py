from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db
from backend.app.models import User
from backend.app.security.jwt import create_access_token
from backend.app.services.dingtalk.oauth import DingTalkOAuthClient

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    auth_code: str


class LoginResponse(BaseModel):
    token: str
    display_name: str
    role: str


@router.post("/dingtalk/login", response_model=LoginResponse)
async def dingtalk_login(req: LoginRequest, db: AsyncSession = Depends(get_db)) -> LoginResponse:
    client = DingTalkOAuthClient()
    try:
        info = await client.exchange_auth_code(req.auth_code)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"DingTalk OAuth failed: {e}") from e

    # union_id 是跨钉钉应用稳定的；用它做我们 users.dingtalk_userid 主标识
    # Try to insert; if another concurrent login already created the row, ignore and re-select.
    stmt = (
        pg_insert(User)
        .values(dingtalk_userid=info.union_id, display_name=info.display_name, role="hr")
        .on_conflict_do_nothing(index_elements=["dingtalk_userid"])
    )
    try:
        await db.execute(stmt)
        await db.commit()
    except IntegrityError:
        await db.rollback()

    # Now SELECT — guaranteed to find a row (either ours or the concurrent one's).
    result = await db.execute(select(User).where(User.dingtalk_userid == info.union_id))
    user = result.scalar_one()

    token = create_access_token({"sub": str(user.id), "role": user.role})
    return LoginResponse(token=token, display_name=user.display_name, role=user.role)
