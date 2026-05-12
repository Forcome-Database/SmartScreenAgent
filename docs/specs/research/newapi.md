# newapi Gateway Integration — Research Notes

**Task:** P1 Task 0.1 — confirm newapi base URL, auth, OpenAI compatibility, streaming, and function-calling support before implementing the LLM gateway client in Task 10.

**Researched on:** 2026-05-12

## TL;DR

- The "newapi" project originally maintained at `Calcium-Ion/new-api` is now maintained at **`QuantumNous/new-api`** (the GitHub search result and the official docs site `doc.newapi.pro` both point there). The Calcium-Ion repo and Docker image `calciumion/new-api` still exist and continue to receive container builds, but new development happens under QuantumNous. Source: [QuantumNous/new-api README.en.md](https://github.com/QuantumNous/new-api/blob/main/README.en.md), [calciumion/new-api Docker Hub](https://hub.docker.com/r/calciumion/new-api).
- newapi exposes a **fully OpenAI-compatible** `/v1/chat/completions` endpoint with `Authorization: Bearer <key>` auth, so the official `openai` / `AsyncOpenAI` Python SDK can connect directly by setting `base_url`.
- Streaming, tools (function calling), and `response_format` (JSON mode / JSON schema) are all supported on the OpenAI-compatible route.
- **Model IDs are admin-configured per channel** — every newapi deployment may expose different strings. The 4 target models for this project (`gpt-5.5`, `gpt-5.4`, `gemini-3-flash`, `DeepSeek-V4`) **must be confirmed in our specific newapi admin console**; do not hardcode them in code without verification.

---

## 1. Base URL Template

```
http(s)://<newapi-host>[:<port>]/v1
```

- Default port: **3000**. Source: ["visit `http://localhost:3000` to start using"](https://github.com/QuantumNous/new-api/blob/main/README.en.md) and `PORT` env var "Service listening port – Default `3000`" ([env vars docs](https://doc.newapi.pro/en/installation/environment-variables/)).
- OpenAI-compatible endpoint path: `POST /v1/chat/completions` (verbatim from [doc.newapi.pro OpenAI Chat Completions API Reference](https://doc.newapi.pro/en/api/openai-chat/): *"Endpoint URL `POST /v1/chat/completions` … The endpoint format follows: `https://your-newapi-server-address/v1/chat/completions`"*).
- The `/v1` prefix **is required** for OpenAI-compatible routes. There is a separate internal admin API mounted under `/api/...` (e.g., `GET /api/models` for the model list) — that is **not** OpenAI-compatible. Source: [List Available Models docs](https://doc.newapi.pro/en/api/get-available-models-list/).

### Example concrete base URLs

| Environment | Example `base_url` for OpenAI SDK |
|---|---|
| Local dev (Docker default) | `http://localhost:3000/v1` |
| Self-hosted prod with reverse proxy | `https://newapi.<our-domain>/v1` |

> **Important:** when configuring the OpenAI SDK, pass the URL **including `/v1`** (e.g., `base_url="https://newapi.example.com/v1"`). The SDK appends `chat/completions` itself. Do **not** pass the full `chat/completions` path.

## 2. Auth Header Example

```
Authorization: Bearer <NEWAPI_API_KEY>
Content-Type: application/json
```

Verbatim from the official docs ([OpenAI Chat Completions API Reference](https://doc.newapi.pro/en/api/openai-chat/)):

> Authentication — Include this header in requests:
> `Authorization: Bearer $NEWAPI_API_KEY`

The key is issued from the newapi admin console (per-user "Token" with optional model whitelist, group, quota, and rate-limit binding). Source: README mentions *"Token grouping, model restrictions, user management"* ([QuantumNous/new-api](https://github.com/QuantumNous/new-api)).

## 3. OpenAI SDK Direct-Connect Snippet

Because newapi is wire-compatible with OpenAI's REST schema on `/v1/chat/completions`, the standard `openai>=1.x` SDK works without any custom transport.

```python
# pip install openai>=1.30
import os
from openai import AsyncOpenAI

client = AsyncOpenAI(
    api_key=os.environ["NEWAPI_API_KEY"],
    base_url=os.environ["NEWAPI_BASE_URL"],   # e.g. "https://newapi.example.com/v1"
    timeout=60.0,                              # see "Gotchas" §6 about RELAY_TIMEOUT
)

async def screen_resume(prompt: str, model: str) -> str:
    resp = await client.chat.completions.create(
        model=model,                           # exact string from newapi admin, e.g. "gpt-5.5"
        messages=[
            {"role": "system", "content": "You are an HR screening assistant."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},   # see §5
        stream=False,
    )
    return resp.choices[0].message.content
```

Reference cURL form (1:1 OpenAI-compatible) from the docs ([OpenAI Chat Completions API Reference](https://doc.newapi.pro/en/api/openai-chat/)):

```bash
curl https://your-newapi-server-address/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $NEWAPI_API_KEY" \
  -d '{
    "model": "gpt-4.1",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

## 4. Target Model IDs (4 models)

> **Cannot be confirmed from public sources** — newapi exposes whatever string the deployment's admin maps each upstream model to (per the channel-to-model mapping shown in [List Available Models docs](https://doc.newapi.pro/en/api/get-available-models-list/), the response is *"Mapping from Channel ID to model list"* with arbitrary admin-defined names). The plan also instructs against fabricating these. Therefore the table below is **TBD until verified against our gateway**.

| Logical model (per project plan) | Likely upstream | Likely newapi `model` string (TBD) | Verification method |
|---|---|---|---|
| `gpt-5.5` | OpenAI GPT-5.5 (released Apr 2026, ref: [DataCamp GPT-5.5 vs DeepSeek V4](https://www.datacamp.com/blog/deepseek-v4-vs-gpt-5-5)) | `gpt-5.5` (most likely passthrough) — **TBD-verify-with-admin** | `GET <base>/api/models` with user token, or check admin console → Models page |
| `gpt-5.4` | OpenAI GPT-5.4 (older sibling, referenced in DeepSeek benchmarks: [TechCrunch](https://techcrunch.com/2026/04/24/deepseek-previews-new-ai-model-that-closes-the-gap-with-frontier-models/)) | `gpt-5.4` — **TBD-verify-with-admin** | same |
| `gemini-3-flash` | Google Gemini 3 Flash (ref: [Spectrum AI Lab comparison](https://spectrumailab.com/blog/claude-opus-4-7-vs-gpt-5-5-vs-gemini-3-1-pro-vs-deepseek-v4-comparison-2026)) — note public sources actually call it "Gemini 3 Flash" / "Gemini 3.1 Pro"; the exact upstream id might be `gemini-3-flash`, `gemini-3.0-flash`, or `models/gemini-3-flash` | `gemini-3-flash` — **TBD-verify-with-admin** | same |
| `DeepSeek-V4` | DeepSeek V4 (V4-Pro 1.6T / V4-Flash 284B variants, ref: [Simon Willison's notes](https://simonwillison.net/2026/apr/24/deepseek-v4/), [VentureBeat](https://venturebeat.com/technology/deepseek-v4-arrives-with-near-state-of-the-art-intelligence-at-1-6th-the-cost-of-opus-4-7-gpt-5-5)) | `DeepSeek-V4` / `deepseek-v4` / `deepseek-chat` — **TBD-verify-with-admin** (DeepSeek's own API uses `deepseek-chat`, but a newapi admin can rename to anything) | same |

**Action item for the implementer (Task 10):**
1. Run `curl -H "Authorization: Bearer $NEWAPI_API_KEY" "<base>/api/models"` (or whatever endpoint our newapi version exposes — some forks expose `GET /v1/models` in OpenAI-compatible form too; try both) and record the exact strings.
2. Store the 4 strings in `.env` / settings as `NEWAPI_MODEL_GPT55`, `NEWAPI_MODEL_GPT54`, `NEWAPI_MODEL_GEMINI3FLASH`, `NEWAPI_MODEL_DEEPSEEKV4` — do **not** hardcode them in Python source. This way if the admin renames a channel, only `.env` changes.

## 5. Feature Support Matrix

| Feature | Supported? | Source |
|---|---|---|
| Streaming (`stream: true`) | **Yes** | [`STREAMING_TIMEOUT` env var docs](https://doc.newapi.pro/en/installation/environment-variables/) — *"Streaming single response timeout (seconds)"*, default 60s. Also listed as `stream` parameter in [OpenAI Chat Completions API Reference](https://doc.newapi.pro/en/api/openai-chat/). |
| Tool / function calling (`tools`, `tool_choice`) | **Yes** (for OpenAI-route upstreams) | [OpenAI Chat Completions API Reference](https://doc.newapi.pro/en/api/openai-chat/) lists *"`tools` - Array of functions the model can call"* and *"`tool_choice` - Controls tool usage (`none`, `auto`, `required`, or specific function)"*. |
| Legacy `function_call` field | Marked deprecated, but accepted | [OpenAI Chat Completions API Reference](https://doc.newapi.pro/en/api/openai-chat/): *"`function_call` - Deprecated; use `tool_choice` instead"*. Match OpenAI's own deprecation — prefer `tools`. |
| JSON mode (`response_format: {"type": "json_object"}`) | **Yes** | [OpenAI Chat Completions API Reference](https://doc.newapi.pro/en/api/openai-chat/): *"`response_format` - Specify output format (`text`, `json_object`, `json_schema`)"*. |
| JSON schema (`response_format: {"type": "json_schema", ...}`) | **Yes** (per spec) — actual enforcement depends on whether the upstream model supports structured outputs | same source |
| Logprobs, seed, penalties, `user` field | Yes | same source |
| Function calling when **upstream is Gemini** routed through OpenAI-compatible format | **Partially — caveat** | README [QuantumNous/new-api](https://github.com/QuantumNous/new-api): *"Google Gemini → OpenAI Compatible — Text only, function calling not supported yet"*. **Implication:** if our HR screening agent uses `tools=[...]` against the `gemini-3-flash` route, the gateway may strip / reject tool calls. Test before relying on it; fall back to JSON-mode prompting for Gemini. |
| Audio / image / embeddings endpoints | Yes (separate paths) | README lists *"Chat Interface, Image Interface, Audio Interface, Embedding Interface"* ([QuantumNous/new-api](https://github.com/QuantumNous/new-api)). Out of scope for this task. |

## 6. Known Gotchas

### 6.1 Timeouts

- `STREAMING_TIMEOUT` default = **60 seconds** for a single streaming response. Long resume-screening chains may need this bumped on the gateway, or the client must keep responses chunked. Source: [env vars](https://doc.newapi.pro/en/installation/environment-variables/).
- `RELAY_TIMEOUT` — total relay request timeout. **The docs explicitly warn against setting this too short because it causes billing-sync issues** (i.e., a request that the upstream completed but the gateway lost will not be billed and may produce a stale 5xx to the client). Source: [env vars](https://doc.newapi.pro/en/installation/environment-variables/). **Recommendation:** set `AsyncOpenAI(timeout=...)` to a value just *under* the gateway's `RELAY_TIMEOUT` to avoid both sides timing out simultaneously and creating reconciliation mismatches.
- `USER_CONTENT_REQUEST_TIMEOUT` — controls how long the gateway waits when it has to download user-supplied content (e.g., image URLs in vision requests). Source: same.

### 6.2 Rate Limits

- `GLOBAL_API_RATE_LIMIT` — default **180 requests per IP per 3 minutes**. Source: [env vars](https://doc.newapi.pro/en/installation/environment-variables/). For bulk resume screening, plan to either (a) raise this on the gateway, (b) run the SmartScreenAgent worker behind a stable internal IP and whitelist it, or (c) implement client-side throttling (e.g., `asyncio.Semaphore(N)` + token-bucket). 180/3min ≈ **1 RPS sustained**, which is low for a batch resume run.
- Per-token / per-user / per-model rate limits are configurable in the admin console under "Rate Limit Settings" and "Rate Settings" — these are admin-managed and not reflected in env vars. Source: [QuantumNous docs index → Settings](https://doc.newapi.pro/en/).

### 6.3 Token Billing

- Billing supports *"organization-level per-request, usage-based, and cache-hit cost accounting"* (README, [QuantumNous/new-api](https://github.com/QuantumNous/new-api)). Translation: each request is metered against the token's quota; usage = (input_tokens × input_price) + (output_tokens × output_price), with optional discount when prompt-caching is hit.
- `DEFAULT_QUOTA` for new users defaults to `0` — meaning a freshly created token cannot call any models until the admin grants quota. Source: [env vars](https://doc.newapi.pro/en/installation/environment-variables/). **For our deployment:** ensure the SmartScreenAgent service token has sufficient quota before the first run.
- Billing-sync issue (mentioned in 6.1): if `RELAY_TIMEOUT` triggers before the upstream finishes, the request may complete server-side but the gateway will not record usage. Mitigation: keep client-side timeout < gateway timeout, and consider logging request IDs to reconcile against the admin console.

### 6.4 Concurrency

- No explicit per-instance concurrency cap is documented; the gateway is a Go HTTP server (Gin) so practical concurrency is bounded by upstream provider rate limits, gateway CPU, and `MAX_REQUEST_BODY_MB` for large prompts. Source: README env-vars section ([QuantumNous/new-api](https://github.com/QuantumNous/new-api) — `STREAM_SCANNER_MAX_BUFFER_MB`, `MAX_REQUEST_BODY_MB`).
- For SmartScreenAgent, treat the gateway as the bottleneck and cap simultaneous in-flight LLM calls (e.g., `asyncio.Semaphore(8)`) until we benchmark.

### 6.5 Model Mapping is Per-Deployment

- Every newapi instance has its **own** model name space. The "Channel" admin page lets an operator alias an upstream like `openai/gpt-5.5-preview` to a public model id like `gpt-5.5` (or rename it to `our-fast-model`). Source: [List Available Models docs](https://doc.newapi.pro/en/api/get-available-models-list/) — *"`data` field contains 'Mapping from Channel ID to model list'"*.
- **Implication for our code:** never hardcode model strings. Load from `.env` (see §4 action item) and surface them through `LLMGateway` settings so they can be swapped without a code change.

### 6.6 Repo / Image Provenance

- **Active maintainer:** [`QuantumNous/new-api`](https://github.com/QuantumNous/new-api). Recent activity, v1.0.0-rc.5 in May 2026.
- **Original repo:** [`Calcium-Ion/new-api`](https://github.com/Calcium-Ion/new-api) — still served, but README in QuantumNous fork is the authoritative current docs.
- **Docker image:** [`calciumion/new-api`](https://hub.docker.com/r/calciumion/new-api) (the image namespace did not migrate even though the GitHub org did). Pin a specific version tag in our `docker-compose.yml` rather than `latest`.
- **Docs site:** `doc.newapi.pro` (note: singular `doc`, not `docs`). The `docs.newapi.pro` host returns mostly 404s for deep paths.

## Sources (consolidated)

- [QuantumNous/new-api GitHub README](https://github.com/QuantumNous/new-api/blob/main/README.en.md)
- [QuantumNous/new-api repo root](https://github.com/QuantumNous/new-api)
- [Calcium-Ion/new-api GitHub README](https://github.com/Calcium-Ion/new-api/blob/main/README.en.md)
- [calciumion/new-api Docker image](https://hub.docker.com/r/calciumion/new-api)
- [newapi docs — OpenAI Chat Completions API Reference](https://doc.newapi.pro/en/api/openai-chat/)
- [newapi docs — Environment Variables](https://doc.newapi.pro/en/installation/environment-variables/)
- [newapi docs — List Available Models](https://doc.newapi.pro/en/api/get-available-models-list/)
- [newapi docs index](https://doc.newapi.pro/en/)
- 2026 model context (used only to label upstream identities, not to fabricate gateway strings):
  - [DataCamp — GPT-5.5 vs DeepSeek V4](https://www.datacamp.com/blog/deepseek-v4-vs-gpt-5-5)
  - [TechCrunch — DeepSeek previews V4](https://techcrunch.com/2026/04/24/deepseek-previews-new-ai-model-that-closes-the-gap-with-frontier-models/)
  - [VentureBeat — DeepSeek V4 launch](https://venturebeat.com/technology/deepseek-v4-arrives-with-near-state-of-the-art-intelligence-at-1-6th-the-cost-of-opus-4-7-gpt-5-5)
  - [Simon Willison — DeepSeek V4](https://simonwillison.net/2026/apr/24/deepseek-v4/)
  - [Spectrum AI Lab — 2026 model comparison](https://spectrumailab.com/blog/claude-opus-4-7-vs-gpt-5-5-vs-gemini-3-1-pro-vs-deepseek-v4-comparison-2026)

## Open Questions for Task 10 Implementer

1. **Resolve TBD model strings** by calling the deployed gateway's model-list endpoint (or asking the gateway admin). Capture the result in `.env.example`.
2. **Decide tool-calling fallback for Gemini route:** Gemini→OpenAI tool calls are flagged unsupported in the README; for `gemini-3-flash`, prefer `response_format: json_object` + prompt-engineered schema instead of `tools=[]`.
3. **Pick client timeout** = `min(application SLA, RELAY_TIMEOUT − 5s)`. Coordinate with whoever sets `RELAY_TIMEOUT` on the gateway.
4. **Confirm whether our gateway exposes `GET /v1/models`** in OpenAI-compatible form (for clean SDK introspection) in addition to `GET /api/models`.
