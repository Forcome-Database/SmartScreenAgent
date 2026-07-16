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
