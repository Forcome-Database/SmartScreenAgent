from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from backend.app import deps
from backend.app.security.jwt import create_access_token


def _db_returning(user):
    result = SimpleNamespace(scalar_one_or_none=lambda: user)
    return SimpleNamespace(execute=AsyncMock(return_value=result))


@pytest.mark.asyncio
async def test_get_current_user_requires_bearer_token():
    with pytest.raises(HTTPException) as exc_info:
        await deps.get_current_user(authorization="", db=_db_returning(None))
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Missing Bearer token"
    assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}


@pytest.mark.asyncio
async def test_get_current_user_normalizes_decode_failure(monkeypatch):
    def fail_decode(_token: str):
        raise ValueError("signature details")

    monkeypatch.setattr(deps, "decode_token", fail_decode)
    with pytest.raises(HTTPException) as exc_info:
        await deps.get_current_user(
            authorization="Bearer invalid", db=_db_returning(None)
        )
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid token"
    assert "signature" not in str(exc_info.value.detail)


@pytest.mark.asyncio
@pytest.mark.parametrize("subject", [None, "", "abc", "0", -1, True])
async def test_get_current_user_rejects_invalid_subject(monkeypatch, subject):
    monkeypatch.setattr(deps, "decode_token", lambda _token: {"sub": subject})
    with pytest.raises(HTTPException) as exc_info:
        await deps.get_current_user(
            authorization="Bearer token", db=_db_returning(None)
        )
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid token"


@pytest.mark.asyncio
async def test_get_current_user_rejects_missing_user(monkeypatch):
    monkeypatch.setattr(deps, "decode_token", lambda _token: {"sub": "42"})
    with pytest.raises(HTTPException) as exc_info:
        await deps.get_current_user(
            authorization="Bearer token", db=_db_returning(None)
        )
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "User not found"


@pytest.mark.asyncio
async def test_get_current_user_normalizes_expired_token():
    token = create_access_token({"sub": "42", "role": "hr"}, expires_hours=-1)
    with pytest.raises(HTTPException) as exc_info:
        await deps.get_current_user(
            authorization=f"Bearer {token}", db=_db_returning(None)
        )
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid token"


@pytest.mark.asyncio
async def test_require_roles_uses_database_user_role():
    dependency = deps.require_roles("hr", "admin")
    user = SimpleNamespace(role="hr")
    assert await dependency(user) is user

    with pytest.raises(HTTPException) as exc_info:
        await dependency(SimpleNamespace(role="dept_head"))
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Forbidden"
