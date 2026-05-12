# Superpowers 工作流最佳实践（大项目向）

> 基于对 obra/superpowers 官方源码、作者 Jesse Vincent 博客（v1、v5）、Hacker News 讨论以及多位重度用户实战博客的调研整理。
> 整理日期：2026-05-12

---

## TL;DR（30 秒版本）

大项目用 Superpowers 的正确姿势：

1. **顶层 Brainstorm 一次**，产出总设计文档 + **子系统切分清单**
2. **逐个子系统循环**：spec → plan → 人审 → subagent 执行 → merge → 进下一个
3. 每个"阶段"必须是**纵切的、独立可跑可测**的子系统（不是 DB 层 / UI 层这种横切）
4. **下一个子系统的 plan 不要预先写**，等上一个 merge 后再写——因为现实会改变接口
5. **单个 plan 内部**：upfront 写完所有 task，每个 task 含精确文件路径 + 完整测试代码

一句话：**总设计先行，子系统纵切，plan 滚动展开。**

---

## 1. 四层文档结构

```
docs/superpowers/
├── specs/
│   ├── 00-overview.md             # 顶层架构（brainstorm 后一次写完）
│   ├── 01-auth-subsystem.md       # 子系统 spec（What/Why，可一次都写出来）
│   ├── 02-data-pipeline.md
│   └── 03-reporting.md
└── plans/
    └── 2026-05-12-auth-subsystem.md   # 只为正在做的子系统写 plan
    # 02、03 还没开始，没有对应 plan 文件
```

| 层级 | 内容 | 何时写 | 可否一次写完 |
|---|---|---|---|
| Overview | 顶层架构、子系统切分、技术栈 | brainstorm 后立刻写 | 可以 |
| Spec (子系统) | What / Why / 接口契约 / 验收标准 | brainstorm 后写 | 可以全部写出来 |
| Plan (子系统) | 精确 task 列表、文件路径、测试代码 | 上一个子系统 merge 之后 | **单个 plan 内部一次写完** |

---

## 2. 完整工作流（六阶段）

引自 Evan Schwartz 实战博客，与官方 skill 链一致：

```
Brainstorming
    ↓ 产出：design doc（人审批 = 第 1 道闸）
Reviewing Options and Tradeoffs
    ↓ 多个方案对比（人选方向 = 第 2 道闸）
Plan Sketch
    ↓ 高层 plan 草图（人审批 = 第 3 道闸）
Design Doc（细化）
    ↓
Implementation Plan
    ↓ 完整 task 级 plan（人审批 = 第 4 道闸，可手改）
Implementation Steps
    ↓ subagent 逐 task 执行 + 主 agent 两阶段 review
Finishing（merge / PR）
```

**4 个人工 approval gate** 是 Superpowers 的核心机制。不要为了快就跳过 gate——跳过的代价远大于 gate 上花的 5-10 分钟。

---

## 3. Skill 调用顺序速查

| 阶段 | Skill | 用途 |
|---|---|---|
| 开场 | `superpowers:using-superpowers` | 进入 Superpowers 工作流（首次） |
| 设计 | `superpowers:brainstorming` | 多轮对话，给方案+tradeoff |
| 隔离 | `superpowers:using-git-worktrees` | 建 worktree，干净工作区 |
| 计划 | `superpowers:writing-plans` | 写完整 plan |
| 执行 | `superpowers:subagent-driven-development` | 推荐：每 task 派 fresh subagent |
| 执行（备选） | `superpowers:executing-plans` | 在当前会话批量执行 |
| 测试 | `superpowers:test-driven-development` | 强制 RED-GREEN-REFACTOR |
| 调试 | `superpowers:systematic-debugging` | 遇 bug 时系统化定位 |
| 评审 | `superpowers:requesting-code-review` | 关键节点请求评审 |
| 验收 | `superpowers:verification-before-completion` | 声明完成前的强制验证 |
| 收尾 | `superpowers:finishing-a-development-branch` | 决定 merge / PR / 清理 |

---

## 4. 单个 plan 的内部结构（writing-plans 官方规范）

每个 plan 文件路径：`docs/superpowers/plans/YYYY-MM-DD-<feature-name>.md`

**强制头部：**

```markdown
# [Feature Name] Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development

**Goal:** [一句话目标]
**Architecture:** [2-3 句架构说明]
**Tech Stack:** [关键技术栈]
```

**强制：先定义文件结构**（v5 新增要求）

写 task 之前先列：要创建/修改哪些文件，每个文件的单一职责，文件之间的接口。这一步把分解决策锁死，再做 task 切分。

**Task 粒度：每步 2-5 分钟，TDD 五步循环：**

```markdown
### Task N: [组件名]

**Files:**
- Create: `exact/path/to/file.py`
- Modify: `exact/path/to/existing.py:123-145`
- Test: `tests/exact/path/to/test.py`

- [ ] Step 1: 写失败测试（含完整测试代码）
- [ ] Step 2: 跑测试确认 FAIL（含命令和预期输出）
- [ ] Step 3: 最小实现（含完整代码）
- [ ] Step 4: 跑测试确认 PASS
- [ ] Step 5: commit（含 commit message）
```

**No Placeholders 红线：**
- 禁止 "TBD" / "implement later" / "fill in details"
- 禁止 "Add appropriate error handling"
- 禁止 "Write tests for the above"（不带具体测试代码）
- 禁止 "Similar to Task N"（重复写出来，agent 可能乱序读）

---

## 5. "阶段"是纵切，不是横切

这是大项目最容易做错的一点。

**反例（横切，不推荐）：**

```
阶段 1：所有模块的数据库层
阶段 2：所有模块的 API 层
阶段 3：所有模块的 UI 层
```

问题：阶段 1 结束时**没有任何能跑能测的东西**，接口靠想象，到阶段 3 才发现阶段 1 设计错了。

**正例（纵切，推荐）：**

```
阶段 1：用户认证子系统（DB + API + UI 全打通，端到端可登录）
阶段 2：订单子系统（DB + API + UI 全打通，可下单）
阶段 3：报表子系统
```

每个阶段结束 = 一个独立可跑可测可演示的功能切片。

**配套技巧：第一个子系统做 Walking Skeleton**——把 CI、打包、部署、端到端"hello world"全打通，后续子系统都受益。

---

## 6. 滚动展开 plan，不预先全写

**为什么不预先写完所有 plan：**

- writing-plans 强制 No Placeholders，但你不可能在没写代码前知道 Task 17 的精确文件路径和行号
- 早期 plan 里"假设的接口"会被现实推翻；越往后写越偏离真实
- plan 是滚动的、可丢弃的中间产物，不是合同

**正确节奏：**

```
brainstorm 一次 → 产出 overview + 01~03 spec
→ 选定 01，writing-plans 写完整 plan
→ subagent 执行 + review，全绿后 merge
→ 此时再回头看 02 spec，根据 01 实际跑出来的现实，写 02 的 plan
→ 循环
```

**判断标准**：上一个子系统真实合并后，回看时是否需要修改下一个 spec？通常需要。这就是为什么 plan 不能预先写。

---

## 7. Subagent 驱动执行的关键技巧

### 7.1 主 / 副 agent 分工

- **主 agent**：拿着 plan，做 review，不直接写代码
- **Subagent**：每个 task 一个 fresh subagent，干净 context，只看本 task 需要的文件

好处：主 agent 上下文不被代码细节污染，能专注 review 质量。

### 7.2 两阶段 review

subagent 交付后，主 agent 做：
1. **Spec 符合度评审**：这个实现是否完成了 plan 中规定的事
2. **代码质量评审**：单一职责、命名、测试覆盖、文件大小

任一不过 = 退回 subagent 修改。

### 7.3 防止"摘要型 context 污染"（sankalp 的关键洞察）

> "It's important that the model goes through each file itself so all ingested context can attend to each other."
> ——subagent 返回的摘要是有损的，主 agent 看完摘要会"以为自己读过文件"，但其实没读

**对策**：主 agent 在关键节点（比如准备 merge 前）**自己重读核心文件**，不要只信 subagent 的报告。这是 1M context 也救不了的问题。

### 7.4 并发限制

- Claude Code 单会话 **最多 10 个 subagent 并发**
- 超过会排队，大项目要注意 task 切分粒度

### 7.5 跨模型评审

sankalp 的做法：**用 Codex / GPT-5 做代码评审**（不用 Claude 自审）。跨模型抓 bug 比同模型自审更准。

当前环境已装 codex 插件，可直接：
- 用 `codex:rescue` 委托独立评审
- 关键合并前用 `/code-review:code-review` + `codex:rescue` 双跑

---

## 8. Context 管理（重度用户的硬性纪律）

| 信号 | 动作 |
|---|---|
| 用 `/context` 频繁查 | 是 |
| 到 60% | `/compact` 或开新会话 |
| 长会话超过 4 小时 | 强制 `/compact`，无论用量 |
| 跨子系统切换 | 开新会话，旧 plan + 新 spec 重新加载 |
| CLAUDE.md | 持续维护，写"不要做什么"比"要做什么"更有用 |
| 单个 skill 文件 | < 500 行，超了拆分 |

**为什么 60% 就 compact**：即使 Opus 1M，超过 60% 时输出质量明显衰减。重度用户的实测共识。

---

## 9. 4 个 Approval Gate 的具体用法

不要把 gate 当形式，每个 gate 应该有具体输出物 + 具体决策。

### Gate 1：Brainstorm 结束，approve design 方向

- 输出物：3 段以内的方案对比 + 推荐方向 + 风险点
- 你的决策：选方案 / 退回再想 / 拆子系统

### Gate 2：Spec approve

- 输出物：单个子系统的 What / Why / 接口 / 验收标准
- 你的决策：spec 是否清晰可执行
- **修改成本最低的 gate**，多花时间在这里

### Gate 3：Plan approve

- 输出物：完整 task 列表 + 文件路径 + 测试代码
- 你的决策：**手动改 plan**（st0012 重度强调这一步）
- 改什么：task 顺序、漏掉的 edge case、过度抽象、文件切分

### Gate 4：Execution 完成 approve

- 输出物：所有 task checkbox 完成 + 两阶段 review 通过 + 测试全绿
- 你的决策：merge / 退回 fix / 拆分 commit

---

## 10. 真实踩坑清单（多源交叉验证）

| 坑 | 解法 |
|---|---|
| 10 个 subagent 并发上限 | task 切分别太碎；批量任务用 task list 串行 |
| Context bleeding（subagent 摘要污染主 agent） | 关键节点主 agent 自己重读文件 |
| plan 文件名随机冲突 | 严格 `YYYY-MM-DD-<feature>.md` 命名 |
| 一个会话 >1 个 plan 会乱 | 每个子系统开新会话 |
| subagent 自动调用不可靠 | 显式 invoke skill，别指望自动 |
| plan 反馈循环僵硬（小改触发全新多页 plan） | VSCode 插件支持高亮加注释；或手动编辑 md 文件 |
| Claude 用 Superpowers 错更多（HN 部分反馈） | 资深开发熟悉的代码库可降流程；陌生代码库严流程 |
| 长会话漂移 | 60% 强制 compact，4 小时强制新会话 |

---

## 11. 两种风格选择（HN 上的真实分歧）

| 风格 | 适用 | 代表 |
|---|---|---|
| **严流程派** | 陌生代码库 / 大项目 / 多人协作 | Superpowers 默认、Evan Schwartz |
| **轻流程派** | 熟悉代码库 / 个人项目 / 资深开发 | sankalp（很少用 Plan Mode，自己探索 + Opus 对话 + Codex 评审） |

**判断标准不是项目大小，而是你对代码库的熟悉度。**

陌生代码库即使小项目也建议严流程；熟悉代码库即使大项目也可以轻流程 + 精准 prompt。

---

## 12. 落地 Checklist（每次开始大项目时对照）

开工前：
- [ ] 顶层 brainstorm 完成，overview.md 已写
- [ ] 子系统切分清单已定，每个子系统**纵切、独立可跑**
- [ ] 选定第一个子系统（建议是 Walking Skeleton 性质）
- [ ] worktree 已建好，CI 跑过 baseline 测试

每个子系统循环：
- [ ] 该子系统 spec 写完，approve
- [ ] writing-plans 写完 plan，先定义文件结构再切 task
- [ ] No Placeholders 自查通过
- [ ] **手工改一遍 plan**（5-10 分钟，ROI 最高）
- [ ] subagent 逐 task 执行
- [ ] 每 task 两阶段 review 通过
- [ ] 关键节点跨模型评审（Codex / GPT-5）
- [ ] 主 agent 重读核心文件，防摘要污染
- [ ] 全部测试绿，merge

会话纪律：
- [ ] `/context` 到 60% 立即 `/compact`
- [ ] 跨子系统切换开新会话
- [ ] CLAUDE.md 持续更新"不要做什么"

---

## 13. 参考资料

官方：
- [obra/superpowers GitHub](https://github.com/obra/superpowers)
- [writing-plans SKILL.md 源码](https://github.com/obra/superpowers/blob/main/skills/writing-plans/SKILL.md)
- [Superpowers (Anthropic 官方页)](https://claude.com/plugins/superpowers)

作者博客（Jesse Vincent）：
- [Superpowers: How I'm using coding agents in October 2025](https://blog.fsck.com/2025/10/09/superpowers/)
- [Superpowers 5 (2026-03)](https://blog.fsck.com/2026/03/09/superpowers-5/)

重度用户实战：
- [A Rave Review of Superpowers — Evan Schwartz](https://emschwartz.me/a-rave-review-of-superpowers-for-claude-code/)
- [A Claude Code workflow with the superpowers plugin — st0012](https://st0012.dev/links/2026-01-15-a-claude-code-workflow-with-the-superpowers-plugin/)
- [My experience with Claude Code 2.0 — sankalp](https://sankalp.bearblog.dev/my-experience-with-claude-code-20-and-how-to-get-better-at-using-coding-agents/)

讨论：
- [HN 讨论：A Rave Review of Superpowers](https://news.ycombinator.com/item?id=47623101)

相关方法论：
- [Spec-Driven Development with Claude Code in Action — alexop.dev](https://alexop.dev/posts/spec-driven-development-claude-code-in-action/)
- [The Superpowers Plugin — Builder.io](https://www.builder.io/blog/claude-code-superpowers-plugin)
- [GSD Framework](https://ccforeveryone.com/gsd)

---

## 14. 一句话记忆口诀

> **总设计先行，子系统纵切，spec 可全写，plan 不预写，task 不留白，subagent 干净跑，主 agent 不偷懒。**
