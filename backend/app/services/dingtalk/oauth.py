from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from backend.app.config import get_settings


@dataclass
class DingTalkUserInfo:
    """unionId 是钉钉跨应用稳定标识；openId 仅 app 域内稳定。
    用 union_id 作为我们 User.dingtalk_userid 的来源。
    Source: docs/specs/research/dingtalk-oauth.md
    """

    union_id: str
    open_id: str
    display_name: str


class DingTalkOAuthError(RuntimeError):
    pass


class DingTalkOAuthClient:
    """钉钉 OAuth 客户端。端点/字段名来自 docs/specs/research/dingtalk-oauth.md（OAS 实读）。"""

    USER_TOKEN_URL = "https://api.dingtalk.com/v1.0/oauth2/userAccessToken"
    USER_INFO_URL = "https://api.dingtalk.com/v1.0/contact/users/me"
    ACCESS_TOKEN_HEADER = "x-acs-dingtalk-access-token"

    def __init__(self) -> None:
        self.settings = get_settings()

    async def _get_user_access_token(self, auth_code: str) -> str:
        payload = {
            "clientId": self.settings.DINGTALK_APP_KEY,
            "clientSecret": self.settings.DINGTALK_APP_SECRET,
            "code": auth_code,
            "grantType": "authorization_code",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(self.USER_TOKEN_URL, json=payload)
            r.raise_for_status()
            return r.json()["accessToken"]

    async def _fetch_user_info(self, user_access_token: str) -> DingTalkUserInfo:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                self.USER_INFO_URL,
                headers={self.ACCESS_TOKEN_HEADER: user_access_token},
            )
            r.raise_for_status()
            j = r.json()
            return DingTalkUserInfo(
                union_id=j.get("unionId", ""),
                open_id=j.get("openId", ""),
                display_name=j.get("nick", ""),
            )

    async def exchange_auth_code(self, auth_code: str) -> DingTalkUserInfo:
        try:
            token = await self._get_user_access_token(auth_code)
            info = await self._fetch_user_info(token)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise DingTalkOAuthError("DingTalk OAuth exchange failed") from exc
        if not info.union_id or not info.display_name:
            raise DingTalkOAuthError("DingTalk OAuth response missing user identity")
        return info


class DingTalkCorpTokenClient:
    """企业内部应用 (corp/app-level) accessToken, for server-to-server calls.

    VERIFIED: endpoint, request fields and response fields are all read from the
    authoritative DingTalk OAS, `/paths/_v1.0_oauth2_accessToken.json` (summary
    "获取企业内部应用的accessToken"), re-read on 2026-07-27. Request body
    `{appKey, appSecret}`; response `{accessToken, expireIn}` with `expireIn` in
    seconds. Note the field names differ from `DingTalkOAuthClient`'s user-token
    call, which takes `clientId`/`clientSecret` — do not conflate them.

    The token is cached in process. The OAS says verbatim "不能频繁调用 gettoken
    接口，否则会受到频率拦截", so minting one per request would eventually get
    the app throttled. The clock is injected so expiry is testable without
    sleeping.
    """

    CORP_TOKEN_URL = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
    # Abandon the cached token this long before its declared expiry, so it can
    # never expire mid-flight on a request already in the air.
    REFRESH_SKEW_SECONDS = 300.0
    REQUEST_TIMEOUT_SECONDS = 10.0

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self.settings = get_settings()
        self._clock = clock if clock is not None else time.monotonic
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        # The lock coalesces concurrent callers onto one mint. Without it, N
        # workers waking together each pay a request against a throttled
        # endpoint to obtain the same token.
        async with self._lock:
            if self._token is not None and self._clock() < self._expires_at:
                return self._token
            token, expires_in = await self._mint()
            self._token = token
            self._expires_at = self._clock() + max(expires_in - self.REFRESH_SKEW_SECONDS, 0.0)
            return token

    async def _mint(self) -> tuple[str, float]:
        if not self.settings.DINGTALK_APP_KEY or not self.settings.DINGTALK_APP_SECRET:
            raise DingTalkOAuthError("DingTalk app credentials are not configured")
        payload = {
            "appKey": self.settings.DINGTALK_APP_KEY,
            "appSecret": self.settings.DINGTALK_APP_SECRET,
        }
        try:
            async with httpx.AsyncClient(timeout=self.REQUEST_TIMEOUT_SECONDS) as client:
                r = await client.post(self.CORP_TOKEN_URL, json=payload)
                r.raise_for_status()
                body = r.json()
            token = body["accessToken"]
            expires_in = float(body["expireIn"])
        except (httpx.HTTPError, httpx.InvalidURL, KeyError, TypeError, ValueError) as exc:
            # Fixed message: the request body carries the app secret and the
            # response body carries the token. Neither may reach a log or a
            # traceback line.
            raise DingTalkOAuthError("DingTalk corp access token request failed") from exc
        if not isinstance(token, str) or not token:
            raise DingTalkOAuthError("DingTalk corp access token response is empty")
        return token, expires_in
