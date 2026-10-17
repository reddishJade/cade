# Workflows and examples

Use these examples for common software development tasks.

## Single-task execution

For a code question, directory analysis, or focused operation:

```bash
# 查询代码实现
cade exec "Explain the agent loop in src/cade/agent/agent_loop.py"

# 定位未使用的 import
cade exec "Check the project for unused imports"
```

## Terminal workspace

Cade starts the TUI by default:

```bash
# 启动默认 TUI
cade

# 显式启动 TUI
cade tui
```

- Reference files with `@`, such as `@src/cade/main.py`, to include their content as context.
- Run local shell commands with `!`, such as `!git status`, without a model call.
- Activate a skill with `$`, such as `$refactor Extract a shared base class`.

## Plan, review, and implement

For a larger feature or refactor, investigate in Plan mode, review the plan, then implement in Build mode.

### Plan the change

In the TUI, enter:

```text
/mode plan
Analyze the current permission logic and plan an extension for role-based access control (RBAC).
```

The agent explores code with `read` and shell searches and can write `.cade/plans/rbac_design.md` with affected files, data structures, and tests. Plan mode restricts project code changes and shell mutations; see [Execution modes](guide/modes.md).

### Review and implement

After reviewing the Markdown plan, enter:

```text
/mode build
Implement .cade/plans/rbac_design.md and run the tests.
```

The agent applies changes with `edit` and `write` and runs tests through `bash`. An independent reviewer assesses shell calls requiring review. Sensitive or out-of-workspace operations remain subject to permissions and approval.

## Branch and undo

### Fork a session

To explore another approach from an earlier input:

```text
/fork 3
```

Cade retains history before the third user input, creates a new session, and returns that input to the editor. The parent history remains unchanged. See [Sessions](guide/sessions.md).

### Undo file changes

```text
/undo --list            # 查看可撤销的文件快照
/undo                   # 撤销最近一个可撤销用户轮次的文件修改
```

Cade restores snapshot changes after conflict and permission checks. Restoration can complete partially when files conflict.

## Connect MCP tools

Create `.cade/mcp_config.json` in the project root to connect local stdio servers, such as databases or web fetch tools:

```json
{
  "mcpServers": {
    "sqlite": {
      "command": "uvx",
      "args": ["mcp-server-sqlite", "--db-path", "./data.db"]
    },
    "fetch": {
      "command": "uvx",
      "args": ["mcp-server-fetch"]
    }
  }
}
```

After startup, the agent discovers and registers tools such as:

```text
mcp__sqlite__read_query
mcp__sqlite__describe_table
mcp__fetch__fetch
```

Ask, for example, "Read the ten most recent user records from SQLite." The agent can call the MCP tool and summarize the results.

## Memory

Each Markdown file in `.cade/memory/` stores hard-earned project knowledge that might help later tasks. `/memory` shows the directory; users can inspect, edit, and delete files with an editor.

A bounded dynamic catalog exposes paths and opening hints on requests. For details, the agent searches with tools such as `rg "KEYWORD" .cade/memory/`, reads the body, follows History references, and checks current code.

`save_memory` accepts free-form content with explicit real session/entry sources. See [Memory](guide/memory.md) for parameters and updates.

## External hooks

Configure host commands to validate or report key actions in `cade.config.json`:

```json
{
  "hooks": {
    "entries": [
      {
        "event": "pre_tool",
        "matcher": "bash",
        "command": ["python", "scripts/pre_bash_check.py"],
        "timeout": 5,
        "failure_policy": "fail"
      },
      {
        "event": "post_tool",
        "matcher": "edit",
        "command": ["uv", "run", "ruff", "check", "--fix"],
        "timeout": 10,
        "failure_policy": "warn"
      }
    ]
  }
}
```

- `pre_tool`: run a custom check before `bash`, returning constraints such as `allow`, `deny`, or `ask`.
- `post_tool`: run lint fixes after file edits.
