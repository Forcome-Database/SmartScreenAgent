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
