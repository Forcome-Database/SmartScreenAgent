# P2 API JWT/RBAC 设计

> **状态：已并入 WP1 并完成本地实现验证。** 最终实现契约由 [`2026-07-16-wp1-security-and-raw-file-integrity-design.md`](2026-07-16-wp1-security-and-raw-file-integrity-design.md) 接管；WP1 托管 CI 验收尚待提交后执行。

## 背景

P2 后端已经完成简历上传、解析、抽取、评分和质量门禁加固，但候选人上传与重新评分接口仍是公开接口。README 已明确标注该状态不能直接公网部署。

本阶段目标是先补齐现有 P2 主流程的安全边界，复用 P1 已实现的钉钉 OAuth 登录、JWT 签发和 `get_current_user` 依赖，不引入前端、不扩展候选人归属模型、不重构评分流程。

## 目标

- `POST /api/v1/candidates/upload` 必须携带有效 Bearer token。
- `POST /api/v1/candidates/{candidate_id}/score` 必须携带有效 Bearer token。
- 允许角色：`hr`、`hr_lead`、`admin`。
- 拒绝角色：`dept_head`。
- 未携带 token 返回 `401`。
- token 无效、过期、用户不存在返回 `401`。
- token 有效但角色不允许返回 `403`。
- `/auth/dingtalk/login` 保持公开，继续作为换取 JWT 的入口。
- `/healthz` 保持公开，继续用于容器与部署健康检查。

## 非目标

- 不实现前端登录页或路由守卫。
- 不实现候选人与 JD 的岗位归属权限过滤。
- 不实现 `dept_head` 的部门级访问，因为当前数据模型没有部门归属字段。
- 不保护只读查询接口；当前 P2 只有上传和评分写入口。
- 不改变钉钉 OAuth 流程。
- 不改变 JWT 载荷结构，继续使用 `sub` 与 `role`。

## 权限模型

当前 `users.role` 是字符串字段，设计文档定义的角色包括：

| 角色 | 上传简历 | 触发评分 | 原因 |
|---|---:|---:|---|
| `hr` | 允许 | 允许 | HR 专员是 P2 主使用者 |
| `hr_lead` | 允许 | 允许 | HR 负责人可处理全部 P2 主流程 |
| `admin` | 允许 | 允许 | 管理员可执行系统操作 |
| `dept_head` | 拒绝 | 拒绝 | 当前缺少部门归属字段，不能安全限定范围 |

权限判断只在 FastAPI 边界做。内部的 `run_parse_and_score` 与 `ScoringPipeline` 保持纯业务编排，不关心 HTTP 用户身份。

## 接口行为

### 候选人上传

`POST /api/v1/candidates/upload`

鉴权后保持现有行为：

- 读取 multipart 文件。
- 写临时文件。
- 调用 `run_parse_and_score`。
- `MinerUParseError` 继续映射为 `502 Resume parser failed`。
- 成功返回 `{"candidate_id": ..., "status": "parsed"}`。

鉴权失败行为：

| 场景 | HTTP 状态 | detail |
|---|---:|---|
| 缺少 `Authorization` | `401` | `Missing Bearer token` |
| 不是 `Bearer ...` | `401` | `Missing Bearer token` |
| token 无效或过期 | `401` | `Invalid token` |
| token 指向的用户不存在 | `401` | `User not found` |
| 用户角色不允许 | `403` | `Forbidden` |

### 重新评分

`POST /api/v1/candidates/{candidate_id}/score`

鉴权后保持现有行为：

- 查找 `jd_code` 对应 JD。
- JD 不存在仍返回 `404`。
- 调用 `ScoringPipeline.run`。
- 成功返回 `score_id`、`total_score`、`grade`、`rejected`。

鉴权失败行为与上传接口一致。

## 实现设计

### 角色依赖

在 `backend/app/deps.py` 中新增角色依赖工厂：

- 输入允许角色集合。
- 依赖现有 `get_current_user`。
- 当前用户角色不在集合内时抛 `HTTPException(status_code=403, detail="Forbidden")`。
- 返回 `User`，供未来需要审计当前操作者时复用。

建议接口：

```python
def require_roles(*roles: str):
    async def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return user

    return _dep
```

### Token 错误边界

当前 `get_current_user` 会把 `decode_token` 的具体异常文本透出到 HTTP detail。阶段 2 需要收紧为稳定错误：

- `decode_token` 仍可抛 `ValueError`，保留内部原因链。
- `get_current_user` 捕获后统一返回 `401 Invalid token`。
- 不把 PyJWT 的异常文本、过期时间或签名错误细节暴露给 API 调用方。

缺少 Bearer token 与用户不存在仍保持稳定文本，便于前端区分重新登录和权限不足。

### Candidates Router

在 `backend/app/routers/candidates.py` 中挂依赖：

```python
from backend.app.deps import require_roles
from backend.app.models import User

WRITE_ROLES = ("hr", "hr_lead", "admin")
```

在 `upload_resume` 与 `score_candidate` 参数中加入：

```python
current_user: User = Depends(require_roles(*WRITE_ROLES))
```

当前阶段不使用 `current_user` 写审计字段，因为现有 `scores` 与 `audit_logs` 的 P2 写入路径没有用户上下文参数。为避免扩大改动，不在本阶段改 `ScoringPipeline` 签名。

## 测试设计

### 单元测试

本阶段不新增单元测试。`require_roles` 是 FastAPI 依赖工厂，核心风险在依赖链是否真实触发 `get_current_user`、数据库用户查询和路由拒绝逻辑，因此统一用集成测试覆盖真实 HTTP 边界。

### 集成测试

在 `backend/tests/integration/test_candidates_api.py` 中补充：

- 上传接口无 token 返回 `401 Missing Bearer token`。
- 上传接口无效 token 返回 `401 Invalid token`。
- 上传接口 `dept_head` token 返回 `403 Forbidden`。
- 上传接口 `hr` token 维持原成功行为。
- 上传接口 parser 失败映射测试使用 `hr` token 后仍返回 `502 Resume parser failed`。
- 评分接口无 token 返回 `401 Missing Bearer token`。
- 评分接口 `dept_head` token 返回 `403 Forbidden`。
- 评分接口 `hr` token 维持原成功行为。
- 评分接口未知 JD 测试使用 `hr` token 后仍返回 `404`。

测试创建用户时直接写 `users` 表，并用 `create_access_token({"sub": str(user.id), "role": user.role})` 生成 token。这样测试复用真实 JWT 与真实 `get_current_user`，避免 mock 掉安全边界。

## 文档更新

README 中 P2 安全边界需要更新：

- 删除“P2 的候选人上传和评分 API 尚未强制 JWT/RBAC，不能直接公网部署”的旧描述。
- 改为说明候选人上传与评分 API 已强制 Bearer JWT。
- 保留“生产部署仍需要前端接入钉钉登录并妥善保存 token”的提醒。
- 如果仍有未保护的未来接口，不在 README 中暗示它们已经存在。

## 验收标准

完成后运行：

```bash
uv run pytest backend/tests/integration/test_candidates_api.py -v -m integration
uv run pytest -m "not integration"
uv run pytest
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
```

预期：

- 候选人上传和评分接口在无 token 时不再执行业务逻辑。
- `hr`、`hr_lead`、`admin` 角色可以执行现有 P2 主流程。
- `dept_head` 被拒绝。
- 现有 P2 成功路径、MinerU 失败映射和未知 JD 行为保持不变。

## 风险与缓解

- **现有集成测试会因为新增鉴权失败**：统一增加测试 helper 创建用户与 Authorization header，逐个测试显式表达身份。
- **错误信息可能泄露 JWT 解析细节**：在 HTTP 依赖层统一映射为稳定文本。
- **`dept_head` 未来需要部门级查看**：当前模型不足，本阶段明确拒绝；后续增加部门/JD 归属字段后再开放。
- **后台任务绕过 HTTP 鉴权**：这是有意设计。Celery 与内部函数由系统调度，不通过用户 Bearer token 控制。

## 后续阶段

JWT/RBAC 落地后，下一步可以进入 HR Web 工作台或规则闭环。涉及前端时必须使用 `ui-ux-pro-max`，并以高密度、低干扰、可扫描的 HR SaaS 后台为设计基调。
