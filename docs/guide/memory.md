# 长期记忆（Memory）系统

Cade 的长期记忆是**两个 Markdown 文件**：项目根 `MEMORY.md` 与 `~/.cade/memory/MEMORY.md`。
文件本身是唯一事实源，可以用编辑器或 `/memory` 命令直接查看和修改。

记忆永远低于当前仓库的事实权威：

```text
repository / files / tests / git  >  MEMORY.md
```

记忆是"过去在这里踩过这个坑"的历史提示，不是"当前代码一定还是这样"的断言。

---

## 1. 两层作用域

| 层级 | 作用范围 | 存储位置 | 适用内容 |
| :--- | :--- | :--- | :--- |
| **Project 记忆** | 当前仓库 | `<项目根>/MEMORY.md` | 项目规则、架构决策、已验证事实、可复用经验 |
| **User 记忆** | 跨项目 | `~/.cade/memory/MEMORY.md` | 个人偏好与跨项目习惯 |

只有这两层，没有单独的"模块记忆"或"路径记忆"：路径、符号与错误签名以 `anchors` 的形式写在记录里。
**经验（Experience）只能写在项目层**——仓库相对锚点在别的项目里会指向完全不同的文件。

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
evidence: validation=ab12cd34ef56; verify=uv run pytest -q; exit_code=0; session=9f2c1a7b; anchor_state=sha256:1f0c…; commit=1a2b3c4
```

- H2 标题就是问题陈述，不重复写 `problem` 字段。
- `anchors`：裸路径=文件，`dir=`=目录，`sym=`=符号，`err=`=原样错误签名。
  **任意一种锚点都可以**：文件/目录锚点额外支持路径提示与内容快照，`err=` 支持错误提示，`sym=` 只支持显式召回。
- 必填：`root_cause`、`fix`、`applies_when`、至少一个锚点、至少一个证据指针。
- 不要写 `confidence`、`status`、`validity`、`utility` 或各类计数：这些旧治理字段会被拒绝。

### 2.3 证据由 Host 盖章，不由模型填写

`remember` 不接受模型自述的测试命令，它只接受这一次真实发生的成功验证事件：

| 指针 | 含义 |
| :--- | :--- |
| `validation` | 该验证事件在 session 账本里的 entry id |
| `verify` | 该事件实际执行的命令 |
| `exit_code` | 该事件观察到的退出码 |
| `session` | 产生它的 session |
| `anchor_state` | 写入时锚点文件内容的 sha256 快照 |
| `commit` | 写入时的 HEAD，仅作附加线索 |

如果**最近一次**显式验证没有成功（失败或没有退出码），`remember` 会直接拒绝写入，
也不会回退到更早的成功——否则等于在回归之后声称"刚刚验证过"。
这保证的是"证据是真的、可回溯的"，不是"根因一定正确"——后者需要靠 `history` 回看原始事件自行判断。

因此证据分两档，索引行会直接标出来：

- `evidence=event`：带 `session=` 与 `validation=` 指针；
- `evidence=claim`：人工或旧版本写入，没有已记录事件支撑。

回溯原始事件：

```text
history read session=9f2c1a7b message_id=ab12cd34ef56
```

---

## 3. 记忆如何进入上下文

1. **默认不注入内容**：普通回合只收到一段记忆使用协议（文件位置、如何 `recall`、何时 `remember`）。
2. **两段式检索**（与 `history` 同构）：
   - `recall` 返回索引行：`memory_id`、问题、适用条件、锚点、证据档位、新鲜度；
   - `recall memory_id=mem_xxx` 才返回某一条的正文。
   索引存在的意义是：读三条完整经验应当是三次刻意读取，而不是一次灌满上下文。
3. **稀疏提示**：只有确定性触发条件命中时才注入一个 `LOW` 优先级提示块，最多 3 行指针：
   - 本 session 真实读写过的文件命中了某条经验的 file/dir 锚点；或
   - 某条经验的 `err=` 签名在最近的失败工具输出或用户消息里原样出现。
   每条经验每个上下文窗口最多自动提示一次；显式 `recall` 过之后，本窗口内不再自动提示。
   预算紧张时提示块先被丢弃。
4. **恢复会话**：规则与决策按原有方式恢复；经验只恢复指针行，避免历史经验吃光恢复预算。

### 新鲜度只看内容快照

| state | 含义 |
| :--- | :--- |
| `unchanged` | 锚点文件内容与写入时一致 |
| `changed` | 锚点内容变了，用前重新核对 |
| `missing` | 锚点文件已不存在 |
| `unknown` | 没有快照（claim 档，或只有目录/符号/错误锚点） |

它不看 git commit：真实工作流是"改完 → 测试通过 → 记录"，此时修复往往还没提交，
`git diff HEAD` 会把刚写下的经验误判成 `changed`，而未跟踪的新文件又会被误判成"没变化"。

### 坏记录不会被当成正文消费

MEMORY.md 可以手工编辑，所以校验在**读取期**同样生效：声明了 `type: experience`
但不满足约定的记录不会产生提示、不会在 `recall` 里返回正文、也不会在恢复会话时被注入，
只会以 `INVALID experience: <原因>` 的索引行出现，等你去修。

---

## 4. 记录新经验

Agent 在验证命令成功之后调用 `remember`（它同时盖章验证事件、锚点快照与 HEAD）：

```json
{
  "title": "fd 单文件发现路径不匹配",
  "root_cause": "目录导向的发现假设了遍历语义",
  "fix": "进入目录遍历前先判定显式文件输入",
  "applies_when": "fd 后端 + 显式单文件路径",
  "anchors": ["src/cade/coding_agent/tools/fd.py"]
}
```

权限与写文件同构：Plan 模式不暴露 `remember`，Build 直接允许，Act 需要审批。
成本是普通工具循环里的一次往返（与任何"看到结果再行动"的调用相同），没有任何额外模型调用。

---

## 5. 记忆管理命令

```bash
/memory list                    # 查看当前项目与用户层记录
/memory add project <标题> | <内容>
/memory update project <标题> | <内容>
/memory delete project <标题>
/memory search <关键词>
```

被拒绝时会给出具体原因（例如缺少 `root_cause`、锚点路径不存在、经验写到用户层、使用了已退休的治理字段）。

---

## 6. 三条硬约束

- **bad memory < no memory**：未通过校验的记录不会被当成正文消费，也不会产生提示。
- **No hidden inference**：Memory 路径上没有任何模型调用（无 judge、无 embedding、无摘要、无后台整理）。
- **无 per-record 状态**：不存置信度、效用、计数或生命周期；新鲜度用内容快照现算，提示去重只活在当前窗口的运行时状态里。
