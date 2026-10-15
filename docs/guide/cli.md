# CLI and terminal interaction

Use the `cade` CLI to start the terminal interface or run a noninteractive task.

## Startup options

```text
Usage: cade [OPTIONS] [COMMAND]
```

### Common global options

| Option | Type | Description |
| --- | --- | --- |
| `--project-root <path>` | path | Workspace root; defaults to the current directory |
| `--config <path>` | path | Alternative project configuration file |
| `--sessions-dir <path>` | path | Session directory override for this invocation |
| `-c, --continue` | flag | Resume the current project's most recent session |
| `--session <id>` | string | Resume a specific session |
| `--resume` | flag | Open the TUI session picker |
| `-h, --help` | flag | Show CLI help |

The three shared path options can appear before or after a subcommand. For `session` queries, they can also follow the action. If repeated, the last explicit value wins. The session directory uses the CLI override first, then `paths.sessions_dir`, and finally the workspace's `.cade/sessions/` directory.

### Common invocations

```bash
# 启动默认交互界面
cade

# 在指定工作区启动 TUI
cade tui --project-root ./repo

# 在 CI 或脚本中执行单次任务
cade exec "Run pytest and fix all failing tests"
```

The `exec` subcommand can override the model, mode, approval policy, and run limits. See `cade exec --help`.

## Terminal interaction

### Shortcuts

- **Multiline input**: press Shift+Enter. If your terminal does not map that shortcut, press Esc followed by Enter as a fallback.
- **Completion**: use Tab to complete slash commands after `/` and tool names after `/tool `. Enter `@` for file completion.
- **Interrupt and exit**: press Ctrl+C to cancel the current model stream or long-running command. Press it twice while idle with an empty input to exit.

### File references

Enter `@` in a prompt to find and reference workspace files:

```text
> Use argument handling in @src/cade/main.py as a reference. Add the corresponding options to @src/cade/coding_agent/interaction/commands.py.
```

Cade includes the referenced file content as model context when it sends the request.

### Shell commands

Prefix a line with `!` to run a local command without leaving the conversation:

```text
> !git status
> !pytest src/tests/test_harness.py
```

Command output appears in the terminal.
