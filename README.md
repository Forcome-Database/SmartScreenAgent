# SmartScreenAgent

AI-driven resume screening agent for HR.

## 状态

当前处于“后端评分原型已完成、生产化加固进行中”阶段。

**WP0 可重复集成基线、WP1 安全与原文件完整性、WP2 生产解析器契约与校验 AI 输出均已完成并通过托管 CI**：候选人写接口已强制 JWT/RBAC，上传经流式大小/类型/文件签名校验并持久化到私有 MinIO；MinerU 已切换到官方 API v4，简历抽取与 LLM judge 输出经严格 Pydantic 与证据溯源校验后才能落库。WP2 托管验收见 [`verify` run 29714208508](https://github.com/Forcome-Database/SmartScreenAgent/actions/runs/29714208508)。

**WP3 可恢复异步任务实现已完成，本地验证通过，托管 CI 验证进行中**：候选人上传接口已切换为异步——`POST /candidates/upload` 立即返回 `202 {job_id}` 并把简历交给 `ingestion_jobs` 状态机和 Celery worker（`ingest.parse_and_score`）处理；Celery Beat 定期运行回收/重试 sweeper（`ingest.sweep`），处理中租约过期的任务会被回收并按 `INGESTION_MAX_ATTEMPTS` 重试或终结，不会产生重复候选人或评分。读 API（WP4）与前端（WP5）尚未开始。当前状态和后续依赖以 [`docs/superpowers/specs/2026-07-13-current-state-and-roadmap-design.md`](docs/superpowers/specs/2026-07-13-current-state-and-roadmap-design.md) 为准。

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
- What-If 规则模拟、规则版本 diff、黄金集回归（设计 §6）
- 钉钉招聘文档 API 同步任务（设计 §8.2）
- 评分卡 Web UI 与所有前端页面（设计 §10）
- HR 复核反馈回流（设计 §7）
