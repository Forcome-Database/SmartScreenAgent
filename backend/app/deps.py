from collections.abc import Awaitable, Callable

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db
from backend.app.models import User
from backend.app.security.jwt import decode_token

_BEARER_HEADERS = {"WWW-Authenticate": "Bearer"}


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers=_BEARER_HEADERS,
    )


async def get_current_user(
    authorization: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not authorization.startswith("Bearer "):
        raise _unauthorized("Missing Bearer token")
    token = authorization[len("Bearer ") :]
    try:
        payload = decode_token(token)
    except ValueError as exc:
        raise _unauthorized("Invalid token") from exc
    subject = payload.get("sub")
    if isinstance(subject, bool) or not isinstance(subject, (str, int)):
        raise _unauthorized("Invalid token")
    try:
        user_id = int(subject)
    except (TypeError, ValueError) as exc:
        raise _unauthorized("Invalid token") from exc
    if user_id <= 0:
        raise _unauthorized("Invalid token")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise _unauthorized("User not found")
    return user


def require_roles(*roles: str) -> Callable[..., Awaitable[User]]:
    allowed = frozenset(roles)

    async def _dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden",
            )
        return user

    return _dependency
