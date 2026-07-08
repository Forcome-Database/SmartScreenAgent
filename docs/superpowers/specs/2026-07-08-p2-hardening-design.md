# P2 加固设计

## 背景

SmartScreenAgent 当前已经完成 P1 后端地基与 P2 评分闭环：

- FastAPI、SQLAlchemy、Alembic、Redis/Celery、MinIO、LLMGateway、DingTalk OAuth 骨架已存在。
- P2 已实现 Excel 规则导入、MinerU 解析客户端、LLM 简历抽取、硬筛、规则引擎、LLM judge、评分落库、候选人上传与评分 API。
- 当前验证结果：`uv run pytest` 为 66 passed / 1 skipped；`uv run ruff check backend` 失败；`mypy --explicit-package-bases backend/app --ignore-missing-imports` 有 3 个可定位问题。

完整实现路线采用“主流程先稳，再补闭环”：

1. P2 加固。
2. JWT/RBAC 与安全边界。
3. HR Web 工作台。
4. 规则、What-If、黄金集与复核闭环。
5. 钉钉招聘同步与 Hermes/MCP。

本设计只覆盖阶段 1。它不是缩减功能，而是为后续完整实现建立稳定基线。

## 目标

阶段 1 的目标是把现有 P2 后端从“能跑通”提升到“可持续开发、可验证、可定位问题”的状态。

验收标准：

- `uv run pytest` 通过，保持现有端到端覆盖。
- `uv run pytest -m "not integration"` 在不启动容器时通过。
- `uv run pytest -m integration` 在 `docker compose up -d` 后通过；未启动容器时不出现误报型错误。
- `uv run ruff check backend` 通过。
- `uv run mypy --explicit-package-bases backend/app --ignore-missing-imports` 通过。
- README 或阶段文档明确开发、测试、依赖容器、MinerU 模式和安全边界。

## 非目标

本阶段不实现以下 P3/P4 功能：

- `/api/v1/candidates/*` 的 JWT/RBAC 强制鉴权。
- Web 前端、候选人列表、评分卡、规则编辑器。
- What-If、规则 diff、黄金集回归。
- HR 复核反馈回流。
- 钉钉招聘 API 同步。
- Hermes/MCP 工具。

这些功能会在后续阶段完整实现。

## 设计

### 1. 质量门禁

现状问题集中在样式和类型门禁，而不是大规模业务缺陷。

处理原则：

- 优先做机械性、局部修复，不重构模块边界。
- 不改变外部 API 行为。
- 不为只用一次的逻辑增加工具函数。
- 对测试文件同样执行 Ruff，避免后续质量门禁被测试代码拖住。

具体方向：

- `backend/app/rules/excel_importer.py`：
  - `Iterable` 改从 `collections.abc` 导入。
  - `zip(...)` 明确 `strict=True` 或选择更符合语义的写法。
  - `_pick_method` 返回值收窄到 `RuleDimension.method` 允许的字面量。
  - 修复 mypy 对循环变量复用的误判，避免同一变量在 `RuleDimension` 与 `JudgeDimension` 间交叉推断。
- `backend/app/rules/schema.py` 与 `backend/app/services/parser/extractor.py`：
  - 去掉可由 `from __future__ import annotations` 支持的字符串类型注解。
- `backend/app/routers/health.py`：
  - 处理 `redis.asyncio` 类型声明中 `ping()` 可能被标为 `Awaitable[bool] | bool` 的问题，让检查命令稳定通过。
- `backend/app/scoring/llm_judge.py`：
  - 仅拆行，不改 prompt 语义。
- 测试文件：
  - 删除未使用 import、整理 import 顺序、拆分超长行。

### 2. 测试分层

当前 `backend/tests/unit/test_minio_client.py` 虽标记为 `integration`，但位于 unit 目录。执行 `uv run pytest -m integration` 且 PostgreSQL 不可达时，integration 目录下用例会被 session fixture 跳过，MinIO 用例却仍会被选中并直接访问 `localhost:9000`，导致误报。

处理策略：

- 保留 MinIO 真实集成测试，不把它 mock 掉。
- 给 MinIO 测试增加独立可达性检查；MinIO 不可达时跳过。
- 保持 `uv run pytest -m "not integration"` 不访问外部服务。
- 保持容器就绪后完整集成测试能跑通。

这个方案比移动文件更小，避免因测试路径调整引入额外变更。

### 3. MinerU 契约加固

`MinerUClient` 当前已按调研结论选择 HTTP 服务模式，但代码注释仍标记 `/file_parse` response schema 需要运行时验证。

阶段 1 做到：

- 把 HTTP 响应解析集中在 `MinerUClient` 内部，不让上层依赖不稳定字段。
- 对 `{markdown, layout}`、常见嵌套字段或缺失字段给出明确处理策略。
- 响应无法解析时在系统边界抛出清晰错误，包含足够定位信息，但不泄露文件内容。
- 单元测试覆盖成功响应和无效响应。

暂不做：

- 嵌入 MinerU Python library。
- 在 `docker-compose.yml` 内加入 MinerU 服务。
- docx fallback 的真实解析实现。

原因：设计调研已经建议生产使用独立 `mineru-api` 服务；本阶段只稳定客户端契约。

### 4. 主流程错误边界

P2 主流程是：

`upload_resume → run_parse_and_score → MinerUClient.parse → ResumeExtractor.extract → Candidate upsert → ScoringPipeline.run → Score/AuditLog`

阶段 1 保持这个流程不变，只加固系统边界：

- 上传临时文件清理保持现有 background task 方式。
- 外部服务失败时保留异常，不在内部业务函数里吞掉错误。
- 候选人去重仍以 `pii_hash` 为准。
- 评分落库仍由 `ScoringPipeline` 单点负责。

如果发现现有错误信息无法定位，会在边界处调整异常文本或测试断言，但不引入新的任务队列或状态表。

### 5. 文档基线

README 需要反映真实运行方式：

- 本地依赖：`docker compose up -d` 启动 PostgreSQL、Redis、MinIO。
- 非集成测试：`uv run pytest -m "not integration"`。
- 集成测试：依赖容器启动后运行 `uv run pytest -m integration`。
- 全量测试：容器启动后运行 `uv run pytest`。
- 静态检查：`uv run ruff check backend` 与 `uv run mypy --explicit-package-bases backend/app --ignore-missing-imports`。
- MinerU：`MINERU_MODE=stub` 用于本地离线开发，`http` 用于生产/集成；`library` 仍不是本阶段目标。
- 安全边界：P2 API 仍未强制 JWT/RBAC，不能公网部署。

## 风险与缓解

- **Ruff 自动修复可能影响 import 顺序和注释位置**：优先手工处理少量业务文件，必要时只对测试文件用格式化命令。
- **Mypy 修复可能诱发过度类型设计**：只修当前 3 个问题，不引入全局类型系统改造。
- **MinerU 真实服务契约仍未现场验证**：本阶段收紧客户端和测试，真实容器接入留后续集成阶段。
- **依赖容器环境差异**：测试跳过逻辑需要只跳过不可达服务，不掩盖已启动但行为异常的失败。

## 验证计划

阶段完成后运行：

```bash
uv run pytest -m "not integration"
uv run pytest -m integration
uv run pytest
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
```

集成测试前运行：

```bash
docker compose up -d
docker compose ps
```

预期：

- 依赖容器健康时，全量测试通过。
- 依赖容器未启动时，非集成测试通过；集成测试跳过不可达服务，不产生误报型 error。

## 后续阶段衔接

阶段 1 完成后，阶段 2 进入 JWT/RBAC：

- 复用现有 `DingTalkOAuthClient`、`create_access_token`、`get_current_user`。
- 给候选人上传、评分、规则导入/管理等写入口加权限边界。
- 审计日志继续由后端统一写入。

阶段 3 涉及前端时，使用 `ui-ux-pro-max` 建立设计系统，面向 HR/SaaS 后台采用高密度、低干扰、可扫描的信息架构。
