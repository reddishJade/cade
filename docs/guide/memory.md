# 长期记忆（Memory）系统

Cade 的长期记忆是**两个 Markdown 文件**：项目根 `MEMORY.md` 与 `~/.cade/memory/MEMORY.md`。
文件本身是唯一事实源，可以用编辑器或 `/memory` 命令直接查看和修改。

记忆永远低于当前仓库的事实权威：

```text
repository / files / git / tests  >  MEMORY.md
```

记忆是"过去在这里踩过这个坑"的历史提示，不是"当前代码一定还是这样"的断言。

---

## 1. 两层作用域

| 层级 | 作用范围 | 存储位置 | 适用内容 |
| :--- | :--- | :--- | :--- |
| **Project 记忆** | 当前仓库 | `<项目根>/MEMORY.md` | 项目规则、架构决策、已验证事实、可复用经验 |
| **User 记忆** | 跨项目 | `~/.cade/memory/MEMORY.md` | 个人偏好与跨项目习惯 |

只有这两层，没有单独的"模块记忆"或"路径记忆"：路径、符号与错误签名以 `anchors` 的形式写在记录里。

当前任务进度**不属于**记忆，它写在项目根 `NOTE.md`（Working Note）里；会话细节无损保存在 Session JSONL 中。

---

## 2. 两类记录

### 2.1 规则与决策（普通 H2 记录）

```markdown
## 新接口必须做入参校验
所有新增 API 接口必须通过 pydantic 做入参强校验。
```

### 2.2 经验（Experience）

保存"昂贵获得、未来可能复用"的排障经验：

```markdown
## fd 单文件发现路径不匹配
type: experience
root_cause: 目录导向的发现假设了遍历语义，单文件路径被当成根目录处理
fix: 进入目录遍历前先判定显式文件输入，走单文件分支
applies_when: fd 后端 + 显式单文件路径（目录输入不受影响）
anchors: src/cade/coding_agent/tools/fd.py, dir=src/cade/coding_agent/tools, err=NotADirectoryError
evidence: commit=1a2b3c4; session=9f2c1a7b; test=uv run pytest -q
```

- H2 标题就是问题陈述，不重复写 `problem` 字段。
- `anchors`：裸路径=文件，`dir=`=目录，`sym=`=符号，`err=`=原样错误签名
  （要足够特异，例如 `err=NotADirectoryError`；过泛的签名会频繁命中提示）。
- `evidence`：`;` 分隔的 `key=value` 指针（commit / session / test / message_id）。
  `commit` 与 `session` 由 `remember` 工具自动盖章，模型不编造。
- 必填：`root_cause`、`fix`、`applies_when`、至少一个文件或目录锚点、至少一个证据指针。
  缺任何一项都会被写入路径拒绝（`remember`、`/memory add`、`/memory update` 一致）。
- 不要写 `confidence`、`status`、`validity`、`utility` 或各类计数：这些旧治理字段会被拒绝。

---

## 3. 记忆如何进入上下文

1. **默认不注入内容**：普通回合只收到一段记忆使用协议（文件位置、如何 `recall`、何时 `remember`）。
2. **显式召回**：Agent 通过 `recall` 检索：
   - `query`：BM25 + token 重叠 + 精确子串加成；项目层优先；
   - `anchor`：仓库相对路径，只返回声明了该锚点的经验（确定性过滤，不参与打分）；
   - 结果附带 `state=unchanged|changed|unknown`，由 `git diff` 现算，不落盘。
     `changed` 只表示锚点与该 commit 不一致（可能正是这条经验描述的修复本身，也可能之后又被改过），
     含义是"用前核对"，不是"作废"。
3. **稀疏提示**：只有确定性触发条件命中时才注入一个 `LOW` 优先级提示块，最多 3 行指针：
   - 本 session 真实读写过的文件命中了某条经验的 file/dir 锚点；或
   - 某条经验的 `err=` 签名在最近的失败工具输出或用户消息里原样出现。
   提示块只给标题、适用条件、锚点、证据与 state；正文必须显式 `recall`。预算紧张时它先被丢弃。
4. **恢复会话**：规则与决策按原有方式恢复；经验只恢复指针行，避免历史经验吃光恢复预算。

---

## 4. 记录新经验

Agent 在修复代价较高的 bug 之后调用 `remember`（与验证步骤放在同一条 assistant 消息里，不需要额外一轮推理）：

```json
{
  "title": "fd 单文件发现路径不匹配",
  "root_cause": "目录导向的发现假设了遍历语义",
  "fix": "进入目录遍历前先判定显式文件输入",
  "applies_when": "fd 后端 + 显式单文件路径",
  "anchors": ["src/cade/coding_agent/tools/fd.py", "err=NotADirectoryError"],
  "evidence": ["test=uv run pytest -q"]
}
```

权限与写文件同构：Plan 模式不暴露 `remember`，Build 直接允许，Act 需要审批。

---

## 5. 记忆管理命令

```bash
/memory list                    # 查看当前项目与用户层记录
/memory add project <标题> | <内容>
/memory update project <标题> | <内容>
/memory delete project <标题>
/memory search <关键词>
```

被拒绝时会给出具体原因（例如缺少 `root_cause`、锚点路径不存在、使用了已退休的治理字段）。

---

## 6. 三条硬约束

- **bad memory < no memory**：未通过校验的记录不会被召回，也不会产生提示。
- **No hidden inference**：Memory 路径上没有任何模型调用（无 judge、无 embedding、无摘要、无后台整理）。
- **无 per-record 状态**：不存置信度、效用、计数或生命周期；新鲜度每次读取时用 git 现算。
