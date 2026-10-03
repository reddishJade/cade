# 执行模式：Plan、Build、Act

Cade 设计了三种执行模式（Execution Modes），用来在**全自动开发的高效性**与**系统修改的安全性**之间取得平衡。

空闲时按 Shift+Tab，按 act → build → plan 循环切换；也可输入 `/mode` 打开模式列表。

---

## 1. 三种模式特性对比

| 模式 | 核心定位 | 工具可见性 | 写入与执行权限 | 适用场景 |
| :--- | :--- | :--- | :--- | :--- |
| **`Plan`** | 调研与规划 | 代码探索核心为 `read + bash`，另有 Web、问答、历史/记忆能力 | 已确认只读 Shell 直接执行；已确认 mutation 拒绝；未知副作用交自动 Reviewer；结构化写入仅限计划文件 | 分析需求、排查 Bug 原因、制定重构设计方案 |
| **`Build`** | 自动实施与验证 | 日常 coding surface：`read / write / edit / patch / bash` | 结构化项目写入与已确认只读 Shell 直接执行；未知/有副作用 Shell 交自动 Reviewer | 需求明确，让 Agent 自主实现、测试与验证 |
| **`Act`** (默认) | 人工把关副作用 | 与 Build 相同的日常 coding surface | 只读与已确认只读 Shell 直接执行；结构化写入和未知/有副作用 Shell 交用户审批 | 关键代码修改、希望人工确认副作用时 |

---

## 2. 深入理解各模式

### 2.1 Plan 模式：只动口不动手

输入 `/mode plan` 进入规划模式，再单独提交问题：

```text
> /mode plan
> 分析当前数据库连接池在高并发下连接泄露的原因并给出修复步骤
```

- **安全边界**：Plan 保留 Bash 探索能力；确定只读命令直接运行，analyzer 确认的 mutation 由 mode policy 拒绝，无法静态确认副作用的命令交独立 Reviewer。
- **产物沉淀**：Cade 会将最终的规划方案整理为 Markdown 文件，写入项目根目录下的 `.cade/plans/` 中保存。
- **自动防死循环（Timeout）**：Plan 模式内置轮数上限（默认 **8 轮**）；达到上限后会自动切换到 Build，并发出模式通知。

### 2.2 Build 模式：全自动高效推进

当你已有了明确的方案，或者不想每改一个文件都手动点击“确认”时，输入 `/mode build`：

```text
> /mode build
```

- **自动文件修改**：修改、新建文件不再打扰用户，Agent 自行闭环完成。
- **Shell 审查机制（Reviewer）**：常见只读 `rg`/`fd` 搜索与只读 Git 命令直接运行；`fd` 的未知或可执行选项，以及静态确认的 mutation 和未知副作用命令进入独立 Reviewer。

### 2.3 Act 模式：人工安全锁

Act 模式是 Cade 启动时的**默认模式**。

- **透明可控**：`read` 和可证明只读的 Bash 直接运行；`write/edit/patch` 以及未知或有副作用的 Bash 会进入用户审批。
- **确认选项**：你可以输入 `y` 确认执行、`n` 拒绝本次操作，或者直接输入意见让 Agent 调整思路后再试。

---

## 3. 模式切换操作

在 REPL 中可以随时无缝切换：

```bash
/mode           # 打开 act/build/plan 模式列表
/mode plan      # 切换至 Plan 模式
/mode build     # 切换至 Build 模式
/mode act       # 切换回 Act 模式
```
切换记录会持久化在当前会话状态中，即使退出重进也会恢复之前的模式设置。
