# P2 加固 Implementation Plan

> **历史状态：已执行计划。** 复选框未在执行期间维护；完成证据为提交 `077901e`、`a8746f5`、`cb18418`、`c911e32` 及当前测试/静态检查。后续计划索引见 [`README.md`](README.md)。

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 P2 评分闭环加固到测试、lint、类型检查和 MinerU 契约都稳定可验证的状态。

**Architecture:** 保持当前 FastAPI + SQLAlchemy + Celery + MinIO + MinerUClient 的边界不变，只在系统边界补清晰契约和测试。MinIO 测试增加独立可达性判断；MinerU HTTP 响应解析集中在 `MinerUClient`；API 层只负责把解析器失败映射为 502。质量门禁只做局部修复，不引入新业务能力。

**Tech Stack:** Python 3.14、FastAPI、pytest、pytest-asyncio、respx、httpx、MinIO SDK、Ruff、mypy。

---

## 文件结构

- Modify: `backend/tests/unit/test_minio_client.py`  
  增加 MinIO 可达性 fixture；不可达时跳过，认证或 bucket 操作失败时保持失败。
- Modify: `backend/tests/unit/test_mineru_client.py`  
  增加 MinerU 响应契约和失败契约单元测试。
- Modify: `backend/app/services/parser/mineru_client.py`  
  新增 `MinerUParseError` 和私有响应解析逻辑，统一返回 `ParseResult`。
- Modify: `backend/app/routers/candidates.py`  
  捕获 `MinerUParseError` 并返回 502。
- Modify: `backend/tests/integration/test_candidates_api.py`  
  增加 upload API 对 MinerU 失败映射的测试；同时修复 lint 问题。
- Modify: `backend/app/rules/excel_importer.py`  
  修复 Ruff 与 mypy 暴露的类型/导入问题。
- Modify: `backend/app/rules/schema.py`  
  去掉不必要的字符串返回类型注解。
- Modify: `backend/app/routers/health.py`  
  修复 Redis `ping()` 类型检查问题。
- Modify: `backend/app/scoring/llm_judge.py`  
  拆分超长行，不改 prompt 语义。
- Modify: `backend/app/scoring/pipeline.py`、`backend/app/scoring/rule_engine.py`、`backend/app/services/parser/extractor.py`、`backend/app/services/parser/pii.py`  
  整理导入和少量类型提示。
- Modify: affected test files under `backend/tests/`  
  删除未使用 import、整理 import 顺序、拆分超长行。
- Modify: `README.md`  
  补充 P2 加固后的测试、lint、mypy、容器和 MinerU 模式说明。

## Chunk 1: 外部依赖测试边界

### Task 1: MinIO 集成测试可达性判断

**Files:**
- Modify: `backend/tests/unit/test_minio_client.py`

- [ ] **Step 1: 实现 MinIO 不可达跳过逻辑**

在 `backend/tests/unit/test_minio_client.py` 中新增一个小的可达性 helper，先让测试表达目标行为：

```python
import socket


def _minio_tcp_reachable(endpoint: str, timeout: float = 1.5) -> bool:
    host, port_text = endpoint.rsplit(":", 1)
    try:
        with socket.create_connection((host, int(port_text)), timeout=timeout):
            return True
    except OSError:
        return False
```

修改 `storage` fixture：

```python
@pytest.fixture
def storage():
    settings = get_settings()
    if not _minio_tcp_reachable(settings.MINIO_ENDPOINT):
        pytest.skip("MinIO not reachable")
    s = MinIOStorage()
    s.ensure_bucket()
    return s
```

并补充 import：

```python
from backend.app.config import get_settings
```

- [ ] **Step 2: 运行 MinIO 未启动场景验证**

如果当前容器正在运行，先只记录已有状态，不强制停容器。若要手动验证未启动场景，运行：

```bash
docker compose stop minio
uv run pytest backend/tests/unit/test_minio_client.py -v
```

Expected: `2 skipped`，skip reason 包含 `MinIO not reachable`。

继续验证 marker 入口：

```bash
uv run pytest -m integration
```

Expected: 不应出现 error。MinIO 用例应 skipped；integration 目录下其它用例按各自依赖规则通过或跳过。

继续验证非集成入口：

```bash
uv run pytest -m "not integration"
```

Expected: pass，且不触发 MinIO 连接。

- [ ] **Step 3: 运行 MinIO 启动场景验证**

```bash
docker compose up -d minio
uv run pytest backend/tests/unit/test_minio_client.py -v
```

Expected: `2 passed`。

继续验证 marker 入口：

```bash
uv run pytest -m integration
```

Expected: MinIO 用例通过；其它 integration 用例按当前依赖状态通过或跳过，不应出现 error。

- [ ] **Step 4: 提交**

```bash
git add backend/tests/unit/test_minio_client.py
git commit -m "测试：稳定 MinIO 集成测试跳过逻辑"
```

## Chunk 2: MinerU HTTP 契约与 API 映射

### Task 2: MinerU 响应契约测试

**Files:**
- Modify: `backend/tests/unit/test_mineru_client.py`
- Modify: `backend/app/services/parser/mineru_client.py`

- [ ] **Step 1: 写响应形状测试**

在 `backend/tests/unit/test_mineru_client.py` 增加参数化测试：

```python
@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("body", "expected_markdown", "expected_layout"),
    [
        ({"markdown": "# Resume", "layout": {"pages": 1}}, "# Resume", {"pages": 1}),
        ({"markdown": "# Primary", "md_content": "# Alias", "layout": {}}, "# Primary", {}),
        ({"data": {"markdown": "# Data", "layout": {"source": "data"}}}, "# Data", {"source": "data"}),
        ({"result": {"markdown": "# Result", "layout": {}}}, "# Result", {}),
        ({"md_content": "# Alias", "layout": {}}, "# Alias", {}),
        ({"data": {"md_content": "# Wrapped Alias", "layout": {}}}, "# Wrapped Alias", {}),
    ],
)
async def test_http_mode_accepts_supported_response_shapes(
    monkeypatch, tmp_path, body, expected_markdown, expected_layout
):
    monkeypatch.setenv("MINERU_MODE", "http")
    monkeypatch.setenv("MINERU_BASE_URL", "https://mineru.example.com")
    get_settings.cache_clear()
    try:
        pdf = tmp_path / "fake.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        respx.post("https://mineru.example.com/file_parse").mock(
            return_value=Response(200, json=body)
        )
        result = await MinerUClient().parse(pdf)
        assert result.markdown == expected_markdown
        assert result.layout == expected_layout
        assert result.source == "http"
    finally:
        get_settings.cache_clear()
```

- [ ] **Step 2: 写失败契约测试**

继续在同文件添加：

```python
import httpx

from backend.app.services.parser.mineru_client import MinerUParseError


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({}, "missing markdown"),
        ({"markdown": "   "}, "missing markdown"),
        ({"markdown": "# ok", "layout": []}, "invalid layout"),
    ],
)
async def test_http_mode_rejects_invalid_response(monkeypatch, tmp_path, body, message):
    monkeypatch.setenv("MINERU_MODE", "http")
    monkeypatch.setenv("MINERU_BASE_URL", "https://mineru.example.com")
    get_settings.cache_clear()
    try:
        pdf = tmp_path / "fake.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        respx.post("https://mineru.example.com/file_parse").mock(
            return_value=Response(200, json=body)
        )
        with pytest.raises(MinerUParseError, match=message):
            await MinerUClient().parse(pdf)
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("response", "message"),
    [
        (Response(200, content=b"not-json"), "invalid json response"),
        (Response(200, json=[]), "invalid json response"),
    ],
)
async def test_http_mode_rejects_unparseable_json(monkeypatch, tmp_path, response, message):
    monkeypatch.setenv("MINERU_MODE", "http")
    monkeypatch.setenv("MINERU_BASE_URL", "https://mineru.example.com")
    get_settings.cache_clear()
    try:
        pdf = tmp_path / "fake.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        respx.post("https://mineru.example.com/file_parse").mock(return_value=response)
        with pytest.raises(MinerUParseError) as exc_info:
            await MinerUClient().parse(pdf)
        error = str(exc_info.value)
        assert message in error
        assert "mode=http" in error
        assert "https://mineru.example.com/file_parse" in error
        assert "not-json" not in error
        assert "fake.pdf" not in error
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
async def test_http_mode_wraps_non_2xx(monkeypatch, tmp_path):
    monkeypatch.setenv("MINERU_MODE", "http")
    monkeypatch.setenv("MINERU_BASE_URL", "https://mineru.example.com")
    get_settings.cache_clear()
    try:
        pdf = tmp_path / "fake.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        respx.post("https://mineru.example.com/file_parse").mock(
            return_value=Response(500, json={"error": "boom"})
        )
        with pytest.raises(MinerUParseError) as exc_info:
            await MinerUClient().parse(pdf)
        message = str(exc_info.value)
        assert "mode=http" in message
        assert "https://mineru.example.com/file_parse" in message
        assert "status_code=500" in message
        assert "fake.pdf" not in message
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("side_effect", "expected_error"),
    [
        (httpx.ConnectError("connect failed"), "ConnectError"),
        (httpx.TimeoutException("timed out"), "TimeoutException"),
    ],
)
async def test_http_mode_wraps_http_error(monkeypatch, tmp_path, side_effect, expected_error):
    monkeypatch.setenv("MINERU_MODE", "http")
    monkeypatch.setenv("MINERU_BASE_URL", "https://mineru.example.com")
    get_settings.cache_clear()
    try:
        pdf = tmp_path / "fake.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        respx.post("https://mineru.example.com/file_parse").mock(
            side_effect=side_effect
        )
        with pytest.raises(MinerUParseError) as exc_info:
            await MinerUClient().parse(pdf)
        message = str(exc_info.value)
        assert "mode=http" in message
        assert "https://mineru.example.com/file_parse" in message
        assert expected_error in message
        assert "fake.pdf" not in message
    finally:
        get_settings.cache_clear()
```

- [ ] **Step 3: 运行测试确认失败**

```bash
uv run pytest backend/tests/unit/test_mineru_client.py -v
```

Expected: 新增用例因 `MinerUParseError` 未定义或响应解析未实现而失败。

- [ ] **Step 4: 实现 `MinerUParseError` 和响应解析**

在 `backend/app/services/parser/mineru_client.py` 中新增：

```python
class MinerUParseError(RuntimeError):
    pass
```

新增私有函数：

```python
def _response_payload(data: dict) -> dict:
    for key in ("data", "result"):
        nested = data.get(key)
        if isinstance(nested, dict):
            return nested
    return data


def _parse_response(data: dict) -> ParseResult:
    payload = _response_payload(data)
    markdown = payload.get("markdown")
    if not isinstance(markdown, str) or not markdown.strip():
        markdown = payload.get("md_content")
    if not isinstance(markdown, str) or not markdown.strip():
        raise MinerUParseError("missing markdown in MinerU response")
    layout = payload.get("layout", {})
    if layout is None:
        layout = {}
    if not isinstance(layout, dict):
        raise MinerUParseError("invalid layout in MinerU response")
    return ParseResult(markdown=markdown, layout=layout, source="http")
```

修改 `_parse_http`：

```python
    async def _parse_http(self, file_path: Path) -> ParseResult:
        url = f"{self.settings.MINERU_BASE_URL.rstrip('/')}/file_parse"
        headers: dict[str, str] = {}
        if self.settings.MINERU_API_KEY:
            headers["Authorization"] = f"Bearer {self.settings.MINERU_API_KEY}"
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                with file_path.open("rb") as f:
                    resp = await client.post(
                        url, headers=headers, files={"file": (file_path.name, f)}
                    )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            raise MinerUParseError(
                "MinerU HTTP parse failed: "
                f"mode=http url={url} status_code={e.response.status_code}"
            ) from e
        except (httpx.HTTPError, OSError) as e:
            raise MinerUParseError(
                f"MinerU HTTP parse failed: mode=http url={url} error={type(e).__name__}"
            ) from e
        except ValueError as e:
            raise MinerUParseError(
                f"invalid json response from MinerU: mode=http url={url}"
            ) from e
        if not isinstance(data, dict):
            raise MinerUParseError(
                f"invalid json response from MinerU: mode=http url={url}"
            )
        return _parse_response(data)
```

- [ ] **Step 5: 运行 MinerU 单元测试**

```bash
uv run pytest backend/tests/unit/test_mineru_client.py -v
```

Expected: all passed。

### Task 3: 上传 API 映射 MinerUParseError 为 502

**Files:**
- Modify: `backend/app/routers/candidates.py`
- Modify: `backend/tests/integration/test_candidates_api.py`

- [ ] **Step 1: 写失败映射测试**

在 `backend/tests/integration/test_candidates_api.py` 添加：

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_parser_failure_returns_502(client, db_session, monkeypatch):
    from backend.app.services.parser.mineru_client import MinerUParseError

    monkeypatch.setattr(
        "backend.app.tasks.ingest.MinerUClient",
        lambda: SimpleNamespace(
            parse=AsyncMock(side_effect=MinerUParseError("missing markdown"))
        ),
    )
    resp = await client.post(
        "/api/v1/candidates/upload",
        files={"file": ("r.pdf", b"%PDF-1.4 dummy", "application/pdf")},
    )
    assert resp.status_code == 502
    assert resp.json()["detail"] == "Resume parser failed"
    assert "missing markdown" not in resp.text
    assert "mineru.example.com" not in resp.text
    assert "r.pdf" not in resp.text
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest backend/tests/integration/test_candidates_api.py::test_upload_parser_failure_returns_502 -v -m integration
```

Expected: FAIL，当前 API 未捕获 `MinerUParseError`。

- [ ] **Step 3: 实现 API 映射**

在 `backend/app/routers/candidates.py` import：

```python
from backend.app.services.parser.mineru_client import MinerUParseError
```

包住 `run_parse_and_score`：

```python
    try:
        candidate_id = await run_parse_and_score(
            db=db,
            file_path=tmp_path,
            source="upload",
            source_external_id=None,
            jd_code=jd_code,
        )
    except MinerUParseError as e:
        raise HTTPException(
            status_code=502,
            detail="Resume parser failed",
        ) from e
```

- [ ] **Step 4: 运行 API 测试**

```bash
uv run pytest backend/tests/integration/test_candidates_api.py -v -m integration
```

Expected: all passed。

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/parser/mineru_client.py backend/app/routers/candidates.py backend/tests/unit/test_mineru_client.py backend/tests/integration/test_candidates_api.py
git commit -m "加固：明确 MinerU 响应契约和上传失败映射"
```

## Chunk 3: 质量门禁与文档

### Task 4: 修复 Ruff 与 mypy 问题

**Files:**
- Modify: `backend/app/rules/excel_importer.py`
- Modify: `backend/app/rules/schema.py`
- Modify: `backend/app/routers/health.py`
- Modify: `backend/app/scoring/llm_judge.py`
- Modify: `backend/app/scoring/pipeline.py`
- Modify: `backend/app/scoring/rule_engine.py`
- Modify: `backend/app/services/parser/extractor.py`
- Modify: `backend/app/services/parser/pii.py`
- Modify: affected test files under `backend/tests/`

- [ ] **Step 1: 运行 Ruff 获取当前列表**

```bash
uv run ruff check backend
```

Expected: 当前失败列表包含 import 排序、未使用 import、行长、`zip(strict=...)`、typing 迁移等。

- [ ] **Step 2: 先应用安全自动修复**

```bash
uv run ruff check backend --fix
```

Expected: 自动修复 import 排序、未使用 import、简单 typing 迁移。不要使用 `--unsafe-fixes`。

执行后检查差异：

```bash
git diff -- backend
```

Expected: 只包含机械性格式/导入修复，不接受业务行为变化。

- [ ] **Step 3: 手工修复剩余 Ruff 问题**

按剩余输出处理：

- `backend/app/rules/excel_importer.py`：
  - `from collections.abc import Iterable`
  - `zip(labels, layout.tier_cols, strict=True)`
  - `zip(layout.tier_cols, layout.tier_labels, strict=True)`
  - 未使用的 `idx` 删除或改为不产生未使用变量的循环。
- `backend/app/scoring/llm_judge.py`：
  - 把 `suggested_interview_questions` schema 拆成多行。
  - 把长 prompt 字符串拆成多个短字符串。
- `backend/app/rules/schema.py`：
  - 将 `def _weights_sum_to_total(self) -> "RuleSchema"` 改为 `-> RuleSchema`。
- `backend/app/services/parser/extractor.py`：
  - 将 `from_dict(...) -> "ExtractedResume"` 改为 `-> ExtractedResume`。
- `backend/app/scoring/pipeline.py`：
  - 仅整理 import 顺序，不改三段评分逻辑。
- `backend/app/scoring/rule_engine.py`：
  - `Callable` 改从 `collections.abc` 导入；底部方法模块 import 保持注册副作用，必要时只调整顺序。
- `backend/app/services/parser/pii.py`：
  - 仅整理 import 顺序。
- 测试文件：
  - 删除未使用 import。
  - 拆分超长断言和字典字面量。
  - 只修改 Ruff 输出点名的测试文件；提交前在 commit message 或最终摘要中列出实际修改的测试文件，避免扩大范围。

- [ ] **Step 4: 修复 mypy 问题**

运行：

```bash
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
```

针对当前问题修复：

- `backend/app/rules/excel_importer.py`：
  - 从 `typing` 导入 `Literal` 或定义类型别名：

```python
RuleMethod = Literal["tiered_keyword_match", "experience_years", "lookup"]
```

  - `_pick_method(...) -> RuleMethod`
  - 创建 `RuleDimension` 和 `JudgeDimension` 时不要复用同一个变量名承载不同模型类型。
- `backend/app/routers/health.py`：
  - 用 `inspect.isawaitable` 或局部 `Any` 消除 `ping()` 类型歧义：

```python
import inspect

ping_result = r.ping()
if inspect.isawaitable(ping_result):
    pong = await ping_result
else:
    pong = ping_result
```

- [ ] **Step 5: 运行质量门禁**

```bash
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
```

Expected: both pass。

- [ ] **Step 6: 运行测试**

```bash
uv run pytest -m "not integration"
uv run pytest
```

Expected: tests pass；如果 MinIO/PostgreSQL 容器未启动，全量测试中的 integration 用例可按既定规则跳过，不应出现 error。

- [ ] **Step 7: 提交**

```bash
git add backend/app backend/tests
git commit -m "质量：通过 Ruff 和 mypy 门禁"
```

### Task 5: 更新 README 验证说明

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 更新 README**

在 Quick start 或 P2 章节补充：

````markdown
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
````

- [ ] **Step 2: 运行文档相关检查**

```bash
uv run pytest -m "not integration"
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
```

Expected: all pass。

同时人工检查 README Markdown 片段能正确渲染，尤其是嵌套代码块没有提前闭合。当前项目没有 markdown lint 工具，本阶段不额外引入依赖。

- [ ] **Step 3: 提交**

```bash
git add README.md
git commit -m "文档：补充 P2 加固验证说明"
```

## Chunk 4: 最终验收

### Task 6: 全量验证与收尾

**Files:**
- No code files expected.

- [ ] **Step 1: 检查工作区**

```bash
git status --short --branch
```

Expected: 只允许 `.superpowers/`、`backend.zip` 等既有未跟踪文件存在；已修改代码和 README 都应提交。

- [ ] **Step 2: 非集成测试**

```bash
uv run pytest -m "not integration"
```

Expected: pass。

- [ ] **Step 3: 容器未启动场景的集成测试入口**

如果当前容器正在运行，先记录状态；需要验证未启动场景时运行：

```bash
docker compose stop
uv run pytest -m integration
```

Expected: 不出现 error；不可达外部服务对应用例 skip 或按已有规则处理。

- [ ] **Step 4: 启动依赖容器**

```bash
docker compose up -d
docker compose ps
```

Expected: PostgreSQL、Redis、MinIO healthy；如果 `docker compose ps` 不显示 health 字段，应确认三个服务处于 running/healthy 等价状态。

- [ ] **Step 5: 集成测试**

```bash
uv run pytest -m integration
```

Expected: pass，Celery worker 手动烟测用例保持 skip。

- [ ] **Step 6: 全量测试**

```bash
uv run pytest
```

Expected: pass。

- [ ] **Step 7: 静态检查**

```bash
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
```

Expected: both pass。

- [ ] **Step 8: README 内容验收**

```powershell
Select-String -Path README.md -SimpleMatch "docker compose up -d"
Select-String -Path README.md -SimpleMatch 'pytest -m "not integration"'
Select-String -Path README.md -SimpleMatch "pytest -m integration"
Select-String -Path README.md -SimpleMatch "MINERU_MODE=stub"
Select-String -Path README.md -SimpleMatch "MINERU_MODE=http"
Select-String -Path README.md -SimpleMatch "library"
Select-String -Path README.md -SimpleMatch "JWT/RBAC"
```

Expected: 每条命令都能找到对应说明。

- [ ] **Step 9: 最终状态记录**

```bash
git log --oneline -n 6
git status --short --branch
```

Expected: 最近提交包含本计划的阶段性提交；工作区无未提交的代码变更。
