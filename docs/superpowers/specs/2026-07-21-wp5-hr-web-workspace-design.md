# WP5 HR Web Workspace Design

**Date:** 2026-07-21

**Status:** Draft (pending approval)

**Work package:** WP5

**Depends on:** WP1 DingTalk OAuth + JWT, WP3 async upload/status endpoints, WP4 read APIs

## 1. Purpose

WP5 delivers the first usable internal HR workflow as a standalone web
application: an HR user logs in with DingTalk, uploads resumes (single or
batch), monitors ingestion, browses ranked and flat candidate lists, inspects a
candidate's authorized PII, reads a scorecard with evidence, downloads the raw
resume, and re-scores — the complete **upload → monitor → list → inspect →
re-score** golden path, entirely over the WP4/WP3 HTTP surface with no direct
database access.

WP5 is a frontend-only work package. It changes no backend contract, schema, or
scoring behavior. It consumes the endpoints frozen by WP1 (`/auth/dingtalk/login`),
WP3 (`/candidates/upload`, `/candidates/batch`, `/jobs/{id}`, `/batches/{id}`,
`POST /candidates/{id}/score`), and WP4 (the nine read routes).

## 2. Baseline and Gaps

### 2.1 Baseline

- The repository has **no frontend**. The FastAPI app's CORS middleware carries
  an intent comment ("Next.js 前端跨域调用") and `allow_credentials=True`, but no
  client exists.
- WP1 provides `POST /auth/dingtalk/login` which takes a DingTalk `auth_code`,
  exchanges it for user info, upserts a `User` (role defaults to `hr`), and
  returns `{token, display_name, role}`. The token is a JWT bearer used as
  `Authorization: Bearer <token>` on every protected route. Roles are
  `hr`, `hr_lead`, `admin`.
- WP3 provides async upload (`202 {job_id}`), batch upload, and
  `GET /api/v1/candidates/jobs/{id}` / `GET /api/v1/candidates/batches/{id}`
  status, plus the idempotent `POST /api/v1/candidates/{id}/score`.
- WP4 provides nine role-gated read routes: JD-scoped ranked candidate list,
  flat candidate list, candidate detail (audited PII decrypt), score detail with
  evidence, raw-file presigned download (audited), JD list/detail, rule-version
  list, and rule-version diff.

### 2.2 Gaps

- No login UI, no session handling, no authenticated data-fetching layer.
- No screens for upload, status monitoring, candidate lists, candidate detail,
  scorecard, or raw-file download.
- No responsive/accessible presentation and no frontend test or quality gate.

## 3. Goals

- A standalone web app an HR user opens in a normal desktop or mobile-width
  browser and logs into via DingTalk (QR / account) OAuth redirect.
- The complete golden path: login, upload (single + batch), job/batch status
  monitoring, JD-scoped ranked and flat candidate lists (filtered, paginated),
  candidate detail with authorized PII, scorecard with evidence, raw-file
  download, and re-score.
- A Backend-for-Frontend (BFF) security posture: the JWT never reaches client
  JavaScript; it is held in an httpOnly cookie and injected server-side.
- Consistent loading, empty, and error states; PII shown only on a deliberate
  detail view; no leak of tokens, presigned URLs, object keys, or ciphertext
  into client JS or logs.
- Responsive and accessible; an automated golden-path end-to-end test plus
  accessibility and responsive checks as the exit evidence.

## 4. Non-goals

- JD / rule-version / rule-diff **viewing** UI and any rule editor or
  publication workflow (WP6).
- Feedback capture, golden-set calibration, What-If simulation (WP6); cost and
  operational reporting (WP7); DingTalk recruitment-document sync (WP8).
- Multi-locale i18n (zh-CN only for v1; code is structured to not preclude it).
- Any change to WP1–WP4 backend contracts, schema, or the scoring algorithm.
- Server-side rendering of candidate PII for SEO/caching (PII is fetched on
  demand, never statically cached).

## 5. Architecture and Stack

- **Next.js 15 (App Router) + TypeScript**, **Tailwind CSS v4**, and
  **shadcn/ui (latest)** for accessible Radix-based components. Client
  server-state via **TanStack Query**; forms via **react-hook-form + zod**, with
  zod also validating API responses at the BFF boundary. UI language **zh-CN**
  (single locale for v1).
- **BFF pattern.** The browser talks only to the Next.js origin. Next.js Route
  Handlers under `/api/*` hold the JWT in an httpOnly, Secure, SameSite=Lax
  cookie and proxy each backend call to FastAPI, injecting the
  `Authorization: Bearer` header server-side. The JWT is never exposed to
  client JavaScript, so an XSS bug cannot exfiltrate it — the decisive property
  for a PII-handling app. Because the browser→Next hop is same-origin and the
  Next→FastAPI hop is server-to-server, no browser CORS is involved (the backend
  CORS config is left in place but unused by this client).
- **Location.** A new `frontend/` directory in the repository. The app builds to
  a Next.js standalone output, containerized via `frontend/Dockerfile` and run as
  a compose service alongside the FastAPI backend.

The BFF is a thin, uniform proxy: one internal helper builds the upstream
request (base URL from server env, cookie-derived bearer, forwarded method /
query / body), calls FastAPI, and returns the JSON or a normalized
`{code, message}` error. Route Handlers are deliberately dumb so the security and
error contract lives in one place.

## 6. Screens and Routes

All app routes except `/login` and `/auth/callback` require a valid session;
Next.js middleware redirects unauthenticated requests to `/login`.

Backend routes are under these prefixes: auth `/auth`, WP3 write/status
`/api/v1/candidates`, WP4 reads `/api/v1`.

| Route | Screen | Backend endpoint(s) via BFF |
|---|---|---|
| `/login` | DingTalk login (redirect to OAuth) | — (builds the DingTalk authorize URL) |
| `/auth/callback` | OAuth callback, exchanges `auth_code` | `POST /auth/dingtalk/login` |
| `/upload` | Single + batch drag-drop upload with progress | `POST /api/v1/candidates/upload`, `POST /api/v1/candidates/batch` |
| `/jobs/[id]`, `/batches/[id]` | Job / batch status (polled); may also render inline on `/upload` | `GET /api/v1/candidates/jobs/{job_id}`, `GET /api/v1/candidates/batches/{batch_id}` |
| `/jds/[code]` | JD-scoped ranked candidate list (grade filter, pagination) | `GET /api/v1/jds/{code}/candidates` |
| `/candidates` | Flat candidate list (state filter, pagination) | `GET /api/v1/candidates` |
| `/candidates/[id]` | Candidate detail: authorized PII + score summaries | `GET /api/v1/candidates/{candidate_id}` |
| `/candidates/[id]/scores/[sid]` | Scorecard: hard-filter, rule, judge dimensions + evidence quotes | `GET /api/v1/candidates/{candidate_id}/scores/{score_id}` |
| detail actions | Raw-file download, re-score | `GET /api/v1/candidates/{candidate_id}/raw-file`, `POST /api/v1/candidates/{candidate_id}/score` |

A persistent app shell provides navigation (upload, candidates, per-JD lists),
the signed-in user's display name/role, and logout.

## 7. Authentication and Session

1. `/login` builds the DingTalk OAuth authorize URL (client id + redirect URI to
   `/auth/callback`, state for CSRF) and sends the user to DingTalk (QR / account
   login).
2. DingTalk redirects back to `/auth/callback?code=...&state=...`. The callback
   verifies `state`, then calls the BFF route handler `POST /api/auth/callback`,
   which forwards the `auth_code` to `POST /auth/dingtalk/login`.
3. On success the BFF sets an httpOnly+Secure+SameSite=Lax session cookie holding
   the JWT (and non-sensitive display name / role for UI), then redirects into
   the workspace.
4. **Middleware** guards protected routes: no valid session cookie → redirect to
   `/login`. Logout clears the cookie.
5. **Expiry / revocation.** When the BFF proxies a call and FastAPI returns
   `401`, the BFF clears the session and returns a normalized 401; the client
   redirects to `/login`. Roles from the session drive UI affordances only —
   the backend remains the authoritative RBAC.

The `state` parameter and the redirect URI are validated to prevent OAuth CSRF
and open-redirect. The JWT is never written to a non-httpOnly cookie, to
`localStorage`, or into any client-readable response body.

## 8. Data Flow and Interaction States

- **Lists.** TanStack Query over the BFF, offset pagination (`page`,
  `page_size`), filters as query params. Filter and page state is reflected in
  the URL so a view is shareable and survives refresh. `total` from the envelope
  drives the pager.
- **Upload.** Drag-drop one or many files → submit (single or batch) → receive
  `job_id` / `batch_id` → TanStack Query with `refetchInterval` polls
  `GET /api/v1/candidates/jobs/{id}` / `GET /api/v1/candidates/batches/{id}`
  until a terminal state
  (`ready`/`completed`/`*_failed`), then stops and shows the outcome. Per the WP3
  contract, per-file batch failures are reported synchronously in the `202`
  response and `GET /batches/{id}` reflects only durable jobs — the UI surfaces
  both the synchronous per-file failures and the durable batch progress.
- **Re-score.** A button on the detail/scorecard triggers
  `POST /candidates/{id}/score`; on success the relevant queries are invalidated
  and refetched.
- **States.** Every data view has explicit loading (skeleton), empty (guidance),
  and error (message + retry) states. Errors are normalized by the BFF to
  `{code, message}` and rendered consistently; a `503 object_storage_unavailable`
  on raw-file download and a `404` on a missing object are surfaced as distinct,
  actionable messages.

## 9. PII and Leak Safety

- Candidate **lists carry no PII**. The candidate **detail** page is the
  deliberate PII view: it is reached only by an explicit click from the PII-free
  list and calls `GET /candidates/{id}`, which writes exactly one `pii_decrypt`
  audit row per view. Phone / email are presented with a copy affordance; no
  additional client-side masking is layered on (the audit already records the
  access, so masking would be theater).
- **Raw-file download** uses the short-lived presigned URL, which writes one
  `raw_file_access` audit row. The presigned URL is obtained server-side by the
  BFF and handed to the browser only at the moment of download (a redirect or a
  one-shot link); it is never stored in client state, embedded in rendered HTML
  beyond the immediate download action, or logged.
- No token, presigned URL, object key, ciphertext, or provider body ever appears
  in client JavaScript, client-readable responses, or logs. The BFF strips any
  such field it does not explicitly forward.

## 10. Responsive and Accessibility

- Supported on desktop and mobile-width browsers. Tables degrade to card lists
  on narrow viewports; the app shell collapses navigation responsively.
- Built on Radix primitives (via shadcn/ui) for keyboard navigation, focus
  management, and correct ARIA semantics. Color, contrast, and focus-visible
  states meet WCAG AA. Forms use label association and inline validation
  messaging.

## 11. Testing and Quality Gates

- **Unit / component:** Vitest + React Testing Library. Cover the BFF request
  helper and zod response validation, the normalized error mapping, PII-free
  list rendering, pagination/filter URL sync, upload state machine, and
  loading/empty/error states.
- **End-to-end:** Playwright drives the golden path (DingTalk login mocked at the
  BFF boundary → upload → poll status → ranked and flat lists → candidate detail
  → scorecard → re-score), asserting the audited PII view appears only on detail
  and that no token/URL leaks into the DOM or console. FastAPI is stubbed at the
  BFF boundary for determinism; an optional real-backend smoke can run the same
  flow against the live stack.
- **Accessibility:** axe-core checks on each key screen (no serious/critical
  violations); **responsive** checks across desktop and mobile viewports in
  Playwright.
- **Static:** `tsc --noEmit`, ESLint, Prettier. These plus the test suites are
  the WP5 exit evidence, mirroring the backend's offline/lint/type gate.

## 12. Deployment and Configuration

- `frontend/Dockerfile` produces a Next.js standalone image; a compose service
  runs it alongside FastAPI.
- Server-only env: FastAPI base URL, session-cookie secret, DingTalk client id
  and redirect URI. No secret is exposed to the browser bundle (only the DingTalk
  client id and authorize endpoint, which are public, appear client-side).
- Because the BFF makes browser traffic same-origin, the backend CORS
  configuration is unused by this client; it is left untouched.

## 13. Rollout and Rollback

WP5 adds a new `frontend/` app and a compose service; it changes no backend
code, contract, or schema. Rollback is simply not deploying (or removing) the
frontend service — the backend is unaffected. The app can ship behind an
internal URL for HR before any wider exposure.

## 14. Exit Criteria

WP5 is complete when:

- An HR user can complete the full golden path — DingTalk login, upload (single
  and batch), monitor job/batch status to a terminal state, browse the ranked
  and flat candidate lists with filters and pagination, open a candidate detail
  with authorized PII, read a scorecard with evidence, download the raw resume,
  and re-score — in supported desktop and mobile-width browsers.
- The JWT is held only in an httpOnly cookie via the BFF; no token, presigned
  URL, object key, or ciphertext appears in client JavaScript, client-readable
  responses, or logs. PII appears only on the deliberate detail view, each view
  writing exactly one `pii_decrypt` audit row (verified against the backend).
- The Playwright golden-path e2e passes with FastAPI stubbed at the BFF
  boundary; axe-core accessibility checks show no serious/critical violations;
  responsive checks pass on desktop and mobile viewports.
- `tsc --noEmit`, ESLint, Prettier, and the Vitest/Playwright suites pass; the
  frontend builds a production standalone image.
- Exact commits, test counts, and (if wired) hosted CI evidence are recorded;
  WP6 is changed to Ready for planning only after every WP5 gate passes.

## 15. Approval

Approval means implementation may proceed. WP5 completion remains blocked until
the full frontend gate (unit, e2e, accessibility, responsive, type, lint, build)
passes.
