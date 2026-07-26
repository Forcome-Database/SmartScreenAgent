from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx

from backend.app.config import get_settings
from backend.app.services.dingtalk.oauth import DingTalkCorpTokenClient, DingTalkOAuthError

TOKEN_URL = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
APP_SECRET = "super-secret-app-credential"


@pytest.fixture(autouse=True)
def _reset_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def _configured(monkeypatch) -> None:
    monkeypatch.setenv("DINGTALK_APP_KEY", "test-app-key")
    monkeypatch.setenv("DINGTALK_APP_SECRET", APP_SECRET)


class _Clock:
    """A hand-cranked clock, so expiry is tested without ever sleeping."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@respx.mock
async def test_the_corp_token_is_minted_from_the_app_credentials(_configured) -> None:
    captured: dict[str, str] = {}

    def mint(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"accessToken": "corp-token-1", "expireIn": 7200})

    respx.post(TOKEN_URL).mock(side_effect=mint)

    token = await DingTalkCorpTokenClient().get_token()

    assert token == "corp-token-1"
    # Field names verified against the authoritative OAS
    # (/paths/_v1.0_oauth2_accessToken.json): appKey/appSecret, NOT the
    # clientId/clientSecret pair the user-token endpoint takes.
    assert captured == {"appKey": "test-app-key", "appSecret": APP_SECRET}


@respx.mock
async def test_a_cached_token_is_reused_until_the_refresh_window_opens(_configured) -> None:
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "corp-token-1", "expireIn": 7200})
    )
    clock = _Clock()
    client = DingTalkCorpTokenClient(clock=clock)

    assert await client.get_token() == "corp-token-1"
    clock.now = 6899.0
    assert await client.get_token() == "corp-token-1"

    # Minting per request is what the OAS warns about verbatim:
    # "不能频繁调用 gettoken 接口，否则会受到频率拦截。"
    assert route.call_count == 1


@respx.mock
async def test_the_token_is_reminted_once_it_nears_expiry(_configured) -> None:
    respx.post(TOKEN_URL).mock(
        side_effect=[
            httpx.Response(200, json={"accessToken": "corp-token-1", "expireIn": 7200}),
            httpx.Response(200, json={"accessToken": "corp-token-2", "expireIn": 7200}),
        ]
    )
    clock = _Clock()
    client = DingTalkCorpTokenClient(clock=clock)

    assert await client.get_token() == "corp-token-1"
    # 7200 - 300 skew: the cached token is abandoned before it can expire
    # mid-flight on a request already in the air.
    clock.now = 6900.0
    assert await client.get_token() == "corp-token-2"


@respx.mock
async def test_concurrent_callers_mint_exactly_one_token(_configured) -> None:
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "corp-token-1", "expireIn": 7200})
    )
    client = DingTalkCorpTokenClient(clock=_Clock())

    tokens = await asyncio.gather(*(client.get_token() for _ in range(5)))

    assert tokens == ["corp-token-1"] * 5
    assert route.call_count == 1


@respx.mock
async def test_a_rejected_credential_raises_without_leaking_the_secret(_configured) -> None:
    # The 400 the OAS documents for this endpoint.
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"code": "invalidClientIdOrSecret"})
    )

    with pytest.raises(DingTalkOAuthError) as caught:
        await DingTalkCorpTokenClient().get_token()

    assert APP_SECRET not in str(caught.value)


@respx.mock
async def test_a_malformed_token_response_raises(_configured) -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"expireIn": 7200}))

    with pytest.raises(DingTalkOAuthError):
        await DingTalkCorpTokenClient().get_token()


async def test_unconfigured_credentials_fail_before_any_request(monkeypatch) -> None:
    monkeypatch.setenv("DINGTALK_APP_KEY", "")
    monkeypatch.setenv("DINGTALK_APP_SECRET", "")

    # No respx mock is installed: a request here would be a real network call,
    # so this also proves nothing leaves the process.
    with pytest.raises(DingTalkOAuthError):
        await DingTalkCorpTokenClient().get_token()
