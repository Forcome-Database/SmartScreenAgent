from __future__ import annotations

import hmac

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.models import User


class McpUnauthorized(Exception):
    """The presented MCP token is absent or wrong."""


async def resolve_mcp_user(db: AsyncSession, token: str) -> User:
    """Map the shared MCP token to its service user.

    Compared with `hmac.compare_digest` so a wrong token cannot be recovered
    by timing, and on the encoded forms because that function raises
    `TypeError` for a `str` carrying non-ASCII characters — the token comes
    from a caller-chosen HTTP header, and every wrong token has to leave by the
    same door. The returned user carries the ceiling role; every downstream
    check uses it exactly as it would a human's role.
    """
    settings = get_settings()
    expected = settings.MCP_SERVICE_TOKEN
    if not expected or not token or not hmac.compare_digest(token.encode(), expected.encode()):
        raise McpUnauthorized("invalid mcp token")
    user = (
        await db.execute(select(User).where(User.role == settings.MCP_SERVICE_ROLE))
    ).scalars().first()
    if user is None:
        raise McpUnauthorized("mcp service user is not provisioned")
    return user
