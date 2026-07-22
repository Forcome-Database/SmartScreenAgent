# SmartScreenAgent

AI-driven resume screening agent for HR.

## 状态

当前处于“后端评分原型已完成、生产化加固进行中”阶段。

**WP0 可重复集成基线、WP1 安全与原文件完整性、WP2 生产解析器契约与校验 AI 输出均已完成并通过托管 CI**：候选人写接口已强制 JWT/RBAC，上传经流式大小/类型/文件签名校验并持久化到私有 MinIO；MinerU 已切换到官方 API v4，简历抽取与 LLM judge 输出经严格 Pydantic 与证据溯源校验后才能落库。WP2 托管验收见 [`verify` run 29714208508](https://github.com/Forcome-Database/SmartScreenAgent/actions/runs/29714208508)。

**WP3 可恢复异步任务已完成并通过托管 CI**：候选人上传接口已切换为异步——`POST /candidates/upload` 立即返回 `202 {job_id}` 并把简历交给 `ingestion_jobs` 状态机和 Celery worker（`ingest.parse_and_score`）处理；Celery Beat 定期运行回收/重试 sweeper（`ingest.sweep`），处理中租约过期的任务会被回收并按 `INGESTION_MAX_ATTEMPTS` 重试或终结、卡在 `queued` 的任务会被重扫补入队，不会产生重复候选人或评分。WP3 托管验收见 [`verify` run 29795950194](https://github.com/Forcome-Database/SmartScreenAgent/actions/runs/29795950194)（提交 `4bd7130`，PR #3）。**读 API（WP4）In progress**：只读的候选人/JD/规则版本接口已实现并通过本地全量门禁，尚待托管 CI 验收后才会标记为 Complete。**HR Web 工作台（WP5）In progress**：`frontend/` 下的 Next.js BFF 前端（候选人列表/详情/评分卡、JD、上传）已实现并通过本地全量门禁（lint/typecheck/vitest/Playwright e2e/build），托管 CI 验收与最终 Ready 标记随 WP6 一起完成。**HR 复核反馈与最小报表（WP6a）In progress**：候选人评分页新增复核反馈（推进/淘汰/待定 + 理由，`ai_agreed` 服务端派生）与一个 AI-HR 一致性最小报表页，后端/前端均已实现并通过本地全量门禁（含新增的 `frontend/e2e/feedback.spec.ts`），托管 CI 验收后标记为 Complete。当前状态和后续依赖以 [`docs/superpowers/specs/2026-07-13-current-state-and-roadmap-design.md`](docs/superpowers/specs/2026-07-13-current-state-and-roadmap-design.md) 为准。

## Quick start

```bash
# 1. 启动依赖容器
docker compose up -d

# 2. 配环境
cp .env.example .env
# 编辑 .env，至少替换:
#   NEWAPI_API_KEY (你的 newapi 网关 key)
#   DINGTALK_APP_KEY / DINGTALK_APP_SECRET / DINGTALK_CORP_ID
#   PII_ENCRYPTION_KEY (生成: uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
#   JWT_SECRET_KEY (任何 ≥32 字节随机字符串)

# 3. 安装依赖
uv sync --extra dev

# 4. 跑迁移
uv run alembic upgrade head

# 5. 启动后端
uv run uvicorn backend.app.main:app --reload

# 6. 启动 Celery worker (新终端)
uv run celery -A backend.app.tasks.celery_app worker -l info

# 7. 启动 Celery Beat (新终端；定期触发 ingest.sweep 回收/重试处理中任务)
uv run celery -A backend.app.tasks.celery_app beat -l info

# 8. 验证
curl http://127.0.0.1:8000/healthz
# 应返回 {"status":"ok","checks":{"db":"ok","redis":"ok","minio":"ok"}}

# 9. 跑测试
uv run pytest                 # 全部
uv run pytest -m "not integration"  # 仅单元
```

`docker-compose.yml` 只编排基础设施（PostgreSQL/Redis/MinIO）；应用、worker、beat 均通过 `uv run` 在宿主机启动，不在 compose 里。

注：本机若 `localhost` 被劫持到其他服务，所有 curl 用 `127.0.0.1`。

## 开发验证

```bash
uv run pytest -m "not integration"
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
uv run python scripts/verify.py
```

直接选择 `pytest -m integration` 时，本地不可用的外部服务可能使相关用例跳过；`scripts/verify.py` 会启动隔离依赖并启用严格模式，任何跳过或服务缺失都会使验证失败。

托管工作流 [`.github/workflows/verify.yml`](.github/workflows/verify.yml) 在 Python 3.10 和 3.14 上运行验证矩阵；WP0 验收运行见 [GitHub Actions run 29237545679](https://github.com/Forcome-Database/SmartScreenAgent/actions/runs/29237545679)。

`MINERU_MODE=stub` 仅用于离线开发和测试；生产使用 `MINERU_MODE=official`，通过官方 MinerU API v4 申请签名上传地址、轮询批次并下载结果 ZIP。

候选人上传和评分 API 已要求 Bearer JWT，允许角色为 `hr`、`hr_lead`、`admin`。MinerU 官方 API v4 四格式与 new-api 严格结构化输出已通过本地真实端点契约门禁；公网部署前仍需完成托管 CI、生产密钥托管和运行环境验收。

## 设计文档

- 当前状态与交付路线图（权威）：`docs/superpowers/specs/2026-07-13-current-state-and-roadmap-design.md`
- WP1 安全与原文件完整性规格：`docs/superpowers/specs/2026-07-16-wp1-security-and-raw-file-integrity-design.md`
- WP1 实施与验证记录：`docs/superpowers/plans/2026-07-16-wp1-security-and-raw-file-integrity.md`
- 后续工作包计划索引：`docs/superpowers/plans/README.md`
- 原始产品设计（历史愿景）：`docs/specs/2026-05-12-resume-screening-agent-design.md`
- P1/P2 历史实施计划：`docs/specs/plans/`
- 已并入 WP1 的 JWT/RBAC 历史设计：`docs/superpowers/specs/2026-07-08-jwt-rbac-p2-api-design.md`
- 调研笔记：`docs/specs/research/`
  - `newapi.md` — LLM 网关接入
  - `dingtalk-oauth.md` — 钉钉 OAuth 流程（基于 OAS 实读）
  - `pgvector-sqlalchemy.md` — 向量列与 HNSW 索引
  - `pgcrypto.md` — PII 加密决策（选 Fernet）

## 项目结构（P1 完成时）

```
backend/
├── app/
│   ├── main.py              # FastAPI app factory
│   ├── config.py            # pydantic-settings
│   ├── database.py          # SQLAlchemy async engine
│   ├── deps.py              # FastAPI dependencies
│   ├── logging_config.py    # structlog
│   ├── middleware.py        # access log + trace id
│   ├── models/              # User / JD / RuleVersion / Candidate /
│   │                         #   Score / Feedback / GoldenSet /
│   │                         #   AuditLog / CandidateEmbedding
│   ├── routers/             # health, auth (DingTalk login)
│   ├── services/
│   │   ├── llm/             # LLMGateway (newapi + fallback)
│   │   ├── dingtalk/        # OAuth client
│   │   ├── storage/         # MinIO client + verified resume objects
│   │   └── upload/          # streamed validation + malware seam
│   ├── security/            # JWT + PII (Fernet) crypto
│   └── tasks/               # Celery app
└── tests/
    ├── unit/                # deterministic unit tests
    └── integration/         # real-service integration tests
```

## P1 已就绪能力

- FastAPI 服务 + /healthz（DB/Redis/MinIO 三检）
- 钉钉一键登录路由（POST /auth/dingtalk/login）
- 9 张业务表 + pgvector HNSW 索引
- Fernet PII 加密 + sha256 去重哈希
- LLMGateway: extract/judge/lightweight + 主备模型 fallback + tenacity 重试
- MinIO 客户端 + 预签名 URL
- Celery + Redis broker
- 结构化日志 + trace id 中间件
- CORS（默认放行 localhost:3000，P3 用）

## P2 — 评分引擎

P2 完成了简历评分核心闭环：Excel 规则导入 → 解析 → 抽取 → 三段评分 → API。

### 一次性导入岗位规则

```bash
# 6 个岗位（外贸/物流/采购/QC/SQE/项目工程师）从 Excel 一键导入
uv run python -m backend.app.cli.import_rules import-rules 招聘JD整理-智能筛简历.xlsx
```

### 上传简历（异步）+ 评分

```bash
# 用钉钉授权码换取 JWT（示例需要 jq）
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/dingtalk/login \
  -H "Content-Type: application/json" \
  -d '{"auth_code":"<dingtalk-auth-code>"}' | jq -r .token)

# 上传简历：立即返回 202 + job_id，不在请求内解析/抽取/评分
curl -i -F "file=@resume.pdf" \
  -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8000/api/v1/candidates/upload
# 202 {"job_id": 123, "batch_id": null, "state": "queued"}

# 轮询任务状态，直到 state 落在 ready/completed（或 terminal_failed）
curl -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8000/api/v1/candidates/jobs/123
# {"state": "completed", "attempts": 1, "last_error_code": null,
#  "candidate_id": 45, "score_id": 9, "batch_id": null}

# 对指定岗位评分（同步，独立于上传/任务队列）
curl -X POST http://127.0.0.1:8000/api/v1/candidates/<id>/score \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jd_code": "FOREIGN_TRADE"}'
```

上传边界：

- 支持 PDF、DOCX、PNG、JPEG；旧版 DOC 暂不支持。
- 默认最大 20 MiB，可用 `MAX_RESUME_FILE_BYTES` 调整。
- 原文件使用不含 PII 的不可变键写入私有 MinIO，并校验大小和 SHA-256。
- 同一 SHA-256 的重复上传幂等：复用同一个非终止态 `ingestion_jobs` 行、返回同一个 `job_id`，本次重复对象会被清理，不会重复入队。
- `POST /upload` 自身的稳定错误码（校验/存储阶段，非任务失败）：`invalid_upload`、`file_too_large`、`unsupported_media_type`、`invalid_document`、`object_storage_unavailable`。任务在 worker 中失败时，稳定错误码写入任务的 `last_error_code`（见下）：`candidate_file_conflict`、`resume_parser_unavailable`、`resume_parser_contract_invalid`、`resume_parser_failed`、`ai_service_unavailable`、`ai_service_configuration_invalid`、`ai_invalid_output`，以及未分类异常的兜底码 `ingestion_worker_error`；
批量上传单文件遇到未分类异常时的兜底码是 `ingestion_failed`（见下方 WP3 批量上传）。

升级已有数据库后，部署前必须检查旧候选人的原文件元数据；非零结果需要先回填或隔离，不能宣称历史文件已完成持久化：

```sql
SELECT count(*) AS legacy_raw_file_rows
FROM candidates
WHERE raw_file_key IS NULL
   OR raw_file_sha256 IS NULL
   OR raw_file_size_bytes IS NULL
   OR raw_file_content_type IS NULL
   OR raw_file_original_name_cipher IS NULL;
```

### WP3 — 可恢复异步任务、批量上传与状态查询

简历解析/抽取/评分不再阻塞 HTTP 请求，改为经由 `ingestion_jobs` 状态机
（`queued -> parsing -> extracting -> (ready | scoring -> completed)`，失败分支
`retryable_failed`/`terminal_failed`）在 Celery worker 中异步处理：

- **`POST /api/v1/candidates/upload`** → `202 {job_id, batch_id, state}`：校验、恶意扫描、写入私有 MinIO、创建（或按 SHA-256 复用）`ingestion_jobs` 行后立即返回，并把 `job_id` 交给 `ingest.parse_and_score` 任务。
- **`POST /api/v1/candidates/batch`** → `202 {batch_id, jobs: [{job_id, state, error_code?}]}`：一次请求上传多个文件，共享一个 `batch_id`；每个文件独立校验/存储/入队。单个文件的校验或存储失败（或其他未分类异常）**只**在这个 `202` 响应体里同步报告为 `{state: "terminal_failed", error_code}`——该文件从未被写入 MinIO，也**不会**创建 `ingestion_jobs` 行；不影响批次内其他文件。批次大小上限由 `INGESTION_BATCH_MAX_FILES` 控制，超限返回 `413 batch_too_large`。
- **`GET /api/v1/candidates/jobs/{job_id}`** → `{state, attempts, last_error_code, candidate_id, score_id, batch_id}` 或 `404`。
- **`GET /api/v1/candidates/batches/{batch_id}`** → `{total, by_state}`（按状态聚合计数）或 `404`（无效 UUID 或没有任何任务属于该批次）。由于被拒绝的文件从未产生 `ingestion_jobs` 行，这个聚合结果只反映成功入队的任务；如果一个批次里的文件全部被拒绝（零个持久化任务），该接口会返回 `404`，与未知 `batch_id` 的情形一致。

**Celery Beat 回收/重试 sweeper**（`ingest.sweep`，每 `INGESTION_SWEEP_INTERVAL_SECONDS` 秒运行一次）：

1. 回收：处理中状态（`parsing`/`extracting`/`scoring`）且租约（`lease_expires_at`）已过期的任务——判定持有者 worker 已死——转入 `retryable_failed`。
2. 重新入队：`retryable_failed` 且 `attempts < INGESTION_MAX_ATTEMPTS` 的任务转回 `queued` 并重新提交给 Celery。
3. 终结：`retryable_failed` 且已达 `INGESTION_MAX_ATTEMPTS` 上限的任务转入 `terminal_failed`，不再重试。

Worker 崩溃恢复不会产生重复数据：候选人按 `pii_hash` 唯一，评分按
`(candidate_id, jd_id, rule_version_id)` 唯一（`uq_scores_candidate_jd_rule`
约束 + upsert），且一个任务若已经在崩溃前创建了候选人（`job.candidate_id`
已回填），重试时会跳过下载/解析/抽取，直接从评分/完成阶段继续——不会对同一
个已删除的原始对象重新发起下载。

**部署前的重复评分回归门禁**：在应用 `uq_scores_candidate_jd_rule` 唯一约束
的迁移前，必须先确认已部署数据库中没有违反该约束的历史行；非零结果需要先
回填/去重或隔离，不能直接迁移：

```sql
SELECT candidate_id, jd_id, rule_version_id, count(*) AS duplicate_count
FROM scores
GROUP BY candidate_id, jd_id, rule_version_id
HAVING count(*) > 1;
```

### WP4 — 只读 API（候选人/JD/规则版本）

在 WP1（JWT/RBAC）与 WP3（可恢复异步任务）之上新增的只读 HTTP 面，供 HR 客户端完成
“上传 → 轮询状态 → 列表 → 查看 → 重新评分”闭环而无需直接访问数据库。全部路由要求
Bearer JWT 且角色属于 `hr`、`hr_lead`、`admin`；未知资源统一返回 `404 {code, message}`。

**候选人两种列表**（均不解密 PII、不写审计日志）：

- 岗位维度排行榜：`GET /api/v1/jds/{code}/candidates?grade=&page=&page_size=` —— 返回该 JD **当前生效规则版本**下的评分结果，按 `total_score` 降序（`Score.id` 兜底稳定排序），每项 `{candidate_id, score_id, total_score, grade, rule_version, scored_at}`；JD 不存在返回 `404`。
- 全量候选人列表：`GET /api/v1/candidates?state=&page=&page_size=` —— 按创建时间倒序，`latest_state` 取该候选人最近一条 `ingestion_jobs` 的状态（无任务记录为 `null`），每项 `{candidate_id, created_at, latest_state, scored_jd_codes}`。

**候选人详情（PII，审计）**：`GET /api/v1/candidates/{id}` 解密姓名/电话/邮箱并返回
`{candidate_id, name, phone, email, age, education, experiences, source, created_at, scores}`；
每次调用精确写入一条 `event_type="pii_decrypt"` 的 `audit_logs` 记录（`actor`/`candidate_id`/
`purpose`/`trace_id`，不含明文）；候选人不存在返回 `404`。

**评分详情（评分卡，含证据）**：`GET /api/v1/candidates/{id}/scores/{score_id}` 返回
`{score_id, candidate_id, jd_code, rule_version, total_score, grade, hard_filter_result, rule_dimensions, judge_dimensions}`，其中 `judge_dimensions` 含每个维度的档位、分数、`evidence_quotes`、理由、置信度与建议追问；不属于该候选人的评分返回 `404`。评分卡本身不视为 PII 视图。

**原始文件预签名下载（审计）**：`GET /api/v1/candidates/{id}/raw-file` 返回
`{url, expires_in_seconds}`（默认 `RAW_FILE_PRESIGN_TTL_SECONDS=300` 秒的 MinIO 预签名 GET
URL），每次调用精确写入一条 `event_type="raw_file_access"` 的审计记录；预签名 URL 不写入日志；
候选人或原始对象不存在返回 `404`；MinIO 不可用返回 `503 object_storage_unavailable`。

**JD 列表/详情**：`GET /api/v1/jds?status=&page=&page_size=` 返回
`{code, name, status, active_rule_version}` 列表；`GET /api/v1/jds/{code}` 返回含
`active_rule_version: {id, version, published_at}` 的详情；未知 JD 返回 `404`。

**规则版本列表与结构化 diff**：`GET /api/v1/jds/{code}/rule-versions?page=&page_size=` 按
`published_at` 降序返回 `{id, version, published_at, published_by_user_id, notes,
golden_set_metrics, is_active}`（`is_active` 仅在 JD 当前生效版本上为 `true`）；
`GET /api/v1/jds/{code}/rule-versions/{from_version}/diff/{to_version}` 返回
`{jd_code, from_version, to_version, changes: [{path, kind, before, after}]}`，覆盖
`passing_threshold`、`hard_filters[id]`、`rule_dimensions[id]`、`judge_dimensions[id]`、
`grade_thresholds[grade]`，`kind` 为 `added`/`removed`/`changed`；JD 或任一版本不存在返回 `404`。

**分页**：所有列表接口使用统一的 offset 分页——`?page=`（从 1 开始，默认 1）与
`?page_size=`（默认 `READ_PAGE_SIZE_DEFAULT=20`，上限 `READ_PAGE_SIZE_MAX=100`），响应体统一
包裹为 `{items, page, page_size, total}`。

**读写边界**：除候选人详情与原始文件下载外，其余列表/详情接口**从不解密 PII、也从不写
审计日志**——只有这两个接口触发解密与审计。设计文档：
[`docs/superpowers/specs/2026-07-21-wp4-read-apis-design.md`](docs/superpowers/specs/2026-07-21-wp4-read-apis-design.md)；
实施计划：[`docs/superpowers/plans/2026-07-21-wp4-read-apis.md`](docs/superpowers/plans/2026-07-21-wp4-read-apis.md)。

### MinerU 解析器模式

`MINERU_MODE` 环境变量：

- `stub` — 本地开发/测试，返回固定 markdown
- `official` — 调用官方 MinerU API v4；需要 `MINERU_BASE_URL=https://mineru.net`、`MINERU_API_KEY`，默认模型为 `vlm`

详见 `docs/specs/research/mineru.md`。

### new-api 结构化输出

生产环境配置 `NEWAPI_BASE_URL`、`NEWAPI_API_KEY`、抽取/评分主模型和回退模型，
并使用 `LLM_STRUCTURED_OUTPUT_MODE=json_schema`。`json_object` 仅用于兼容不支持
严格 JSON Schema 的网关；两种模式都会经过相同的本地 Pydantic 和证据校验。

密钥只放在未跟踪的 `.env.local` 或生产密钥服务中，不得写入仓库、日志或运行证据。

### 外部契约验证与回滚

默认测试完全离线。使用合成、无个人信息的 PDF、DOCX、PNG 和 JPEG 调用真实
MinerU/new-api 时，显式运行：

```powershell
uv run python scripts/verify_external_contracts.py
```

该命令缺少凭据、使用 `MINERU_MODE=stub`、发生测试跳过或端点不符合契约时都会
失败。提交的运行证据只记录端点环境、API/模型版本和测试计数，不保留批次 ID、
签名 URL、请求/响应正文、Prompt、Completion 或候选人信息。

解析或 AI 校验失败会回滚候选人/评分事务并清理对象和临时文件。生产回滚使用上一
应用镜像；不会自动回退到未经验证的旧 `/file_parse`，`MINERU_MODE=stub` 也不能
作为生产回滚模式。

### 后续工作范围

- 段 D 双引擎交叉打分（cross_engine_diff / is_suspicious 字段已存模型，本期始终 None/False）
- 规则版本受控发布（写入工作流）、What-If 规则模拟、黄金集回归（设计 §6）；只读的规则版本列表与 diff 已随 WP4 上线，见上文
- 钉钉招聘文档 API 同步任务（设计 §8.2）
- HR 复核反馈回流（设计 §7）

## WP5 — HR Web 工作台（前端）

`frontend/` 是面向 HR 的 Web 工作台：Next.js 15（App Router）+ React 19，作为 BFF
（Backend-for-Frontend）代理 WP1/WP4 的 FastAPI 接口——浏览器不直接持有或看到
Bearer JWT。技术栈：Tailwind CSS v4、shadcn/ui（`@base-ui/react` 基座）、TanStack
Query（服务端数据获取/缓存）、zod（响应体运行时校验，`src/lib/schemas.ts`）、
Vitest + Testing Library（单元/组件测试）、Playwright + `@axe-core/playwright`
（端到端与无障碍）。页面：登录（钉钉一键登录）、候选人列表、候选人详情、评分卡、
JD 详情、上传。

### 开发/构建/测试命令

进入 `frontend/`：

```bash
npm install
npm run dev          # 开发服务器（Turbopack）
npm run build         # 生产构建（next build --turbopack；产出 .next/standalone）
npm run start           # 生产模式启动已构建的应用
npm run lint             # eslint
npm run typecheck        # tsc --noEmit
npm run test              # vitest run（单元/组件）
npm run e2e                # playwright test（desktop + mobile 两套 project）
```

`npm run e2e` 的 Playwright `webServer` 会自动执行 `npm run build && npm run start
-- -p 4173`（3000/3100 等常规端口在本机被 Docker Desktop 占用，固定改用 4173，见
`playwright.config.ts`）。e2e 用例通过 `page.route` 桩接浏览器发往 `/api/proxy/**`
的请求，并用 `e2e/helpers/session.ts` 的 `mintSession()` 铸造与生产同算法
（HMAC-SHA256）签名的 `ssa_session` cookie，覆盖"候选人列表 → 详情（PII）→ 评分卡
（含证据）"这条金路径以及无障碍检查；真实的钉钉登录握手（服务端向 FastAPI 换取
token）不可被 Playwright 拦截，改由 `src/lib/server/session.test.ts` 等单测覆盖。

### BFF 鉴权模型

- 登录页拿到钉钉 `auth_code` 后 `POST /api/auth/callback`；该路由在服务端调用
  FastAPI `POST /auth/dingtalk/login` 换取 JWT，再用 `SESSION_COOKIE_SECRET` 对
  `{token, displayName, role}` 做 HMAC-SHA256 签名，写入 httpOnly、`sameSite=lax`
  （生产环境额外 `secure`）的 `ssa_session` cookie（`src/lib/server/session.ts`）。
- `(app)/layout.tsx` 在服务端读取并校验 `ssa_session`；校验失败直接重定向登录页，
  不渲染任何受保护内容。
- 业务请求统一经服务端代理转发：`GET/POST /api/proxy/[...path]`
  （`src/app/api/proxy/[...path]/route.ts`）从 cookie 中取出 `session.token`，以
  `Authorization: Bearer` 附加后转发给 `API_BASE_URL`；浏览器发出的请求本身不带
  token。
- 原始简历文件走独立的 `GET /api/candidates/[id]/raw-file`：服务端向 FastAPI 换取
  一次性预签名 MinIO URL 后直接 302 重定向浏览器——预签名 URL 不进入客户端 JS
  状态，也不写入日志。
- 登出 `POST /api/auth/logout` 清除 `ssa_session` cookie。

### 环境变量（`frontend/.env.example`）

| 变量 | 说明 |
| --- | --- |
| `API_BASE_URL` | 后端 FastAPI 地址；仅服务端读取，从不下发到浏览器 |
| `SESSION_COOKIE_SECRET` | 签名 `ssa_session` cookie 的 HMAC-SHA256 密钥（≥32 字节随机串）；轮换会使已签发会话全部失效 |
| `DINGTALK_CLIENT_ID` | 钉钉登录使用的 AppKey，须与后端交换 `auth_code` 时使用的一致 |
| `DINGTALK_REDIRECT_URI` | 钉钉 OAuth 回调地址 |
| `DINGTALK_AUTHORIZE_URL` | 可选，钉钉 OAuth 授权端点，默认为 `https://login.dingtalk.com/oauth2/auth` |

### PII / 审计边界

前端严格遵循 WP4 已实现的读取边界，不引入新的解密或审计路径：

- **候选人列表**（`/candidates`、JD 维度排行榜）只消费 `GET /api/v1/candidates`、
  `GET /api/v1/jds/{code}/candidates`——不含姓名/电话/邮箱，从不触发解密或审计
  日志。
- **候选人详情** `/candidates/[id]` 消费 `GET /api/v1/candidates/{id}`，是唯一
  渲染姓名/电话/邮箱等 PII 的页面，对应后端每次调用精确写入一条 `pii_decrypt`
  审计记录。
- **评分卡** `/candidates/[id]/scores/[sid]` 展示评分维度、依据与证据引用
  （`evidence_quotes`），不是 PII 视图。
- **原始文件下载**只走上述服务端 302 重定向，前端代码从不落地或展示预签名
  URL 本身。

### Docker

`frontend/Dockerfile` 是两阶段镜像（`node:22-alpine` 构建 + 运行，运行阶段以
非 root 的 `node` 用户执行），依赖 `next.config.ts` 的 `output: "standalone"`；
仓库根 `docker-compose.yml` 的 `frontend` service 构建该镜像，并通过
`host.docker.internal` 访问宿主机运行的 FastAPI 后端（FastAPI/worker/beat 均不在
compose 内，见上文"Quick start"）。

## WP6a — HR 复核反馈与最小报表

WP6a 在 WP4/WP5 之上加入人工复核闭环：HR 对每条评分记录一个裁决（`advance`/
`reject`/`hold`）与理由，服务端派生该裁决是否与 AI 结论一致（`ai_agreed`），并
提供一个只读的 AI-HR 一致性聚合报表。不改变评分本身——反馈是独立记录，从不
写回 `scores` 表。

**数据模型与约束**：复用既有的 `feedback` 表（`score_id`、`reviewer_user_id`、
`decision`、`reason`、`ai_agreed`、`created_at`、`updated_at`），新增迁移
[`1e9b39dbf340_wp6a_feedback_constraints.py`](migrations/versions/1e9b39dbf340_wp6a_feedback_constraints.py)
添加 `(score_id, reviewer_user_id)` 唯一约束（同一评分允许多个不同复核人各提
交一条，同一复核人重复提交则更新）与 `decision` 的 CHECK 约束。

**`ai_agreed` 派生规则**（服务端计算，从不信任客户端传入）：AI 判定拒绝当且
仅当 `score.grade == "rejected"`；`decision == "hold"` 时 `ai_agreed = null`
（不计入一致率）；否则 `ai_agreed = (AI 是否拒绝 == HR 是否拒绝)`。当
`ai_agreed` 为 `false`（即与 AI 不一致）而未填写理由时，返回
`422 {code: "feedback_reason_required"}`。

**接口**（均要求 Bearer JWT，角色 `hr`/`hr_lead`/`admin`）：

- `PUT /api/v1/candidates/{id}/scores/{score_id}/feedback` —— upsert 当前用户
  对该评分的复核（`{decision, reason}` → `FeedbackItem`，含服务端派生的
  `ai_agreed`）；评分不属于该候选人返回 `404`。
- `GET /api/v1/candidates/{id}/scores/{score_id}/feedback` —— 列出该评分的全部
  复核记录（`FeedbackItem[]`）。
- `GET /api/v1/feedback/report?jd_code=&page=&page_size=` —— 聚合报表：
  `overall`（总体一致率统计）、`by_jd`（按 JD 拆分）、`disagreements`（不一致
  明细的分页列表，仅含 `candidate_id`/`jd_code`/`score_id`/裁决/理由/复核人，
  不含姓名/电话/邮箱等 PII、不解密、不触发审计日志）。

**前端**：评分卡页新增 `FeedbackPanel`（`frontend/src/components/feedback-panel.tsx`）
——选择裁决、必要时填写理由、提交后展示该评分下每位复核人的记录与是否与 AI
一致；新增报表页 `/reports/feedback`（`frontend/src/app/(app)/reports/feedback/page.tsx`），
展示总体一致率、按 JD 一致率与不一致明细表，导航栏新增入口。两者都复用既有的
`apiGet`/`apiPut` BFF 代理与 zod 响应校验（`FeedbackItem`/`FeedbackList`/
`FeedbackReport`，见 `frontend/src/lib/schemas.ts`）。

**测试**：后端单元覆盖 `ai_agreed` 派生与报表聚合算法；集成测试覆盖
upsert/唯一约束更新、按分数列出、理由必填的 `422`、评分不存在的 `404`、报表
的 overall/by_jd/disagreements 分页与 PII 边界。前端单元测试覆盖
`FeedbackPanel` 与报表页组件；新增的 Playwright e2e
[`frontend/e2e/feedback.spec.ts`](frontend/e2e/feedback.spec.ts) 用桩接的
`/api/proxy/api/v1/feedback/report` 响应驱动 `/reports/feedback`，断言一致率
正确渲染（desktop + mobile 两套 project）。设计文档：
[`docs/superpowers/specs/2026-07-22-wp6a-feedback-capture-design.md`](docs/superpowers/specs/2026-07-22-wp6a-feedback-capture-design.md)；
实施计划：[`docs/superpowers/plans/2026-07-22-wp6a-feedback-capture.md`](docs/superpowers/plans/2026-07-22-wp6a-feedback-capture.md)。

WP6a 已通过本地全量门禁（后端 offline/integration/ruff/mypy，前端
lint/typecheck/vitest/Playwright e2e/build），托管 CI 验收后标记为 Complete。
