# DingTalk OAuth 2026 Flow — Research Notes

**Task:** P1 Task 0.2 — confirm the current DingTalk one-click login + H5/mobile 免登 (silent-login) flow, endpoints, and signature requirements before implementing `DingTalkOAuthClient` in Task 11.

**Researched on:** 2026-05-12

**Primary source of truth:** the `dingtalk-api` MCP (OpenAPI Spec downloaded 2026-05-12; cited as `oas-ref:<path>` below).

---

## TL;DR

- **Three discrete tokens** are used together: (a) `userAccessToken` — represents a logged-in user, scope-limited; (b) `accessToken` (corp-level) — represents the app itself, called server-to-server; (c) `jsapiTicket` — only needed if we additionally call `dd.config(...)` for advanced JSAPI permissions (NOT needed for the basic `requestAuthCode`免登 flow).
- The **OAuth 2.0 authorize URL** is `https://login.dingtalk.com/oauth2/auth` (PC scan-code login). The **server-side code-exchange endpoint** is `POST https://api.dingtalk.com/v1.0/oauth2/userAccessToken`.
- Both return / accept `accessToken` (note the camelCase field names — this is DingTalk's "new" v1.0 API; the legacy `/sns/getuserinfo_bycode` returns snake_case and is deprecated for new apps).
- **For our app (内部企业 H5 微应用)** the simplest免登 path is: frontend calls `dd.runtime.permission.requestAuthCode({corpId})` → backend exchanges `auth_code` via `/v1.0/oauth2/userAccessToken` (grantType=`authorization_code`) → backend calls `GET /v1.0/contact/users/me` with header `x-acs-dingtalk-access-token: <userAccessToken>` to retrieve `unionId` / `openId` / `nick` / `mobile`.
- `userAccessToken` TTL = **7200 s** (`expireIn` field, oas-ref `/paths/_v1.0_oauth2_userAccessToken.json`). `refreshToken` TTL = **30 days** (description text in the same OAS path).
- **Decision: persist `unionId`** in `users.dingtalk_userid` (cross-app stable identifier). See §6.
- **`appSecret` must NEVER leave the backend.** Only the *frontend `auth_code`* is exchanged from the browser via our own backend — the browser never touches `clientSecret`.

---

## 1. OAuth Flow Diagrams

### 1.1 PC scan-code login (扫码登录)

```
+----------+              +----------------------+              +------------+              +---------------+
|  User    |              |  Our Frontend (PC)   |              |  DingTalk  |              | Our Backend   |
|  Browser |              |  (resume-screen SPA) |              |  Auth Page |              |  (FastAPI)    |
+----------+              +----------------------+              +------------+              +---------------+
     |                              |                                |                              |
     |  1. Click "钉钉登录"          |                                |                              |
     |----------------------------->|                                |                              |
     |                              |  2. Redirect to                |                              |
     |                              |     https://login.dingtalk.com |                              |
     |                              |     /oauth2/auth?              |                              |
     |                              |       client_id=<AppKey>       |                              |
     |                              |       &redirect_uri=<our cb>   |                              |
     |                              |       &response_type=code      |                              |
     |                              |       &scope=openid+corpid     |                              |
     |                              |       &state=<csrf>            |                              |
     |                              |       &prompt=consent          |                              |
     |                              |------------------------------->|                              |
     |  3. Show QR code; user scans + confirms in DingTalk mobile    |                              |
     |<--------------------------------------------------------------|                              |
     |                                                               |                              |
     |  4. 302 redirect to <our cb>?authCode=xxx&state=<csrf>        |                              |
     |---------------------------------------------------------------+----------------------------->|
     |                                                                                              |
     |                              5. Backend: POST /v1.0/oauth2/userAccessToken                   |
     |                                 body: { clientId, clientSecret, code, grantType }            |
     |                                                                                              |
     |                              6. Returns { accessToken, refreshToken, expireIn, corpId }      |
     |                                                                                              |
     |                              7. Backend: GET /v1.0/contact/users/me                          |
     |                                 header: x-acs-dingtalk-access-token: <accessToken>           |
     |                                                                                              |
     |                              8. Returns { unionId, openId, nick, avatarUrl, mobile, ... }    |
     |                                                                                              |
     |                              9. Backend upserts users row by unionId,                        |
     |                                 issues our own JWT session cookie                            |
     |  10. Set-Cookie: session=<jwt>, redirect to app                                              |
     |<---------------------------------------------------------------------------------------------|
     |                                                                                              |
```

### 1.2 In-DingTalk mobile / PC client 免登 (silent login via JSAPI)

```
+--------------+              +-----------------------+              +---------------+
| User opens   |              |  Our Frontend running |              | Our Backend   |
| our app in   |              |  inside DingTalk      |              | (FastAPI)     |
| DingTalk     |              |  WebView (H5 微应用)  |              |               |
+--------------+              +-----------------------+              +---------------+
       |                                  |                                  |
       |  1. WebView loads our SPA URL    |                                  |
       |--------------------------------->|                                  |
       |                                  |                                  |
       |                  2. `dd.ready(() => dd.runtime.permission           |
       |                       .requestAuthCode({ corpId,                    |
       |                          onSuccess: info => info.code,              |
       |                          onFail: err => ... }))`                    |
       |                                  |                                  |
       |                  3. DingTalk client returns                         |
       |                       { code: "<short-lived authCode>" }            |
       |                                  |                                  |
       |                                  |  4. POST /api/auth/dingtalk      |
       |                                  |     { code: <authCode> }         |
       |                                  |--------------------------------->|
       |                                  |                                  |
       |                                  |  5. Backend: same code-exchange  |
       |                                  |     + users/me as in §1.1 (5-8)  |
       |                                  |                                  |
       |                                  |  6. { sessionToken, user }       |
       |                                  |<---------------------------------|
       |                                  |                                  |
```

Note: the `auth_code` returned by `dd.runtime.permission.requestAuthCode` is the **same kind of `authorization_code`** consumed by `/v1.0/oauth2/userAccessToken` — i.e., the backend code path is identical for scan-code and in-client 免登. This is confirmed by the official PHP demo and Aliyun developer doc (cited below).

---

## 2. Backend Endpoints — Verbatim from OAS

All citations below reference the OAS downloaded from the `dingtalk-api` MCP (`refresh_project_oas_ce0216`) on 2026-05-12.

### 2.1 Exchange `auth_code` for `userAccessToken`

**OAS path:** `/v1.0/oauth2/userAccessToken` (oas-ref `/paths/_v1.0_oauth2_userAccessToken.json`).

| | |
|---|---|
| Method | `POST` |
| Host | `api.dingtalk.com` |
| Full URL | `https://api.dingtalk.com/v1.0/oauth2/userAccessToken` |
| Content-Type | `application/json` |
| Auth | **None** on this endpoint — `clientId` + `clientSecret` are sent in the JSON body (this is by design: this is the credential-exchange endpoint itself). |

**Request body schema (verbatim field names from OAS):**

| Field | Type | Required | OAS description (translated/condensed) |
|---|---|---|---|
| `clientId` | string | **yes** | "应用id。企业内部应用传 AppKey；第三方企业应用传 SuiteKey；第三方个人应用传 AppId" |
| `clientSecret` | string | yes (when grantType=`authorization_code`) | "应用密钥。企业内部应用传 AppSecret；第三方企业应用传 SuiteSecret；第三方个人应用传 AppSecret" |
| `code` | string | yes (when grantType=`authorization_code`) | "OAuth 2.0 临时授权码 authCode" |
| `refreshToken` | string | yes (when grantType=`refresh_token`) | "OAuth2.0 刷新令牌，过期时间 30 天" |
| `grantType` | string | yes | `authorization_code` (code→token) **or** `refresh_token` (refresh→token) |

**Response 200 body schema (verbatim field names):**

| Field | Type | Required | Notes |
|---|---|---|---|
| `accessToken` | string | yes | The user accessToken to put in the `x-acs-dingtalk-access-token` header for subsequent user-scoped API calls. |
| `refreshToken` | string | yes | 30-day refresh token. |
| `expireIn` | integer | yes | **Seconds.** Per the OAS description: "accessToken 的有效期为 7200 秒 (2 小时)，有效期内重复获取会返回相同结果并自动续期" — so expect `expireIn = 7200`. |
| `corpId` | string | yes (returned only when `scope` includes `corpid`) | The org corpId the user chose during the authorize step. |

**Example curl:**

```bash
curl -X POST https://api.dingtalk.com/v1.0/oauth2/userAccessToken \
  -H 'Content-Type: application/json' \
  -d '{
    "clientId": "<AppKey>",
    "clientSecret": "<AppSecret>",
    "code": "<authCode-from-frontend>",
    "grantType": "authorization_code"
  }'
```

**Refresh-token variant:**

```bash
curl -X POST https://api.dingtalk.com/v1.0/oauth2/userAccessToken \
  -H 'Content-Type: application/json' \
  -d '{
    "clientId": "<AppKey>",
    "clientSecret": "<AppSecret>",
    "refreshToken": "<previously-saved-refreshToken>",
    "grantType": "refresh_token"
  }'
```

### 2.2 Fetch the currently-authorized user (users/me)

**OAS path:** `/v1.0/contact/users/{unionId}` (oas-ref `/paths/_v1.0_contact_users_%7BunionId%7D.json`).

| | |
|---|---|
| Method | `GET` |
| Host | `api.dingtalk.com` |
| Full URL | `https://api.dingtalk.com/v1.0/contact/users/me` |
| Required header | `x-acs-dingtalk-access-token: <userAccessToken>` (verbatim header name from OAS `parameters[].name`) |

OAS path-parameter doc verbatim: *"unionId — 用户的 unionId。如需获取当前授权人的信息，unionId 参数可以传 me。"* So `…/users/me` is the canonical "current user" call.

**Response 200 body fields (verbatim):**

| Field | Type | Notes |
|---|---|---|
| `nick` | string | User's DingTalk display name. |
| `avatarUrl` | string | Avatar URL. |
| `mobile` | string | Mobile number (**requires extra "Contact.User.mobile" permission grant** in dev console — OAS description: "如果要获取用户手机号，需要在开发者后台申请个人手机号信息权限"). |
| `openId` | string | **App-scoped** identifier. Same physical user gets *different* openIds across different apps. |
| `unionId` | string | **Stable across all apps of the same DingTalk ISV / org developer**. This is what we want to persist. |
| `email` | string | User's personal email (only if granted). |
| `stateCode` | string | Country code of the mobile (e.g. `86`). |

OAS example payload (verbatim):
```json
{
  "nick": "zhangsan",
  "avatarUrl": "https://xxx",
  "mobile": "150xxxx9144",
  "openId": "123",
  "unionId": "z21HjQliSzpw0Yxxxx",
  "email": "zhangsan@alibaba-inc.com",
  "stateCode": "86"
}
```

**Example curl:**

```bash
curl -X GET https://api.dingtalk.com/v1.0/contact/users/me \
  -H "x-acs-dingtalk-access-token: <userAccessToken-from-2.1>"
```

> The OAS does **not** return `userid` (the corp-internal user ID) from `/v1.0/contact/users/me`. If we ever need the corp `userid`, we must additionally call **`POST /topapi/v2/user/getuserinfo`** with a corp `access_token` + a fresh auth code (oas-ref `/paths/_topapi_v2_user_getuserinfo.json`, summary "通过免登码获取用户信息"). For this project we do not need `userid` — `unionId` is sufficient.

### 2.3 (Optional, for server-to-server calls) Corp accessToken

If/when we need to call corp-scoped APIs (e.g., look up department, send a workflow notice) **on behalf of the app rather than a user**, we use:

**OAS path:** `/v1.0/oauth2/accessToken` (oas-ref `/paths/_v1.0_oauth2_accessToken.json`).

```bash
curl -X POST https://api.dingtalk.com/v1.0/oauth2/accessToken \
  -H 'Content-Type: application/json' \
  -d '{
    "appKey": "<AppKey>",
    "appSecret": "<AppSecret>"
  }'
```

Response: `{ "accessToken": "...", "expireIn": 7200 }`. Same 2-hour TTL with auto-renew within the window (OAS description). Cache aggressively — the OAS explicitly warns: *"不能频繁调用 gettoken 接口，否则会受到频率拦截。"*

This is **not** part of the login flow but is documented here for Task 11 completeness because the same `DingTalkOAuthClient` class will probably handle both. Note the *different field names* compared to §2.1: this one uses `appKey`/`appSecret`, not `clientId`/`clientSecret`. Don't conflate them.

---

## 3. Frontend JSAPI Snippet

Source: official Aliyun developer-community article ["Vue 项目对接钉钉企业内部 H5 微应用"](https://developer.aliyun.com/article/790736) (mirrors what the official `dd.runtime.permission.requestAuthCode` reference page documents — that page itself is a JS-rendered SPA shell that WebFetch/watercrawl cannot extract, but the API surface is universally documented in the Aliyun mirror and in the [`open-dingtalk/corp_demo_php`](https://github.com/open-dingtalk/corp_demo_php) reference repo).

**JSAPI method:** `dd.runtime.permission.requestAuthCode`

**Parameters:**

| Param | Required | Notes |
|---|---|---|
| `corpId` | yes | The org corpId. Frontend can hard-code this (it is not secret) OR fetch it from a backend `/api/config` endpoint. |
| `onSuccess` | yes | callback `({ code: string }) => void` — `info.code` is the short-lived `authCode`. |
| `onFail` | yes | callback `(err) => void`. |

**Callback success payload:** `{ code: "<authCode>" }` — note the key is `code`, not `authCode`. The backend then exchanges this `code` via the §2.1 endpoint exactly as if it had come from a redirect URL.

**Reference snippet:**

```js
import * as dd from 'dingtalk-jsapi'

export function getDingTalkAuthCode(corpId) {
  return new Promise((resolve, reject) => {
    if (dd.env.platform === 'notInDingTalk') {
      reject(new Error('not running inside DingTalk client'))
      return
    }
    dd.ready(() => {
      dd.runtime.permission.requestAuthCode({
        corpId,
        onSuccess: info => resolve(info.code),
        onFail:    err  => reject(err),
      })
    })
  })
}

// usage:
const code = await getDingTalkAuthCode(CORP_ID)
const { sessionToken } = await fetch('/api/auth/dingtalk', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ code }),
}).then(r => r.json())
```

### PC vs Mobile

The same `dd.runtime.permission.requestAuthCode` API is supported on both the DingTalk PC client and the DingTalk mobile app. **TBD:** the official "PC vs mobile differences" sub-table on the JSAPI overview page (`https://open.dingtalk.com/document/orgapp/jsapi-overview`) and the dedicated `dd.runtime.permission.requestAuthCode` reference page are both JS-rendered SPAs; neither WebFetch nor the headless `watercrawl` scraper could extract their rendered body during this research. The behavior described above is consistent across all reproduced references but a final per-platform compatibility check should be done with a live browser before shipping. (Rationale for the TBD: anti-hallucination — I could not directly verify the canonical doc text.)

### `dd.getAuthCode` vs `dd.runtime.permission.requestAuthCode`

These are **two different JSAPIs from two different SDK lineages**:

- `dd.runtime.permission.requestAuthCode(...)` — comes from the old H5 microapp JSSDK (`dingtalk-jsapi` npm package). Used for in-DingTalk H5 微应用.
- `dd.getAuthCode(...)` — the newer JSAPI exposed via `https://g.alicdn.com/dingding/dingtalk-jsapi/3.x/dingtalk.open.js` for "open" mini-program scenarios.

For an internal-corp H5 微应用 (our case), **stick with `dd.runtime.permission.requestAuthCode`**. It is what every reference implementation we found uses.

---

## 4. accessToken Caching Strategy

Two distinct cache namespaces — never share keys:

### 4.1 Corp accessToken (`/v1.0/oauth2/accessToken`)

- **Key:** `dingtalk:corp_access_token:<appKey>` (singleton per app — only one corp accessToken is valid at a time).
- **TTL:** `expireIn - 300` seconds (refresh 5 minutes early to absorb clock skew).
- **Concurrency:** wrap fetch with a Redis lock (`SET NX EX 30`) so concurrent workers don't all hit the endpoint at once (OAS warns about throttling).
- The OAS guarantees "有效期内重复获取会返回相同结果并自动续期", so concurrent fetches are safe in principle, but Redis-side coalescing still avoids unnecessary API calls.

### 4.2 User userAccessToken (`/v1.0/oauth2/userAccessToken`)

- **Key:** `dingtalk:user_access_token:<unionId>`
- **TTL:** `expireIn - 300` (= 6900 s when `expireIn = 7200`).
- **Refresh token storage:** `dingtalk:user_refresh_token:<unionId>` with TTL 30 days (per OAS description). Encrypt at rest (refresh tokens are bearer credentials).
- **Lookup-by-unionId pattern:** when a previously-logged-in user comes back, we already have the user row → use `unionId` to fetch a cached userAccessToken. If missing/expired, fall back to refreshToken; if that also fails, force re-auth via the JSAPI 免登 (which is silent in-DingTalk).

### Encryption

- Refresh tokens MUST be encrypted at rest. Use the project's existing Fernet/AES key (same key used for `users.dingtalk_refresh_token_encrypted` column).
- accessTokens are short-lived; storing them plaintext in Redis is acceptable (industry standard) provided the Redis instance itself is access-controlled.

---

## 5. Security Notes

1. **`clientSecret` (AppSecret) is backend-only.** It must live in `.env` and never be sent to the browser. Never log it. (oas-ref `/paths/_v1.0_oauth2_userAccessToken.json`: "应用密钥" is in the JSON body of a server-to-server POST, never in URL params.)
2. **Validate `state`** on the OAuth-redirect callback in §1.1. The DingTalk doc confirms `state` is echoed back as-is on success and on error responses. Generate a per-session CSRF token, stash it in a short-lived signed cookie or Redis key, and reject the callback if it does not match.
3. **`redirect_uri` must be pre-registered** in the DingTalk dev console (per scraped doc: *"需要与注册应用时登记的域名保持一致"*). DingTalk rejects callbacks to un-registered domains.
4. **No `signature` field is required on `/v1.0/oauth2/userAccessToken`** — the OAS `security: []` field is empty, and the OAS field list contains no `signature`/`timestamp`/`nonce`. (The older legacy `/sns/getuserinfo_bycode` v1 SNS endpoint *did* require an HMAC signature, but we are not using it — see §7 below.)
5. **`x-acs-dingtalk-access-token`** is the only header required for the `/v1.0/contact/users/{unionId}` call (oas-ref). No HMAC; the bearer token IS the proof.
6. **URL-encode `redirect_uri` and `scope`** before pasting them into the authorize URL. The scraped doc states verbatim: *"参数 value 必须要做 urlencode"*. A space-separated `scope=openid corpid` must become `scope=openid%20corpid` (or use `+` — both work per OAuth2 RFC and per the example scraped from the official login QR-code link `…&scope=openid+corpid`).
7. **Token rotation:** when refreshing, DingTalk MAY (and per the OAS description, does) return a new `refreshToken` in the response body. Always overwrite the stored refresh token with the new one — do NOT assume it's reusable forever.
8. **HTTPS only.** Both `https://login.dingtalk.com/oauth2/auth` and `https://api.dingtalk.com` are HTTPS endpoints; never accept the http fallback.

---

## 6. Decision: which DingTalk identifier to persist in `users.dingtalk_userid`

**Decision: persist `unionId`.**

| Candidate | OAS field description (verbatim, condensed) | Cross-app stability | Cross-org stability | Recommended? |
|---|---|---|---|---|
| `unionId` | "用户的 unionId" — globally unique under the same ISV/developer account, stable across multiple apps. | Yes — same physical user has the **same** `unionId` across every app published by the same DingTalk developer org. | Stable; one personal DingTalk identity maps 1:1. | **YES** — primary identifier. |
| `openId` | "用户的 openId" — per-app scoped pseudonymous ID. | **No** — same user gets different `openId` per app. | Stable within one app. | No — would break if we ever add a second internal app. |
| `userid` (corp `userid`) | Not returned by `/v1.0/contact/users/me`; requires `/topapi/v2/user/getuserinfo` with a corp accessToken. Corp-scoped. | **No** — different `userid` per corp. | Brittle if user changes orgs. | No — also requires an extra API call we don't need. |
| `mobile` | "用户的手机号" — requires extra "Contact.User.mobile" permission grant. | Stable but PII. | Stable but PII. | No — PII; users can change mobile; not all permission grants will allow it. |

Note the (slightly confusing) naming of our DB column `users.dingtalk_userid` from the spec: **we will store `unionId` in that column** (the column name is historic; the column comment / migration should clarify *"DingTalk `unionId`, the cross-app stable identifier"*). Recommend renaming to `dingtalk_union_id` in a future migration; flagged for the Task 11 planner.

---

## 7. Endpoints NOT used / deprecated for this project

For the record, so Task 11 doesn't accidentally pick them up:

- **`POST /sns/getuserinfo_bycode`** — the legacy SNS-flow user-info endpoint. Requires HMAC `signature`/`timestamp`. Superseded by the v1.0 `userAccessToken` + `users/me` pair documented above. (oas-ref `/paths/_sns_getuserinfo_bycode.json`.)
- **`GET /user/getuserinfo`** — legacy v1 corp `userid` lookup ("通过免登码获取用户信息"). Use only if we need the corp `userid` (we don't). (oas-ref `/paths/_user_getuserinfo.json`.)
- **`POST /topapi/v2/user/getuserinfo`** — v2 of the above. Same comment. (oas-ref `/paths/_topapi_v2_user_getuserinfo.json`.)
- **`POST /v1.0/oauth2/jsapiTickets`** — only needed if we want to call advanced JSAPIs that require `dd.config(...)` initialization (e.g., chat-share, custom navigation). The basic `requestAuthCode` 免登 does NOT need it. (oas-ref `/paths/_v1.0_oauth2_jsapiTickets.json`.)

---

## 8. Open TBDs

1. **PC-vs-mobile differences for `dd.runtime.permission.requestAuthCode`.** Rationale: the canonical reference page is a JS-rendered SPA; could not be scraped headlessly. Need a live-browser check before production. Workaround: the API surface (params + callback shape) is identical across platforms per all secondary sources; the TBD is only about edge cases (e.g., does it auto-refresh the code on the PC client when window regains focus?).
2. **Exact JSAPI overview page URL paths.** The official `/document/orgapp/jsapi-overview` URL was probed but returned only the SPA shell. We have the API name and signature from multiple authoritative secondary mirrors; the original detail page should be re-fetched via a real browser at implementation time to confirm no signature changes were made in 2025-2026.
3. **`org_type` / `exclusiveLogin` / `exclusiveCorpId` authorize-URL params.** Documented as optional in the developerpedia scrape; not needed for our basic flow but noted here for completeness. Not used by Task 11 unless we add multi-org support.
4. **Whether `clientSecret` is required when `grantType=refresh_token`.** The OAS `required: [clientId]` only marks `clientId` as required; both `clientSecret` and `code`/`refreshToken` are listed as optional in the schema, with the "required when" logic only described in field-description prose. Safe assumption: always include `clientId` + `clientSecret` + the appropriate token. This is also what every demo we found does.

---

## 9. Citations

OAS references (from `mcp__dingtalk-api__read_project_oas_ref_resources_ce0216`, downloaded 2026-05-12):

- `/paths/_v1.0_oauth2_userAccessToken.json` — operationId implicit, summary "获取用户token" — §2.1.
- `/paths/_v1.0_oauth2_accessToken.json` — summary "获取企业内部应用的 accessToken" — §2.3.
- `/paths/_v1.0_contact_users_%7BunionId%7D.json` — summary "获取用户通讯录个人信息" — §2.2.
- `/paths/_topapi_v2_user_getuserinfo.json` — summary "通过免登码获取用户信息" — §7 (deprecated for this project).
- `/paths/_sns_getuserinfo_bycode.json` — summary "根据 sns 临时授权码获取用户信息" — §7 (legacy SNS flow).
- `/paths/_v1.0_oauth2_jsapiTickets.json` — summary "获取 jsapiTicket" — §7.

Web references:

- DingTalk Developer Encyclopedia, "浏览器内获取用户委托的访问凭证" — https://open-dingtalk.github.io/developerpedia/docs/develop/permission/token/browser/get_user_app_token_browser — §1.1 authorize URL parameters, §1.1 success/error callback payload (`authCode` + `state`).
- DingTalk official server-side doc, "获取登录用户的访问凭证" — https://open.dingtalk.com/document/orgapp-server/obtain-identity-credentials — §1.1 PC scan-code authorize URL parameters; verbatim `scope=openid corpid` requirement to receive `corpId` in the token response.
- Aliyun Developer Community, "vue 项目对接钉钉企业内部 H5 微应用" — https://developer.aliyun.com/article/790736 — §3 frontend JSAPI snippet (`dd.runtime.permission.requestAuthCode`).
- DingTalk JSAPI overview (SPA shell, did not fully render) — https://open.dingtalk.com/document/orgapp/jsapi-overview — flagged TBD in §8.
- `open-dingtalk/corp_demo_php` reference repo — https://github.com/open-dingtalk/corp_demo_php — used to confirm the JSAPI surface name; not quoted directly.
