from unittest.mock import AsyncMock
import pytest
from backend.app.services.dingtalk.oauth import DingTalkOAuthClient, DingTalkUserInfo


@pytest.mark.asyncio
async def test_exchange_code_for_user(monkeypatch):
    client = DingTalkOAuthClient()
    monkeypatch.setattr(client, "_get_user_access_token", AsyncMock(return_value="ut-fake"))
    monkeypatch.setattr(
        client,
        "_fetch_user_info",
        AsyncMock(return_value=DingTalkUserInfo(union_id="u-stable-1", open_id="o-app-1", display_name="Leo")),
    )
    info = await client.exchange_auth_code("auth-code-xxx")
    assert info.union_id == "u-stable-1"
    assert info.display_name == "Leo"


@pytest.mark.asyncio
async def test_exchange_code_network_error(monkeypatch):
    """网络错误应抛出供路由层捕获并返 400。"""
    import httpx
    client = DingTalkOAuthClient()
    monkeypatch.setattr(
        client,
        "_get_user_access_token",
        AsyncMock(side_effect=httpx.HTTPStatusError("400", request=None, response=None)),
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.exchange_auth_code("bad-code")
