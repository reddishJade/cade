# Execution modes: Plan, Build, and Act

Cade provides three execution modes with different rules for automatic changes and approval.

When idle, press Shift+Tab to cycle through act → build → plan, or enter `/mode` to open the mode list.

## Mode comparison

| Mode | Purpose | Available tools | Write and execution permissions | Typical use |
| --- | --- | --- | --- | --- |
| Plan | Investigation and planning | Primarily `read` and `bash`, plus web, questions, history, and memory | Confirmed read-only shell commands run directly; confirmed mutations are denied; unknown effects go to the automatic reviewer; structured writes are limited to plan files | Analyze requirements, investigate bugs, or plan a refactor |
| Build | Implementation and verification | The normal coding tools: `read`, `write`, `edit`, and `bash` | Structured project writes and confirmed read-only shell commands run directly; unknown or mutating shell commands go to the automatic reviewer | Implement and test clear requirements |
| Act (default) | Human review of side effects | The same coding tools as Build | Reads and confirmed read-only shell commands run directly; structured writes and unknown or mutating shell commands require user approval | Review side effects before changing critical code |

## Plan mode

Enter `/mode plan`, then submit your question separately:

```text
> /mode plan
> Investigate connection leaks in the database pool under high concurrency and propose repair steps.
```

Plan mode retains shell exploration. Confirmed read-only commands run directly. The mode policy denies analyzer-confirmed mutations. Commands whose effects cannot be determined statically go to an independent reviewer.

Cade saves the resulting Markdown plan under `.cade/plans/` in the project root. Plan mode remains active until you switch modes explicitly. Completing a plan or reaching any number of model steps never switches to Build or Act automatically.

## Build mode

When you have a clear plan or do not need to approve every file change, enter:

```text
> /mode build
```

The agent can create and edit project files without individual approval. Common read-only `rg`, `fd`, and Git commands run directly. Unknown or executable `fd` options, confirmed mutations, and commands with unknown side effects go to the independent reviewer.

## Act mode

Act is the default startup mode. Reads and provably read-only shell commands run directly. File writes, edits, and shell commands with unknown or known side effects require user approval.

Enter `y` to approve, `n` to deny, or feedback for the agent to revise its approach.

## Switch modes

```bash
/mode           # 打开 act/build/plan 模式列表
/mode plan      # 切换到 Plan 模式
/mode build     # 切换到 Build 模式
/mode act       # 切换回 Act 模式
```

The current mode is persisted with the session and restored when you resume it.
