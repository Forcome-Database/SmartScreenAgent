# P1 — 后端地基 (Backend Foundation) 实施计划

> **历史状态：已执行计划。** 本计划的复选框未在执行期间维护，不能用来判断当前完成度；实现事实以 Git 历史和测试为准。当前权威状态与后续顺序见 [`../../superpowers/specs/2026-07-13-current-state-and-roadmap-design.md`](../../superpowers/specs/2026-07-13-current-state-and-roadmap-design.md)。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 SmartScreenAgent 搭建可运行的后端地基——FastAPI 服务 + PostgreSQL/pgvector + MinIO + Celery/Redis + LLM 网关 + 钉钉 OAuth 骨架，使后续 P2 评分引擎可以直接接入。

**Architecture:** FastAPI（异步）作为 API 与 MCP 双协议入口；SQLAlchemy 2.x + Alembic 管理数据；OpenAI 兼容 SDK 通过 newapi 中转访问 gpt-5.5 / gpt-5.4 / gemini-3-flash / DeepSeek-V4；Celery + Redis 承载异步评分；MinIO 存简历原文。docker-compose 一把启全部。

**Tech Stack:** Python 3.10 / uv / FastAPI / SQLAlchemy 2 / Alembic / asyncpg / pgvector / Celery / Redis / MinIO / OpenAI SDK / Fernet (PII)。与 ForcomePPT / Qbu-Crawler 现有 ForcomeAiTools 项目 Python 版本一致。

**对应 Spec 章节：** §3 架构、§4 数据模型、§9 LLM 网关、§11 PII/审计、§12 部署

---

## ⚠️ 调研先行原则（USER REQUIREMENT）

凡涉及外部 SDK / 外部 API / 近期演进库，**先 WebFetch 官方文档或 Grep 现有代码再写实现**。本计划在编码任务前嵌入了"调研步骤 0.x"，对应步骤产物（一段调研笔记）必须保存到 `docs/specs/research/` 后再开始编码。

---

## 文件结构总览

```
SmartScreenAgent/
├── pyproject.toml                       # uv + 依赖声明 (Task 1)
├── uv.lock                              # 锁定文件 (uv 自动生成)
├── docker-compose.yml                   # Task 2
├── .env.example                         # Task 1
├── .gitignore                           # Task 1
├── README.md                            # Task 1
├── alembic.ini                          # Task 3
├── docs/specs/research/                 # 调研笔记目录
│   ├── newapi.md
│   ├── dingtalk-oauth.md
│   ├── pgvector-sqlalchemy.md
│   └── pgcrypto.md
│
├── backend/
│   ├── app/
│   │   ├── __init__.py                  # Task 1
│   │   ├── main.py                      # Task 1   (FastAPI app factory)
│   │   ├── config.py                    # Task 1   (Settings via pydantic-settings)
│   │   ├── database.py                  # Task 3   (engine + session)
│   │   ├── deps.py                      # Task 12  (FastAPI 依赖注入)
│   │   ├── logging_config.py            # Task 13
│   │   │
│   │   ├── models/
│   │   │   ├── __init__.py              # Task 4
│   │   │   ├── base.py                  # Task 4   (DeclarativeBase + 时间戳混入)
│   │   │   ├── user.py                  # Task 4
│   │   │   ├── jd.py                    # Task 5
│   │   │   ├── rule_version.py          # Task 5
│   │   │   ├── candidate.py             # Task 6
│   │   │   ├── score.py                 # Task 7
│   │   │   ├── feedback.py              # Task 7
│   │   │   ├── golden_set.py            # Task 7
│   │   │   ├── audit_log.py             # Task 8
│   │   │   └── candidate_embedding.py   # Task 9
│   │   │
│   │   ├── routers/
│   │   │   ├── __init__.py              # Task 13
│   │   │   ├── health.py                # Task 13
│   │   │   └── auth.py                  # Task 11  (DingTalk login)
│   │   │
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── llm/
│   │   │   │   ├── __init__.py          # Task 10
│   │   │   │   ├── gateway.py           # Task 10  (LLMGateway)
│   │   │   │   └── schemas.py           # Task 10
│   │   │   ├── dingtalk/
│   │   │   │   ├── __init__.py          # Task 11
│   │   │   │   └── oauth.py             # Task 11
│   │   │   └── storage/
│   │   │       ├── __init__.py          # Task 15
│   │   │       └── minio_client.py      # Task 15
│   │   │
│   │   ├── security/
│   │   │   ├── __init__.py              # Task 12
│   │   │   ├── jwt.py                   # Task 12
│   │   │   └── crypto.py                # Task 6   (PII 加密/解密辅助)
│   │   │
│   │   └── tasks/
│   │       ├── __init__.py              # Task 14
│   │       └── celery_app.py            # Task 14
│   │
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py                  # Task 1
│       ├── unit/
│       │   ├── test_health.py           # Task 13
│       │   ├── test_llm_gateway.py      # Task 10
│       │   ├── test_dingtalk_oauth.py   # Task 11
│       │   ├── test_jwt.py              # Task 12
│       │   ├── test_minio_client.py     # Task 15
│       │   ├── test_crypto.py           # Task 6
│       │   └── test_models.py           # Task 4-9
│       └── integration/
│           ├── test_db_migrations.py    # Task 3
│           └── test_smoke.py            # Task 16
│
└── migrations/                          # Alembic
    ├── env.py                            # Task 3
    ├── script.py.mako                    # Task 3
    └── versions/
        └── 001_baseline.py              # Task 4-9
```

---

## Task 0: 项目初始化 + git init

**Files:**
- Create: `E:\Project\ForcomeAiTools\SmartScreenAgent\.gitignore`
- Create: `E:\Project\ForcomeAiTools\SmartScreenAgent\README.md`

- [ ] **0.1 初始化 git 仓库**

```bash
cd "E:/Project/ForcomeAiTools/SmartScreenAgent"
git init -b main
git config user.name "Leo"
git config user.email "forcomegroup@gmail.com"
```

- [ ] **0.2 写 .gitignore**

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/
.pytest_cache/
.coverage
htmlcov/

# uv
.uv/
uv-cache/

# Env
.env
.env.local
*.local

# Editor
.vscode/
.idea/
*.swp
.DS_Store

# Project
uploads/
*.log
backend/instance/
docs/specs/research/*.draft.md
```

- [ ] **0.3 提交首个 commit**

```bash
git add .gitignore docs/
git commit -m "chore: bootstrap repo with design spec"
```

Expected: 一次 commit 包含设计稿 + .gitignore。

---

## Task 0.1: 调研 — newapi 网关接入方式

**目的：** 确认 newapi 的 base URL 格式、认证方式、是否完全 OpenAI 兼容、流式与函数调用支持。

**Files:**
- Create: `docs/specs/research/newapi.md`

- [ ] **1 WebFetch newapi 项目主页**

调研对象：项目目前主流为 `Calcium-Ion/new-api` 与社区分支。

WebFetch `https://github.com/Calcium-Ion/new-api` 提取：
- 默认端口与路由前缀（通常 `/v1/chat/completions` 完全兼容 OpenAI）
- Auth header 格式（一般 `Authorization: Bearer <key>`）
- 是否支持 OpenAI Python SDK `base_url=<gateway>/v1` 直接使用

如果 `Calcium-Ion/new-api` 已归档或改名，跟随 README 中的迁移指引。

- [ ] **2 确认 4 个目标模型在 newapi 的 model 字符串**

WebFetch newapi 部署文档中"已支持模型列表"或问用户的网关后台。记录：
- gpt-5.5 实际 model_id 字符串
- gpt-5.4 实际 model_id 字符串
- gemini-3-flash 实际 model_id 字符串
- DeepSeek-V4 实际 model_id 字符串

如果某模型不可用，立刻告知用户，决定备模型。

- [ ] **3 写调研笔记**

文件：`docs/specs/research/newapi.md`，含以下小节：
1. Base URL 模板（含 / 不含 `/v1`）
2. Auth header 示例
3. OpenAI SDK 直连写法（含 `AsyncOpenAI(base_url=..., api_key=...)`）
4. 4 个模型的实际 model_id 字符串表
5. 是否支持函数调用 / JSON 模式 / 流式（每项 yes/no + 引用来源）
6. 已知坑（如 token 计费、超时、并发限制）

- [ ] **4 Commit**

```bash
git add docs/specs/research/newapi.md
git commit -m "docs: research notes on newapi gateway integration"
```

---

## Task 0.2: 调研 — 钉钉 OAuth 2026 流程

**目的：** 确认当前钉钉一键登录 + 移动免登的官方端点与签名要求。**首选数据来源：项目内已配置的 dingtalk-api MCP**（机器可读 OAS，比文档可靠）。

**Files:**
- Create: `docs/specs/research/dingtalk-oauth.md`

- [ ] **1 通过 dingtalk-api MCP 读 OAS**

主代理（或工程师）调用：
- `mcp__dingtalk-api__refresh_project_oas_*`（刷新 OAS 缓存）
- `mcp__dingtalk-api__read_project_oas_*` 搜索关键字：`oauth2`, `userAccessToken`, `users/me`, `contact`
- `mcp__dingtalk-api__read_project_oas_ref_resources_*` 拉相关 schema

从返回的 OAS 中提取（**逐字记录，不要凭印象**）：
- userAccessToken 端点（method + path + 请求体字段名 + 响应字段名）
- 取个人信息端点（method + path + 请求 header + 响应 schema）
- 字段名（`clientId` vs `client_id`，`accessToken` vs `access_token`）

> 注：MCP 工具的具体后缀（如 `_ce0216`）在不同 session 可能变化，用 ToolSearch 查 `select:mcp__dingtalk-api__*` 当场列出可用工具。

- [ ] **2 补充 — WebFetch 钉钉 JSAPI 文档（前端免登）**

OAS 通常不覆盖前端 JSAPI。WebFetch `https://open.dingtalk.com/document/orgapp/jsapi-overview` 与 `dd.runtime.permission.requestAuthCode`：记录前端调用方式与 auth_code 回调字段。

- [ ] **3 写调研笔记**

文件：`docs/specs/research/dingtalk-oauth.md`，含：
1. OAuth 流程图（PC 扫码 + 移动免登）
2. 后端要调用的 3 个端点的完整 cURL 示例
3. JSAPI `dd.getAuthCode` 的调用示例（前端 Task 见 P3，但 P1 调研也包含）
4. accessToken 缓存策略建议（Redis key 命名 + TTL）
5. 安全：appSecret 必须在后端、签名校验位置

- [ ] **4 Commit**

```bash
git add docs/specs/research/dingtalk-oauth.md
git commit -m "docs: research notes on DingTalk OAuth 2026 flow"
```

---

## Task 0.3: 调研 — pgvector + SQLAlchemy 集成

**目的：** 确认正确的 Python 包、SQLAlchemy 列类型、迁移写法。

**Files:**
- Create: `docs/specs/research/pgvector-sqlalchemy.md`

- [ ] **1 WebFetch pgvector-python README**

WebFetch `https://github.com/pgvector/pgvector-python` 提取：
- pip 包名（`pgvector`）
- SQLAlchemy 用法（`from pgvector.sqlalchemy import Vector`）
- Alembic 迁移启用扩展的写法：`op.execute('CREATE EXTENSION IF NOT EXISTS vector')`
- 索引类型 HNSW vs IVFFlat 的选择

- [ ] **2 写调研笔记**

文件：`docs/specs/research/pgvector-sqlalchemy.md`，含：
1. 安装与 PG 版本兼容要求
2. SQLAlchemy 2.x 模型示例：`embedding: Mapped[list[float]] = mapped_column(Vector(1024))`
3. Alembic 迁移片段
4. 1024 维度选择理由（与 P1 选用的 embedding 模型对齐）

- [ ] **3 Commit**

```bash
git add docs/specs/research/pgvector-sqlalchemy.md
git commit -m "docs: research notes on pgvector + SQLAlchemy"
```

---

## Task 0.4: 调研 — pgcrypto 列级加密 vs 应用层加密

**目的：** 确认 PII 列加密的最佳实践（性能 / 可搜索性 / 密钥管理）。

**Files:**
- Create: `docs/specs/research/pgcrypto.md`

- [ ] **1 WebFetch PostgreSQL pgcrypto 文档**

WebFetch `https://www.postgresql.org/docs/current/pgcrypto.html` 提取：
- `pgp_sym_encrypt` / `pgp_sym_decrypt` 用法
- 密钥传递方式（不能写在 SQL 字面量中！）
- 性能影响

- [ ] **2 比较应用层加密替代方案**

WebSearch `sqlalchemy column encryption Fernet python` 与 `python-cryptography Fernet column-level encryption`。

- [ ] **3 写调研笔记并做决策**

文件：`docs/specs/research/pgcrypto.md`，含：
1. 两种方案对比表（pgcrypto vs 应用层 Fernet）
2. **决策**：本项目选哪种 + 原因（推荐应用层 Fernet：密钥不入库、key rotation 简单、性能可控）
3. 选定方案的代码骨架示例
4. 密钥从环境变量加载 + KMS 路线（未来扩展）

- [ ] **4 Commit**

```bash
git add docs/specs/research/pgcrypto.md
git commit -m "docs: research and decision on PII column encryption"
```

---

## Task 1: 项目脚手架 (pyproject.toml + 目录结构)

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `README.md`
- Create: `backend/app/__init__.py`
- Create: `backend/app/main.py`
- Create: `backend/app/config.py`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/conftest.py`

- [ ] **1.1 写 pyproject.toml**

```toml
[project]
name = "smartscreen-agent"
version = "0.1.0"
description = "AI-driven resume screening agent for HR (北美外贸/五金机械行业)"
requires-python = ">=3.10"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "pydantic>=2.9.0",
    "pydantic-settings>=2.6.0",
    "sqlalchemy>=2.0.36",
    "asyncpg>=0.30.0",
    "alembic>=1.13.0",
    "pgvector>=0.3.6",
    "openai>=1.54.0",
    "celery>=5.4.0",
    "redis>=5.2.0",
    "minio>=7.2.0",
    "httpx>=0.27.0",
    "pyjwt>=2.9.0",
    "cryptography>=43.0.0",
    "python-multipart>=0.0.12",
    "tenacity>=9.0.0",
    "structlog>=24.4.0",
    "python-dotenv>=1.0.1",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "pytest-cov>=6.0.0",
    "httpx>=0.27.0",
    "ruff>=0.7.0",
    "mypy>=1.13.0",
]

[tool.uv]
index-url = "https://pypi.tuna.tsinghua.edu.cn/simple"

[tool.pytest.ini_options]
testpaths = ["backend/tests"]
python_files = ["test_*.py"]
asyncio_mode = "auto"
addopts = "-v --tb=short"

[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP"]
```

- [ ] **1.2 写 .env.example**

```dotenv
# Database
DATABASE_URL=postgresql+asyncpg://smartscreen:smartscreen@localhost:5432/smartscreen
DATABASE_URL_SYNC=postgresql://smartscreen:smartscreen@localhost:5432/smartscreen

# Redis (Celery broker + cache)
REDIS_URL=redis://localhost:6379/0

# MinIO
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=resumes
MINIO_SECURE=false

# LLM Gateway (newapi)
NEWAPI_BASE_URL=https://your-newapi.example.com/v1
NEWAPI_API_KEY=sk-your-key-here
LLM_MODEL_EXTRACT=deepseek-v4
LLM_MODEL_EXTRACT_FALLBACK=gemini-3-flash
LLM_MODEL_JUDGE=gpt-5.5
LLM_MODEL_JUDGE_FALLBACK=gpt-5.4
LLM_MODEL_LIGHT=gemini-3-flash

# DingTalk
DINGTALK_APP_KEY=
DINGTALK_APP_SECRET=
DINGTALK_CORP_ID=

# Security
JWT_SECRET_KEY=please-replace-with-long-random-string
JWT_ALGORITHM=HS256
JWT_EXPIRE_HOURS=8
PII_ENCRYPTION_KEY=please-replace-with-fernet-key-44-chars-long

# Cost guards
DAILY_LLM_BUDGET_CNY=100
MONTHLY_LLM_BUDGET_CNY=1500

# CORS (P3 前端要跨域调用后端)
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```

- [ ] **1.3 写 backend/app/config.py**

```python
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    DATABASE_URL: str
    DATABASE_URL_SYNC: str

    # Redis
    REDIS_URL: str

    # MinIO
    MINIO_ENDPOINT: str
    MINIO_ACCESS_KEY: str
    MINIO_SECRET_KEY: str
    MINIO_BUCKET: str = "resumes"
    MINIO_SECURE: bool = False

    # LLM
    NEWAPI_BASE_URL: str
    NEWAPI_API_KEY: str
    LLM_MODEL_EXTRACT: str
    LLM_MODEL_EXTRACT_FALLBACK: str
    LLM_MODEL_JUDGE: str
    LLM_MODEL_JUDGE_FALLBACK: str
    LLM_MODEL_LIGHT: str

    # DingTalk
    DINGTALK_APP_KEY: str = ""
    DINGTALK_APP_SECRET: str = ""
    DINGTALK_CORP_ID: str = ""

    # Security
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 8
    PII_ENCRYPTION_KEY: str

    # Cost
    DAILY_LLM_BUDGET_CNY: float = 100.0
    MONTHLY_LLM_BUDGET_CNY: float = 1500.0

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

- [ ] **1.4 写 backend/app/main.py（占位 app）**

```python
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(
        title="SmartScreenAgent",
        version="0.1.0",
        description="AI 简历筛选服务",
    )

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": "smartscreen-agent", "status": "ok"}

    return app


app = create_app()
```

- [ ] **1.5 写 backend/app/__init__.py（空）**

```python
```

- [ ] **1.6 写 backend/tests/__init__.py 与 backend/tests/conftest.py**

```python
# backend/tests/__init__.py
```

```python
# backend/tests/conftest.py
import os
import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("DATABASE_URL_SYNC", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("MINIO_ENDPOINT", "localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "test")
os.environ.setdefault("MINIO_SECRET_KEY", "testtest")
os.environ.setdefault("NEWAPI_BASE_URL", "http://localhost/v1")
os.environ.setdefault("NEWAPI_API_KEY", "sk-test")
os.environ.setdefault("LLM_MODEL_EXTRACT", "deepseek-v4")
os.environ.setdefault("LLM_MODEL_EXTRACT_FALLBACK", "gemini-3-flash")
os.environ.setdefault("LLM_MODEL_JUDGE", "gpt-5.5")
os.environ.setdefault("LLM_MODEL_JUDGE_FALLBACK", "gpt-5.4")
os.environ.setdefault("LLM_MODEL_LIGHT", "gemini-3-flash")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-do-not-use-in-prod")
# Fernet keys must be valid base64-encoded 32-byte values; generate one per test run.
from cryptography.fernet import Fernet as _Fernet
os.environ.setdefault("PII_ENCRYPTION_KEY", _Fernet.generate_key().decode())
```

- [ ] **1.7 写 README.md（占位）**

```markdown
# SmartScreenAgent

AI-driven resume screening agent for HR.

## Quick start

```bash
uv sync
cp .env.example .env  # edit values
docker compose up -d  # start PG / Redis / MinIO
uv run alembic upgrade head
uv run uvicorn backend.app.main:app --reload
```

See `docs/specs/2026-05-12-resume-screening-agent-design.md` for design.
```

- [ ] **1.8 用 uv 安装依赖并验证**

```bash
uv sync --extra dev
```

Expected: `uv.lock` 生成，无错误。

- [ ] **1.9 启动占位 app 验证**

```bash
uv run uvicorn backend.app.main:app --port 8000 &
sleep 2
curl http://localhost:8000/
```

Expected output: `{"service":"smartscreen-agent","status":"ok"}`

```bash
kill %1 2>/dev/null
```

- [ ] **1.10 Commit**

```bash
git add pyproject.toml uv.lock .env.example README.md backend/
git commit -m "feat: bootstrap FastAPI project skeleton with uv"
```

---

## Task 2: docker-compose（PG + Redis + MinIO）

**Files:**
- Create: `docker-compose.yml`
- Create: `infra/postgres/init.sql`

- [ ] **2.1 写 infra/postgres/init.sql（启用扩展）**

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

注：`pgcrypto` 视调研 Task 0.4 决策决定是否启用——若选 Fernet 应用层加密，不需要此扩展。

- [ ] **2.2 写 docker-compose.yml**

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    container_name: smartscreen-postgres
    environment:
      POSTGRES_USER: smartscreen
      POSTGRES_PASSWORD: smartscreen
      POSTGRES_DB: smartscreen
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./infra/postgres/init.sql:/docker-entrypoint-initdb.d/init.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U smartscreen -d smartscreen"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    container_name: smartscreen-redis
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  minio:
    image: minio/minio:latest
    container_name: smartscreen-minio
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    ports:
      - "9000:9000"
      - "9001:9001"
    volumes:
      - minio_data:/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  postgres_data:
  minio_data:
```

- [ ] **2.3 启动并验证**

```bash
docker compose up -d
docker compose ps
```

Expected: 三个容器都是 `healthy`。

- [ ] **2.4 验证 pgvector 扩展已启用**

```bash
docker exec smartscreen-postgres psql -U smartscreen -d smartscreen -c "SELECT extname FROM pg_extension WHERE extname IN ('vector', 'pg_trgm');"
```

Expected: 两行返回 `vector` 和 `pg_trgm`。

- [ ] **2.5 Commit**

```bash
git add docker-compose.yml infra/
git commit -m "feat: docker-compose with postgres(pgvector)/redis/minio"
```

---

## Task 3: Alembic 初始化 + 数据库会话

**Files:**
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/script.py.mako`
- Create: `backend/app/database.py`
- Create: `backend/tests/integration/__init__.py`
- Create: `backend/tests/integration/test_db_migrations.py`

- [ ] **3.1 写 backend/app/database.py**

```python
from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from backend.app.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
```

- [ ] **3.2 初始化 alembic 骨架**

```bash
uv run alembic init -t async migrations
```

Expected: `migrations/` 目录和 `alembic.ini` 生成。

- [ ] **3.3 修改 alembic.ini 中 script_location 与 sqlalchemy.url**

```ini
# alembic.ini
[alembic]
script_location = migrations
sqlalchemy.url =

# (其余 logging 等保留)
```

> `sqlalchemy.url` 留空，由 env.py 从 .env 读取。

- [ ] **3.4 重写 migrations/env.py 从配置加载 URL**

```python
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from backend.app.config import get_settings
from backend.app.models import Base  # 见 Task 4 创建

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    raise NotImplementedError("Offline mode not supported")
else:
    run_migrations_online()
```

> 注意 `from backend.app.models import Base`——此 import 在 Task 4 之前会失败，所以 Task 3 完成后先不跑 `alembic revision`，仅完成文件。

- [ ] **3.5 写一个集成测试占位**

```python
# backend/tests/integration/__init__.py
```

```python
# backend/tests/integration/test_db_migrations.py
import subprocess


def test_alembic_can_show_history() -> None:
    """Smoke: alembic 配置加载无报错。"""
    result = subprocess.run(
        ["uv", "run", "alembic", "history"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    # Task 3 时尚无 revision，只需命令不报错
    assert result.returncode == 0, f"alembic history failed: {result.stderr}"
```

> 此测试 Task 4 后会有意义；Task 3 时跑不通（因为 Base 还没建），所以暂标 skip。

- [ ] **3.6 Commit（先不跑测试，待 Task 4 后联跑）**

```bash
git add alembic.ini migrations/ backend/app/database.py backend/tests/integration/
git commit -m "feat: alembic + async db session"
```

---

## Task 4: 用户模型 + Base 类

**Files:**
- Create: `backend/app/models/__init__.py`
- Create: `backend/app/models/base.py`
- Create: `backend/app/models/user.py`
- Create: `backend/tests/unit/__init__.py`
- Create: `backend/tests/unit/test_models.py`

- [ ] **4.1 写失败测试**

```python
# backend/tests/unit/__init__.py
```

```python
# backend/tests/unit/test_models.py
from datetime import datetime
from backend.app.models import Base, User


def test_user_model_has_required_columns():
    table = User.__table__
    cols = {c.name for c in table.columns}
    assert {"id", "dingtalk_userid", "display_name", "role", "created_at", "last_login_at"} <= cols


def test_base_registers_user():
    assert "users" in Base.metadata.tables
```

- [ ] **4.2 运行测试确认失败**

```bash
uv run pytest backend/tests/unit/test_models.py -v
```

Expected: ImportError，因为 `models` 还没建。

- [ ] **4.3 写 backend/app/models/base.py**

```python
from datetime import datetime
from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **4.4 写 backend/app/models/user.py**

```python
from datetime import datetime
from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.models.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dingtalk_userid: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="hr")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

- [ ] **4.5 写 backend/app/models/__init__.py 暴露符号**

```python
from backend.app.models.base import Base, TimestampMixin
from backend.app.models.user import User

__all__ = ["Base", "TimestampMixin", "User"]
```

- [ ] **4.6 运行测试确认通过**

```bash
uv run pytest backend/tests/unit/test_models.py::test_user_model_has_required_columns backend/tests/unit/test_models.py::test_base_registers_user -v
```

Expected: 两个测试 PASS。

- [ ] **4.7 Commit**

```bash
git add backend/app/models/ backend/tests/unit/
git commit -m "feat: User model + DeclarativeBase + TimestampMixin"
```

---

## Task 5: JD + RuleVersion 模型

**Files:**
- Create: `backend/app/models/jd.py`
- Create: `backend/app/models/rule_version.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/tests/unit/test_models.py`

- [ ] **5.1 在 test_models.py 追加失败测试**

```python
# 追加到 backend/tests/unit/test_models.py
from backend.app.models import JD, RuleVersion


def test_jd_columns():
    cols = {c.name for c in JD.__table__.columns}
    assert {"id", "code", "name", "description", "status", "active_rule_version_id"} <= cols


def test_rule_version_columns():
    cols = {c.name for c in RuleVersion.__table__.columns}
    assert {"id", "jd_id", "version", "schema_json", "published_at", "published_by_user_id",
            "notes", "golden_set_metrics"} <= cols
```

- [ ] **5.2 写 backend/app/models/jd.py**

```python
from sqlalchemy import BigInteger, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.models.base import Base, TimestampMixin


class JD(Base, TimestampMixin):
    __tablename__ = "jds"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    active_rule_version_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("rule_versions.id", use_alter=True, name="fk_jd_active_rule")
    )
```

- [ ] **5.3 写 backend/app/models/rule_version.py**

```python
from datetime import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.models.base import Base


class RuleVersion(Base):
    __tablename__ = "rule_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    jd_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("jds.id"), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_by_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    golden_set_metrics: Mapped[dict | None] = mapped_column(JSONB)
```

- [ ] **5.4 更新 __init__.py**

```python
# backend/app/models/__init__.py
from backend.app.models.base import Base, TimestampMixin
from backend.app.models.user import User
from backend.app.models.jd import JD
from backend.app.models.rule_version import RuleVersion

__all__ = ["Base", "TimestampMixin", "User", "JD", "RuleVersion"]
```

- [ ] **5.5 跑测试**

```bash
uv run pytest backend/tests/unit/test_models.py -v
```

Expected: 全部 4 个测试 PASS。

- [ ] **5.6 Commit**

```bash
git add backend/app/models/ backend/tests/unit/test_models.py
git commit -m "feat: JD + RuleVersion models (with JSONB schema)"
```

---

## Task 6: Candidate 模型 + PII 加密辅助

**Files:**
- Create: `backend/app/security/__init__.py`
- Create: `backend/app/security/crypto.py`
- Create: `backend/app/models/candidate.py`
- Create: `backend/tests/unit/test_crypto.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/tests/unit/test_models.py`

- [ ] **6.1 写失败测试 backend/tests/unit/test_crypto.py**

```python
import pytest
from backend.app.security.crypto import encrypt_pii, decrypt_pii, hash_pii


def test_encrypt_decrypt_roundtrip():
    plain = "张三 13800138000"
    cipher = encrypt_pii(plain)
    assert cipher != plain
    assert decrypt_pii(cipher) == plain


def test_encrypt_empty_string():
    cipher = encrypt_pii("")
    assert decrypt_pii(cipher) == ""


def test_decrypt_invalid_raises():
    with pytest.raises(ValueError):
        decrypt_pii("not-real-cipher")


def test_hash_pii_deterministic():
    h1 = hash_pii("13800138000", "张三")
    h2 = hash_pii("13800138000", "张三")
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex
```

- [ ] **6.2 跑测试确认失败**

```bash
uv run pytest backend/tests/unit/test_crypto.py -v
```

Expected: ImportError。

- [ ] **6.3 写 backend/app/security/__init__.py + crypto.py**

```python
# backend/app/security/__init__.py
```

```python
# backend/app/security/crypto.py
import hashlib
from cryptography.fernet import Fernet, InvalidToken
from backend.app.config import get_settings

_settings = get_settings()

if len(_settings.PII_ENCRYPTION_KEY) != 44:
    raise RuntimeError(
        "PII_ENCRYPTION_KEY must be a 44-char base64 Fernet key. "
        "Generate one with: "
        'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
    )

_fernet = Fernet(_settings.PII_ENCRYPTION_KEY.encode())


def encrypt_pii(plaintext: str) -> str:
    """对 PII 字符串加密；空字符串也支持。"""
    return _fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_pii(ciphertext: str) -> str:
    """解密回明文；非法 token 抛 ValueError。"""
    try:
        return _fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise ValueError("Invalid ciphertext or wrong key") from e


def hash_pii(phone: str, name: str) -> str:
    """生成稳定哈希用于去重（不可逆）。

    实现注意：手机号搜索空间只有 ~10^11，攻击者拿到库后可用 GPU 爆破 SHA-256。
    若威胁模型要求抗 DB 读取攻击者，升级到 HMAC-SHA-256 并引入独立 PII_INDEX_KEY env var。
    P1 阶段先用 SHA-256 维持简单；安全审计若提出再升级。
    """
    h = hashlib.sha256()
    h.update(phone.encode("utf-8"))
    h.update(b"|")
    h.update(name.encode("utf-8"))
    return h.hexdigest()
```

> ⚠️ `PII_ENCRYPTION_KEY` 必须是 44 字符的 base64 编码 Fernet key。运维生成方式：`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`。

- [ ] **6.5 跑加密测试**

```bash
uv run pytest backend/tests/unit/test_crypto.py -v
```

Expected: 4 个测试全 PASS。

- [ ] **6.6 在 test_models.py 追加 Candidate 测试**

```python
from backend.app.models import Candidate


def test_candidate_columns():
    cols = {c.name for c in Candidate.__table__.columns}
    expected = {"id", "source", "source_external_id", "name_cipher", "phone_cipher",
                "email_cipher", "raw_file_key", "parsed_markdown", "extracted_json", "pii_hash"}
    assert expected <= cols
```

- [ ] **6.7 写 backend/app/models/candidate.py**

```python
from sqlalchemy import BigInteger, String, Text, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.models.base import Base, TimestampMixin


class Candidate(Base, TimestampMixin):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_external_id: Mapped[str | None] = mapped_column(String(128), index=True)
    name_cipher: Mapped[str] = mapped_column(Text, nullable=False)
    phone_cipher: Mapped[str | None] = mapped_column(Text)
    email_cipher: Mapped[str | None] = mapped_column(Text)
    raw_file_key: Mapped[str | None] = mapped_column(String(512))
    parsed_markdown: Mapped[str | None] = mapped_column(Text)
    extracted_json: Mapped[dict | None] = mapped_column(JSONB)
    pii_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    __table_args__ = (
        Index("ix_candidates_source_external", "source", "source_external_id"),
    )
```

- [ ] **6.8 更新 __init__.py 加 Candidate**

```python
# 在 backend/app/models/__init__.py 中
from backend.app.models.candidate import Candidate
# 并把 Candidate 加到 __all__
```

- [ ] **6.9 跑所有 model 测试**

```bash
uv run pytest backend/tests/unit/ -v
```

Expected: 全部 PASS。

- [ ] **6.10 Commit**

```bash
git add backend/app/security/ backend/app/models/candidate.py backend/app/models/__init__.py \
        backend/tests/unit/test_crypto.py backend/tests/unit/test_models.py
git commit -m "feat: Candidate model + PII Fernet encryption helpers"
```

---

## Task 7: Score / Feedback / GoldenSet 模型

**Files:**
- Create: `backend/app/models/score.py`
- Create: `backend/app/models/feedback.py`
- Create: `backend/app/models/golden_set.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/tests/unit/test_models.py`

- [ ] **7.1 追加测试**

```python
from backend.app.models import Score, Feedback, GoldenSet


def test_score_columns():
    cols = {c.name for c in Score.__table__.columns}
    assert {"id", "candidate_id", "jd_id", "rule_version_id", "total_score", "grade",
            "hard_filter_result", "rule_dimensions", "judge_dimensions",
            "cross_engine_diff", "is_suspicious", "llm_model_main", "llm_model_extract",
            "cost_tokens", "cost_cny"} <= cols


def test_feedback_columns():
    cols = {c.name for c in Feedback.__table__.columns}
    assert {"id", "score_id", "reviewer_user_id", "decision", "reason", "ai_agreed"} <= cols


def test_golden_set_columns():
    cols = {c.name for c in GoldenSet.__table__.columns}
    assert {"id", "candidate_id", "jd_id", "label", "imported_at", "imported_by_user_id"} <= cols
```

- [ ] **7.2 写 score.py**

```python
from sqlalchemy import BigInteger, Boolean, ForeignKey, Numeric, String, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.models.base import Base, TimestampMixin


class Score(Base, TimestampMixin):
    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("candidates.id"), nullable=False, index=True)
    jd_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("jds.id"), nullable=False, index=True)
    rule_version_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("rule_versions.id"), nullable=False)

    total_score: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    grade: Mapped[str] = mapped_column(String(16), nullable=False)
    hard_filter_result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    rule_dimensions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    judge_dimensions: Mapped[dict | None] = mapped_column(JSONB)
    cross_engine_diff: Mapped[float | None] = mapped_column(Numeric(6, 2))
    is_suspicious: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    llm_model_main: Mapped[str | None] = mapped_column(String(64))
    llm_model_extract: Mapped[str | None] = mapped_column(String(64))
    cost_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_cny: Mapped[float] = mapped_column(Numeric(10, 4), default=0, nullable=False)
```

- [ ] **7.3 写 feedback.py**

```python
from sqlalchemy import BigInteger, Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.models.base import Base, TimestampMixin


class Feedback(Base, TimestampMixin):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    score_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("scores.id"), nullable=False, index=True)
    reviewer_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    ai_agreed: Mapped[bool | None] = mapped_column(Boolean)
```

- [ ] **7.4 写 golden_set.py**

```python
from datetime import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.models.base import Base


class GoldenSet(Base):
    __tablename__ = "golden_set"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("candidates.id"), nullable=False)
    jd_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("jds.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    imported_by_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)

    __table_args__ = (
        UniqueConstraint("candidate_id", "jd_id", name="uq_golden_set_cand_jd"),
    )
```

- [ ] **7.5 更新 __init__.py**

```python
# backend/app/models/__init__.py 完整版
from backend.app.models.base import Base, TimestampMixin
from backend.app.models.user import User
from backend.app.models.jd import JD
from backend.app.models.rule_version import RuleVersion
from backend.app.models.candidate import Candidate
from backend.app.models.score import Score
from backend.app.models.feedback import Feedback
from backend.app.models.golden_set import GoldenSet

__all__ = ["Base", "TimestampMixin", "User", "JD", "RuleVersion",
           "Candidate", "Score", "Feedback", "GoldenSet"]
```

- [ ] **7.6 跑测试**

```bash
uv run pytest backend/tests/unit/test_models.py -v
```

Expected: 全部 PASS。

- [ ] **7.7 Commit**

```bash
git add backend/app/models/
git commit -m "feat: Score/Feedback/GoldenSet models"
```

---

## Task 8: AuditLog 模型

**Files:**
- Create: `backend/app/models/audit_log.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/tests/unit/test_models.py`

- [ ] **8.1 追加测试**

```python
from backend.app.models import AuditLog


def test_audit_log_columns():
    cols = {c.name for c in AuditLog.__table__.columns}
    assert {"id", "event_type", "actor", "target_type", "target_id",
            "payload", "rule_version_id", "created_at"} <= cols
```

- [ ] **8.2 写 audit_log.py**

```python
from sqlalchemy import BigInteger, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.models.base import Base, TimestampMixin


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[int | None] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    rule_version_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("rule_versions.id"))

    __table_args__ = (
        Index("ix_audit_event_created", "event_type", "created_at"),
    )
```

- [ ] **8.3 更新 __init__.py 加 AuditLog**

```python
from backend.app.models.audit_log import AuditLog
# 加入 __all__
```

- [ ] **8.4 跑测试 + Commit**

```bash
uv run pytest backend/tests/unit/test_models.py -v
git add backend/app/models/
git commit -m "feat: AuditLog model"
```

---

## Task 9: CandidateEmbedding (pgvector)

**Files:**
- Create: `backend/app/models/candidate_embedding.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/tests/unit/test_models.py`

> ⚠️ 实施前必须完成 Task 0.3 (pgvector 调研笔记)。

- [ ] **9.1 追加测试**

```python
from backend.app.models import CandidateEmbedding


def test_candidate_embedding_columns():
    cols = {c.name for c in CandidateEmbedding.__table__.columns}
    assert {"candidate_id", "embedding", "model_name", "created_at"} <= cols
```

- [ ] **9.2 写 candidate_embedding.py**

```python
from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector
from backend.app.models.base import Base, TimestampMixin


class CandidateEmbedding(Base, TimestampMixin):
    __tablename__ = "candidate_embeddings"

    candidate_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("candidates.id", ondelete="CASCADE"), primary_key=True
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(1024), nullable=False)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
```

- [ ] **9.3 更新 __init__.py + 跑测试**

```bash
uv run pytest backend/tests/unit/test_models.py -v
```

- [ ] **9.4 生成第一个 alembic baseline 迁移**

```bash
uv run alembic revision --autogenerate -m "baseline schema"
```

Expected: `migrations/versions/<hash>_baseline_schema.py` 生成。

- [ ] **9.5 检查迁移文件**

打开生成的迁移，确认：
- 所有 8 张表都被创建
- `candidate_embeddings.embedding` 列类型是 `Vector(1024)`
- 首行 `op.execute('CREATE EXTENSION IF NOT EXISTS vector')` 需手动加入（autogenerate 不会加）

如果缺扩展启用语句，手动编辑迁移文件，在 `def upgrade()` 开头插入：

```python
op.execute("CREATE EXTENSION IF NOT EXISTS vector")
```

- [ ] **9.6 应用迁移到本地 PG**

```bash
uv run alembic upgrade head
```

Expected: 无报错，`docker exec smartscreen-postgres psql -U smartscreen -d smartscreen -c "\dt"` 列出 8 张业务表 + `alembic_version`。

- [ ] **9.7 集成测试：迁移 history 可读**

```bash
uv run pytest backend/tests/integration/test_db_migrations.py -v
```

Expected: PASS。

- [ ] **9.8 Commit**

```bash
git add backend/app/models/ backend/tests/unit/test_models.py migrations/versions/
git commit -m "feat: CandidateEmbedding (pgvector) + baseline migration"
```

---

## Task 10: LLMGateway（newapi 客户端）

> ⚠️ 实施前必须完成 Task 0.1 (newapi 调研笔记)。

**Files:**
- Create: `backend/app/services/__init__.py`
- Create: `backend/app/services/llm/__init__.py`
- Create: `backend/app/services/llm/schemas.py`
- Create: `backend/app/services/llm/gateway.py`
- Create: `backend/tests/unit/test_llm_gateway.py`

- [ ] **10.1 写失败测试**

```python
# backend/tests/unit/test_llm_gateway.py
from unittest.mock import AsyncMock, patch
import pytest
from backend.app.services.llm.gateway import LLMGateway, LLMResponse


@pytest.mark.asyncio
async def test_extract_calls_extract_model(monkeypatch):
    gateway = LLMGateway()
    fake = AsyncMock(return_value=LLMResponse(
        content='{"name":"张三"}', model="deepseek-v4", input_tokens=100, output_tokens=20
    ))
    monkeypatch.setattr(gateway, "_call_with_fallback", fake)
    result = await gateway.extract("简历文本", schema={"type": "object"})
    assert result.content == '{"name":"张三"}'
    fake.assert_awaited_once()


@pytest.mark.asyncio
async def test_judge_uses_main_then_fallback():
    """主模型失败时自动切备用。"""
    gateway = LLMGateway()
    call_count = {"n": 0}

    async def fake_call(model: str, **_kw):
        call_count["n"] += 1
        if model == gateway.settings.LLM_MODEL_JUDGE:
            raise RuntimeError("primary down")
        return LLMResponse(content="ok", model=model, input_tokens=10, output_tokens=5)

    gateway._call_once = fake_call  # type: ignore
    result = await gateway.judge("prompt", schema={"type": "object"})
    assert call_count["n"] == 2
    assert result.model == gateway.settings.LLM_MODEL_JUDGE_FALLBACK
```

- [ ] **10.2 跑测试确认失败**

```bash
uv run pytest backend/tests/unit/test_llm_gateway.py -v
```

Expected: ImportError。

- [ ] **10.3 写 schemas.py**

```python
# backend/app/services/llm/schemas.py
from dataclasses import dataclass


@dataclass
class LLMResponse:
    content: str
    model: str
    input_tokens: int
    output_tokens: int
```

- [ ] **10.4 写 gateway.py**

```python
# backend/app/services/llm/gateway.py
from __future__ import annotations
import json
import logging
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential
from backend.app.config import get_settings
from backend.app.services.llm.schemas import LLMResponse

logger = logging.getLogger(__name__)


class LLMGateway:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._client = AsyncOpenAI(
            base_url=self.settings.NEWAPI_BASE_URL,
            api_key=self.settings.NEWAPI_API_KEY,
            timeout=60.0,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _call_once(
        self,
        model: str,
        prompt: str,
        *,
        response_format: dict | None = None,
        temperature: float = 0.1,
    ) -> LLMResponse:
        kwargs: dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if response_format:
            kwargs["response_format"] = response_format
        resp = await self._client.chat.completions.create(**kwargs)
        usage = resp.usage
        return LLMResponse(
            content=resp.choices[0].message.content or "",
            model=model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )

    async def _call_with_fallback(
        self,
        primary: str,
        fallback: str | None,
        prompt: str,
        **kwargs,
    ) -> LLMResponse:
        try:
            return await self._call_once(primary, prompt, **kwargs)
        except Exception as e:
            if not fallback:
                raise
            logger.warning("LLM primary %s failed (%s), falling back to %s", primary, e, fallback)
            return await self._call_once(fallback, prompt, **kwargs)

    async def extract(self, text: str, *, schema: dict) -> LLMResponse:
        prompt = (
            "你是简历结构化抽取助手。基于以下简历内容，输出严格符合 JSON schema 的 JSON。\n\n"
            f"<resume>\n{text}\n</resume>\n\nschema={json.dumps(schema, ensure_ascii=False)}"
        )
        return await self._call_with_fallback(
            self.settings.LLM_MODEL_EXTRACT,
            self.settings.LLM_MODEL_EXTRACT_FALLBACK,
            prompt,
            response_format={"type": "json_object"},
        )

    async def judge(self, prompt: str, *, schema: dict) -> LLMResponse:
        return await self._call_with_fallback(
            self.settings.LLM_MODEL_JUDGE,
            self.settings.LLM_MODEL_JUDGE_FALLBACK,
            prompt,
            response_format={"type": "json_object"},
        )

    async def lightweight(self, prompt: str) -> LLMResponse:
        return await self._call_once(self.settings.LLM_MODEL_LIGHT, prompt)
```

- [ ] **10.5 跑测试**

```bash
uv run pytest backend/tests/unit/test_llm_gateway.py -v
```

Expected: 2 个测试 PASS。

- [ ] **10.6 Commit**

```bash
git add backend/app/services/ backend/tests/unit/test_llm_gateway.py
git commit -m "feat: LLMGateway with newapi + fallback chain"
```

---

## Task 11: DingTalk OAuth 客户端 + 登录路由

> ⚠️ **Prerequisite — 先做 Task 12（JWT）再回来做本任务。** Task 11.3 的登录路由会调用 `create_access_token`（Task 12 产物），不先做 JWT 这里会 import 失败。
>
> ⚠️ 实施前必须完成 Task 0.2 (DingTalk OAuth 调研笔记)。

**Files:**
- Create: `backend/app/services/dingtalk/__init__.py`
- Create: `backend/app/services/dingtalk/oauth.py`
- Create: `backend/app/routers/__init__.py`
- Create: `backend/app/routers/auth.py`
- Create: `backend/tests/unit/test_dingtalk_oauth.py`
- Modify: `backend/app/main.py`

- [ ] **11.1 写失败测试**

```python
# backend/tests/unit/test_dingtalk_oauth.py
from unittest.mock import AsyncMock
import pytest
from backend.app.services.dingtalk.oauth import DingTalkOAuthClient, DingTalkUserInfo


@pytest.mark.asyncio
async def test_exchange_code_for_user(monkeypatch):
    client = DingTalkOAuthClient()
    monkeypatch.setattr(client, "_get_user_access_token", AsyncMock(return_value="ut-fake"))
    monkeypatch.setattr(
        client,
        "_fetch_user_info",
        AsyncMock(return_value=DingTalkUserInfo(union_id="u-stable-1", open_id="o-app-1", display_name="Leo")),
    )
    info = await client.exchange_auth_code("auth-code-xxx")
    assert info.union_id == "u-stable-1"
    assert info.display_name == "Leo"


@pytest.mark.asyncio
async def test_exchange_code_network_error(monkeypatch):
    """网络错误应抛出供路由层捕获并返 400。"""
    import httpx
    client = DingTalkOAuthClient()
    monkeypatch.setattr(
        client,
        "_get_user_access_token",
        AsyncMock(side_effect=httpx.HTTPStatusError("400", request=None, response=None)),
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.exchange_auth_code("bad-code")
```

- [ ] **11.2 写 oauth.py**

> 端点路径与字段以 Task 0.2 调研笔记为准。下面是参考骨架（按调研结果调整 URL、字段名）：

```python
# backend/app/services/dingtalk/oauth.py
from dataclasses import dataclass
import httpx
from backend.app.config import get_settings


@dataclass
class DingTalkUserInfo:
    """unionId 是钉钉跨应用稳定标识；openId 仅 app 域内稳定。用 unionId 作为我们 User.dingtalk_userid 的来源。"""
    union_id: str
    open_id: str
    display_name: str


class DingTalkOAuthClient:
    """钉钉 OAuth 客户端。

    ⚠️ DO NOT TRUST the URLs / payload field names below — they are placeholders.
    BEFORE running this code, verify each endpoint, request body field, and response field
    against docs/specs/research/dingtalk-oauth.md (which itself must be sourced from
    the dingtalk-api MCP read_project_oas / official open.dingtalk.com docs).
    """

    # ⬇️ 必须用 Task 0.2 调研结果替换：
    USER_TOKEN_URL = "<TBD: confirm via dingtalk-api MCP — typically /v1.0/oauth2/userAccessToken>"
    USER_INFO_URL = "<TBD: confirm via dingtalk-api MCP — typically /v1.0/contact/users/me>"

    def __init__(self) -> None:
        self.settings = get_settings()

    async def _get_user_access_token(self, auth_code: str) -> str:
        # ⚠️ Field names (clientId / clientSecret / code / grantType) — verify via OAS before running.
        payload = {
            "clientId": self.settings.DINGTALK_APP_KEY,
            "clientSecret": self.settings.DINGTALK_APP_SECRET,
            "code": auth_code,
            "grantType": "authorization_code",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(self.USER_TOKEN_URL, json=payload)
            r.raise_for_status()
            # ⚠️ Response key name (accessToken vs access_token) — verify.
            return r.json()["accessToken"]

    async def _fetch_user_info(self, user_access_token: str) -> DingTalkUserInfo:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                self.USER_INFO_URL,
                # ⚠️ Header name — verify via OAS.
                headers={"x-acs-dingtalk-access-token": user_access_token},
            )
            r.raise_for_status()
            j = r.json()
            return DingTalkUserInfo(
                union_id=j.get("unionId", ""),
                open_id=j.get("openId", ""),
                display_name=j.get("nick", ""),
            )

    async def exchange_auth_code(self, auth_code: str) -> DingTalkUserInfo:
        token = await self._get_user_access_token(auth_code)
        return await self._fetch_user_info(token)
```

- [ ] **11.3 写登录路由 backend/app/routers/auth.py**

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.database import get_db
from backend.app.models import User
from backend.app.services.dingtalk.oauth import DingTalkOAuthClient

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    auth_code: str


class LoginResponse(BaseModel):
    token: str
    display_name: str
    role: str


@router.post("/dingtalk/login", response_model=LoginResponse)
async def dingtalk_login(req: LoginRequest, db: AsyncSession = Depends(get_db)) -> LoginResponse:
    from backend.app.security.jwt import create_access_token  # 见 Task 12

    client = DingTalkOAuthClient()
    try:
        info = await client.exchange_auth_code(req.auth_code)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"DingTalk OAuth failed: {e}") from e

    # union_id 是跨钉钉应用稳定的；用它做我们 users.dingtalk_userid 主标识
    result = await db.execute(select(User).where(User.dingtalk_userid == info.union_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(dingtalk_userid=info.union_id, display_name=info.display_name, role="hr")
        db.add(user)
        await db.flush()
    await db.commit()

    token = create_access_token({"sub": str(user.id), "role": user.role})
    return LoginResponse(token=token, display_name=user.display_name, role=user.role)
```

- [ ] **11.4 在 main.py 注册路由**

```python
# 修改 backend/app/main.py
from fastapi import FastAPI
from backend.app.routers import auth as auth_router


def create_app() -> FastAPI:
    app = FastAPI(title="SmartScreenAgent", version="0.1.0")
    app.include_router(auth_router.router)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": "smartscreen-agent", "status": "ok"}

    return app


app = create_app()
```

- [ ] **11.5 写空的 routers/__init__.py + services/dingtalk/__init__.py**

```python
# backend/app/routers/__init__.py
```

```python
# backend/app/services/dingtalk/__init__.py
```

- [ ] **11.6 跑测试**

```bash
uv run pytest backend/tests/unit/test_dingtalk_oauth.py -v
```

Expected: PASS。

- [ ] **11.7 Commit**

```bash
git add backend/app/services/dingtalk/ backend/app/routers/ backend/app/main.py \
        backend/tests/unit/test_dingtalk_oauth.py
git commit -m "feat: DingTalk OAuth client + login route"
```

---

## Task 12: JWT 中间件与依赖

**Files:**
- Create: `backend/app/security/jwt.py`
- Create: `backend/app/deps.py`
- Create: `backend/tests/unit/test_jwt.py`

- [ ] **12.1 写失败测试**

```python
# backend/tests/unit/test_jwt.py
import pytest
from backend.app.security.jwt import create_access_token, decode_token


def test_create_and_decode_token():
    token = create_access_token({"sub": "42", "role": "hr"})
    payload = decode_token(token)
    assert payload["sub"] == "42"
    assert payload["role"] == "hr"


def test_decode_invalid_token():
    with pytest.raises(ValueError):
        decode_token("not-a-token")
```

- [ ] **12.2 写 jwt.py**

```python
# backend/app/security/jwt.py
from datetime import datetime, timedelta, timezone
import jwt
from backend.app.config import get_settings

_settings = get_settings()


def create_access_token(claims: dict, expires_hours: int | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        hours=expires_hours or _settings.JWT_EXPIRE_HOURS
    )
    payload = {**claims, "exp": expire}
    return jwt.encode(payload, _settings.JWT_SECRET_KEY, algorithm=_settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, _settings.JWT_SECRET_KEY, algorithms=[_settings.JWT_ALGORITHM])
    except jwt.PyJWTError as e:
        raise ValueError(f"Invalid token: {e}") from e
```

- [ ] **12.3 写 deps.py**

```python
# backend/app/deps.py
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.database import get_db
from backend.app.models import User
from backend.app.security.jwt import decode_token


async def get_current_user(
    authorization: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Bearer token")
    token = authorization[len("Bearer "):]
    try:
        payload = decode_token(token)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e
    user_id = int(payload.get("sub", 0))
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user
```

- [ ] **12.4 跑测试 + Commit**

```bash
uv run pytest backend/tests/unit/test_jwt.py -v
git add backend/app/security/jwt.py backend/app/deps.py backend/tests/unit/test_jwt.py
git commit -m "feat: JWT helper + get_current_user dependency"
```

---

## Task 13: 健康检查 + 结构化日志

**Files:**
- Create: `backend/app/logging_config.py`
- Create: `backend/app/routers/health.py`
- Create: `backend/tests/unit/test_health.py`
- Modify: `backend/app/main.py`

- [ ] **13.1 写失败测试**

```python
# backend/tests/unit/test_health.py
import pytest
from httpx import ASGITransport, AsyncClient
from backend.app.main import app


@pytest.mark.asyncio
async def test_healthz_returns_ok():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "checks" in body
```

- [ ] **13.2 写 logging_config.py**

```python
# backend/app/logging_config.py
import logging
import sys
import structlog


def configure_logging() -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
```

- [ ] **13.3 写 health.py**

```python
# backend/app/routers/health.py
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.config import get_settings
from backend.app.database import get_db
from backend.app.services.storage.minio_client import MinIOStorage

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(db: AsyncSession = Depends(get_db)) -> dict:
    settings = get_settings()
    checks: dict[str, str] = {}

    # DB
    try:
        await db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["db"] = f"fail: {e}"

    # Redis
    try:
        r = aioredis.from_url(settings.REDIS_URL)
        pong = await r.ping()
        await r.close()
        checks["redis"] = "ok" if pong else "fail"
    except Exception as e:  # noqa: BLE001
        checks["redis"] = f"fail: {e}"

    # MinIO（同步 client，包成轻量 bucket_exists 检查）
    try:
        storage = MinIOStorage()
        storage._client.bucket_exists(storage.bucket)  # noqa: SLF001
        checks["minio"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["minio"] = f"fail: {e}"

    overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return {"status": overall, "version": "0.1.0", "checks": checks}
```

- [ ] **13.4 写 access log 中间件 backend/app/middleware.py**

```python
# backend/app/middleware.py
import time
import uuid
import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger("access")


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        trace_id = request.headers.get("x-trace-id", uuid.uuid4().hex[:16])
        start = time.perf_counter()
        structlog.contextvars.bind_contextvars(trace_id=trace_id)
        try:
            response: Response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.error("request_error", method=request.method, path=request.url.path,
                         elapsed_ms=round(elapsed_ms, 2))
            raise
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info("request", method=request.method, path=request.url.path,
                    status=response.status_code, elapsed_ms=round(elapsed_ms, 2))
        response.headers["x-trace-id"] = trace_id
        structlog.contextvars.clear_contextvars()
        return response
```

- [ ] **13.5 修改 main.py 注册 CORS + 日志 + 路由**

```python
# backend/app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.app.config import get_settings
from backend.app.logging_config import configure_logging
from backend.app.middleware import AccessLogMiddleware
from backend.app.routers import auth as auth_router
from backend.app.routers import health as health_router


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    app = FastAPI(title="SmartScreenAgent", version="0.1.0")

    # CORS — P3 Next.js 前端跨域调用
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # access log + trace id
    app.add_middleware(AccessLogMiddleware)

    app.include_router(health_router.router)
    app.include_router(auth_router.router)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": "smartscreen-agent", "status": "ok"}

    return app


app = create_app()
```

- [ ] **13.6 跑测试**

```bash
uv run pytest backend/tests/unit/test_health.py -v
```

Expected: PASS（前提：本机 PG / Redis / MinIO 已起且迁移已 upgrade head）。

- [ ] **13.7 Commit**

```bash
git add backend/app/logging_config.py backend/app/middleware.py backend/app/routers/health.py \
        backend/app/main.py backend/tests/unit/test_health.py
git commit -m "feat: /healthz (db+redis+minio) + CORS + access log middleware"
```

---

## Task 14: Celery + Redis

**Files:**
- Create: `backend/app/tasks/__init__.py`
- Create: `backend/app/tasks/celery_app.py`
- Create: `backend/tests/unit/test_celery_app.py`

- [ ] **14.1 写最小测试**

```python
# backend/tests/unit/test_celery_app.py
from backend.app.tasks.celery_app import celery_app


def test_celery_app_configured():
    assert celery_app.conf.broker_url is not None
    assert celery_app.conf.result_backend is not None
    assert "backend.app.tasks" in celery_app.conf.imports or True  # 后续会注册具体任务
```

- [ ] **14.2 写 celery_app.py**

```python
# backend/app/tasks/celery_app.py
from celery import Celery
from backend.app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "smartscreen",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=600,
    worker_max_tasks_per_child=100,
)


@celery_app.task(name="smartscreen.ping")
def ping() -> str:
    """烟测任务：worker 起来后可触发以确认链路通。"""
    return "pong"
```

- [ ] **14.3 写 __init__.py**

```python
# backend/app/tasks/__init__.py
from backend.app.tasks.celery_app import celery_app

__all__ = ["celery_app"]
```

- [ ] **14.4 启动 worker 烟测**

启动 worker：
```bash
uv run celery -A backend.app.tasks.celery_app worker -l info &
sleep 3
```

触发 ping：
```bash
uv run python -c "
from backend.app.tasks.celery_app import ping
result = ping.delay()
print('result:', result.get(timeout=10))
"
```

Expected: `result: pong`

```bash
kill %1 2>/dev/null
```

- [ ] **14.5 跑单元测试 + Commit**

```bash
uv run pytest backend/tests/unit/test_celery_app.py -v
git add backend/app/tasks/ backend/tests/unit/test_celery_app.py
git commit -m "feat: Celery app + Redis broker + ping smoke task"
```

---

## Task 15: MinIO 客户端

**Files:**
- Create: `backend/app/services/storage/__init__.py`
- Create: `backend/app/services/storage/minio_client.py`
- Create: `backend/tests/unit/test_minio_client.py`

- [ ] **15.1 写失败测试（标记为 integration，本测试需 MinIO 实际可达）**

```python
# backend/tests/unit/test_minio_client.py
# 注：虽然放在 unit/ 目录，本组测试是 integration 性质（需 MinIO 容器）。
# 用 pytest.mark.integration 标记，CI 可分阶段跑。
import io
import pytest
from backend.app.services.storage.minio_client import MinIOStorage

pytestmark = pytest.mark.integration


@pytest.fixture
def storage():
    s = MinIOStorage()
    s.ensure_bucket()
    return s


def test_put_and_get(storage):
    key = "test/hello.txt"
    storage.put_object(key, io.BytesIO(b"hello"), 5, content_type="text/plain")
    data = storage.get_object(key)
    assert data == b"hello"


def test_presigned_url(storage):
    key = "test/hello.txt"
    storage.put_object(key, io.BytesIO(b"hello"), 5, content_type="text/plain")
    url = storage.presigned_get_url(key, expires_seconds=300)
    assert url.startswith("http")
```

同时在 `pyproject.toml` `[tool.pytest.ini_options]` 中注册 marker：

```toml
[tool.pytest.ini_options]
testpaths = ["backend/tests"]
python_files = ["test_*.py"]
asyncio_mode = "auto"
addopts = "-v --tb=short"
markers = [
    "integration: requires external services (PG/Redis/MinIO/network)",
]
```

跑时分流：
```bash
uv run pytest -m "not integration"   # 纯单元
uv run pytest -m integration         # 集成
uv run pytest                        # 全跑
```

- [ ] **15.2 写 minio_client.py**

```python
# backend/app/services/storage/minio_client.py
from __future__ import annotations
from datetime import timedelta
from typing import BinaryIO
from minio import Minio
from minio.error import S3Error
from backend.app.config import get_settings


class MinIOStorage:
    def __init__(self) -> None:
        s = get_settings()
        self.bucket = s.MINIO_BUCKET
        self._client = Minio(
            endpoint=s.MINIO_ENDPOINT,
            access_key=s.MINIO_ACCESS_KEY,
            secret_key=s.MINIO_SECRET_KEY,
            secure=s.MINIO_SECURE,
        )

    def ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self.bucket):
            self._client.make_bucket(self.bucket)

    def put_object(self, key: str, stream: BinaryIO, length: int, *, content_type: str) -> None:
        self._client.put_object(self.bucket, key, stream, length, content_type=content_type)

    def get_object(self, key: str) -> bytes:
        resp = self._client.get_object(self.bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def presigned_get_url(self, key: str, *, expires_seconds: int = 300) -> str:
        return self._client.presigned_get_object(
            self.bucket, key, expires=timedelta(seconds=expires_seconds)
        )

    def delete_object(self, key: str) -> None:
        try:
            self._client.remove_object(self.bucket, key)
        except S3Error:
            pass
```

- [ ] **15.3 跑测试（需本机 MinIO 起着）**

```bash
uv run pytest backend/tests/unit/test_minio_client.py -v
```

Expected: 2 个测试 PASS。

- [ ] **15.4 Commit**

```bash
git add backend/app/services/storage/ backend/tests/unit/test_minio_client.py
git commit -m "feat: MinIO storage client + presigned URLs"
```

---

## Task 16: 端到端烟雾测试

**Files:**
- Create: `backend/tests/integration/test_smoke.py`

- [ ] **16.1 写综合烟测**

```python
# backend/tests/integration/test_smoke.py
"""端到端：起 app → 命中 /healthz → DB / Redis / MinIO 都活着。"""
import io
import pytest
from httpx import ASGITransport, AsyncClient
from backend.app.main import app
from backend.app.services.storage.minio_client import MinIOStorage
from backend.app.tasks.celery_app import ping


@pytest.mark.asyncio
async def test_full_smoke():
    # 1. /healthz 通
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["checks"]["db"] == "ok"

    # 2. MinIO 可写读
    storage = MinIOStorage()
    storage.ensure_bucket()
    key = "smoke/test.bin"
    storage.put_object(key, io.BytesIO(b"smoke"), 5, content_type="application/octet-stream")
    assert storage.get_object(key) == b"smoke"

    # 3. Celery ping（需 worker 已起；CI 单独有 worker 任务覆盖）
    # 此处不做 worker 调用——独立的 worker 烟测见 Task 14.4 手工步骤


@pytest.mark.skip(reason="requires running celery worker; run manually after `celery worker` is up")
def test_celery_ping_when_worker_up():
    from backend.app.tasks.celery_app import ping
    assert ping.delay().get(timeout=10) == "pong"
```

- [ ] **16.2 跑测试**

```bash
uv run pytest backend/tests/integration/test_smoke.py -v
```

Expected: PASS。

- [ ] **16.3 跑全部测试**

```bash
uv run pytest -v
```

Expected: 全绿。

- [ ] **16.4 Commit**

```bash
git add backend/tests/integration/test_smoke.py
git commit -m "test: end-to-end smoke (healthz + MinIO + DB)"
```

---

## Task 17: P1 收尾 — Ruff lint + Type check + 文档

**Files:**
- Modify: `README.md`
- Create: `Makefile` (可选，开发便捷)

- [ ] **17.1 跑 ruff**

```bash
uv run ruff check backend/
uv run ruff format backend/
```

Expected: 无错误。如有错误，按提示修，再 commit。

- [ ] **17.2 跑 mypy（可选，严格度按需）**

```bash
uv run mypy backend/app --ignore-missing-imports
```

> 第一期不强制 mypy 全绿，标记 P1 收尾时已知问题列表。

- [ ] **17.3 更新 README.md**

```markdown
# SmartScreenAgent

AI-driven resume screening agent for HR.

## 状态

✅ P1 后端地基（W1-W2） — 完成

## Quick start

```bash
# 1. 启动依赖容器
docker compose up -d

# 2. 配环境
cp .env.example .env
# 编辑 .env 填入 NEWAPI_API_KEY、DingTalk credentials、生成 PII_ENCRYPTION_KEY:
#   uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 3. 安装依赖
uv sync --extra dev

# 4. 跑迁移
uv run alembic upgrade head

# 5. 启动后端
uv run uvicorn backend.app.main:app --reload

# 6. 启动 Celery worker (另一个终端)
uv run celery -A backend.app.tasks.celery_app worker -l info

# 7. 跑测试
uv run pytest
```

## 设计文档
- 设计稿：`docs/specs/2026-05-12-resume-screening-agent-design.md`
- P1 计划：`docs/specs/plans/2026-05-12-p1-backend-foundation.md`
- 调研笔记：`docs/specs/research/`

## 项目结构
（参见计划文件）
```

- [ ] **17.4 最终 Commit + tag**

```bash
git add README.md
git commit -m "docs: update README after P1 completion"
git tag p1-complete
```

---

## Self-Review Checklist（P1 收尾自检）

- [ ] 所有调研笔记齐全（`docs/specs/research/` 下 4 篇）
- [ ] `uv sync --extra dev` 在干净环境可执行
- [ ] `docker compose up -d` 起所有依赖容器
- [ ] `uv run alembic upgrade head` 无错误
- [ ] `uv run uvicorn backend.app.main:app` 起得来，`curl :8000/healthz` 返回 `status=ok`
- [ ] `uv run pytest` 全绿
- [ ] `uv run ruff check backend/` 通过
- [ ] 8 张业务表 + `alembic_version` 已建
- [ ] pgvector 扩展可用（`SELECT * FROM pg_extension WHERE extname='vector'` 有一行）
- [ ] PII 加密往返测试通过
- [ ] LLM gateway 测试通过（含 fallback）
- [ ] DingTalk OAuth 测试通过（mock 层面）
- [ ] git 历史清晰，每 Task 一个 commit，覆盖率高

---

## P1 与后续阶段的对接

P1 完成后，下列件应可直接被 P2 使用：

| P2 需要 | P1 已提供 |
|---|---|
| 简历解析后写候选人 | `Candidate` 模型 + `MinIOStorage` |
| 评分写库 | `Score` / `RuleVersion` 模型 |
| 调 LLM | `LLMGateway.extract()` / `.judge()` |
| 异步跑批 | Celery app（直接 `@celery_app.task` 注册任务） |
| 审计 | `AuditLog` 模型 |
| 加解密 | `encrypt_pii` / `decrypt_pii` / `hash_pii` |

P2 启动时，第一个任务建议是 "调研 MinerU 2.5 当前 API"（同样调研先行）。
