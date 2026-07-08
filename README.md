# SmartScreenAgent

AI-driven resume screening agent for HR.

## 状态

P1 后端地基（W1-W2）— 完成

下一步：P2 评分引擎（MinerU 简历解析 + 三段式打分）。

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
# 不依赖外部容器的测试
uv run pytest -m "not integration"

# 启动 PostgreSQL / Redis / MinIO
docker compose up -d
docker compose ps

# 集成测试与全量测试
uv run pytest -m integration
uv run pytest

# 质量门禁
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
```

`MINERU_MODE=stub` 用于本地离线开发；`MINERU_MODE=http` 用于对接独立 `mineru-api` 服务。`library` 模式仍未实现。

P2 的候选人上传和评分 API 尚未强制 JWT/RBAC，不能直接公网部署。

## 设计文档

- 设计稿：`docs/specs/2026-05-12-resume-screening-agent-design.md`
- P1 计划：`docs/specs/plans/2026-05-12-p1-backend-foundation.md`
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
│   │   └── storage/         # MinIO client
│   ├── security/            # JWT + PII (Fernet) crypto
│   └── tasks/               # Celery app
└── tests/
    ├── unit/                # ~24 tests
    └── integration/         # ~2 tests (alembic, smoke)
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
# 上传简历（PDF/Word/图片），返回 candidate_id
curl -F "file=@resume.pdf" http://localhost:8000/api/v1/candidates/upload

# 对指定岗位评分
curl -X POST http://localhost:8000/api/v1/candidates/<id>/score \
  -H "Content-Type: application/json" \
  -d '{"jd_code": "FOREIGN_TRADE"}'
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
- **API 暂未挂 JWT/RBAC**（设计 §11.3）—— 切勿直接公网部署，P3 接入 DingTalk OAuth 后启用
- Prompt injection 清洗仅覆盖 3 个经典 pattern；P3 单独做 `docs/specs/research/prompt-injection.md` 调研扩展
