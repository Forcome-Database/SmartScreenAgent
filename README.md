# SmartScreenAgent

AI-driven resume screening agent for HR.

## 状态

当前处于“后端评分原型已完成、生产化加固进行中”阶段。

**WP0 可重复集成基线已完成**。**WP1 安全与原文件完整性已经完成本地实现和严格验证，等待托管 CI 验收**：候选人写接口已强制 JWT/RBAC，上传会经过流式大小/类型/文件签名校验并持久化到私有 MinIO。

项目仍不能直接公网部署：WP2 尚需验证真实 MinerU 契约和 AI 输出，WP3 尚需把同步处理切换为可恢复的异步任务。当前状态和后续依赖以 [`docs/superpowers/specs/2026-07-13-current-state-and-roadmap-design.md`](docs/superpowers/specs/2026-07-13-current-state-and-roadmap-design.md) 为准。

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

# 7. 验证
curl http://127.0.0.1:8000/healthz
# 应返回 {"status":"ok","checks":{"db":"ok","redis":"ok","minio":"ok"}}

# 8. 跑测试
uv run pytest                 # 全部
uv run pytest -m "not integration"  # 仅单元
```

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

`MINERU_MODE=stub` 用于本地离线开发；`MINERU_MODE=http` 用于对接独立 `mineru-api` 服务。`library` 模式仍未实现。

候选人上传和评分 API 已要求 Bearer JWT，允许角色为 `hr`、`hr_lead`、`admin`。真实 MinerU 和 AI 输出契约尚未完成生产验证，因此仍不能直接公网部署。

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

### 上传简历 + 评分（同步）

```bash
# 用钉钉授权码换取 JWT（示例需要 jq）
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/dingtalk/login \
  -H "Content-Type: application/json" \
  -d '{"auth_code":"<dingtalk-auth-code>"}' | jq -r .token)

# 上传简历，返回 candidate_id 和 parsed/duplicate 状态
curl -F "file=@resume.pdf" \
  -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8000/api/v1/candidates/upload

# 对指定岗位评分
curl -X POST http://127.0.0.1:8000/api/v1/candidates/<id>/score \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jd_code": "FOREIGN_TRADE"}'
```

上传边界：

- 支持 PDF、DOCX、PNG、JPEG；旧版 DOC 暂不支持。
- 默认最大 20 MiB，可用 `MAX_RESUME_FILE_BYTES` 调整。
- 原文件使用不含 PII 的不可变键写入私有 MinIO，并校验大小和 SHA-256。
- 重复身份返回 `status: duplicate`，本次重复对象会被清理。
- 稳定错误码包括 `invalid_upload`、`file_too_large`、`unsupported_media_type`、`invalid_document`、`candidate_file_conflict`、`resume_parser_failed`、`object_storage_unavailable`。

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

### MinerU 解析器三种模式

`MINERU_MODE` 环境变量：

- `stub` — 本地开发/测试，返回固定 markdown
- `http` — 调远端 mineru-api 服务（推荐生产）；需要 `MINERU_BASE_URL`、`MINERU_API_KEY`
- `library` — 直接 import mineru 库（暂未实现，留 P3）

详见 `docs/specs/research/mineru.md`。

### P2 未覆盖范围（→ P3）

- 段 D 双引擎交叉打分（cross_engine_diff / is_suspicious 字段已存模型，本期始终 None/False）
- What-If 规则模拟、规则版本 diff、黄金集回归（设计 §6）
- 钉钉招聘文档 API 同步任务（设计 §8.2）
- 评分卡 Web UI 与所有前端页面（设计 §10）
- HR 复核反馈回流（设计 §7）
- 真实 MinerU 提交/轮询/产物契约与 AI 输出强校验尚未完成（WP2）
- Prompt injection 清洗仅覆盖 3 个经典 pattern；WP2 继续扩展并验证 AI 输出边界
