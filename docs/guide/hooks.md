# Lifecycle hooks

Use lifecycle hooks to run shell scripts or monitoring programs before and after tools, before model requests, or during context rollover. Hooks can send alerts, format code, or submit audit records.

## Events

| Event | Trigger | Example use |
| --- | --- | --- |
| `before_agent_start` | Before a session starts or a new user instruction runs | Check prerequisites or fetch Git changes |
| `before_provider_request` | After prompt assembly, before the model API request | Record estimated token usage or local logs |
| `pre_tool` | Before tool execution | Record security audit events or create a backup |
| `post_tool` | After tool execution returns output | Run `ruff format` or a syntax check |
| `on_context_window_reset` | When the context rolls over at its trigger threshold | Back up `NOTE.md` or report task progress |
| `on_error` | On an unexpected exception or fatal network error | Send an alert or save diagnostic information |

## Configure hooks

Add a `hooks` block to the project configuration:

```json
{
  "hooks": {
    "entries": [
      {
        "event": "post_tool",
        "matcher": "edit",
        "command": ["ruff", "format", "--quiet", "src/"],
        "failure_policy": "warn"
      },
      {
        "event": "on_context_window_reset",
        "command": ["python", "scripts/report_rollover.py"],
        "failure_policy": "ignore"
      }
    ]
  }
}
```

`command` is a nonempty argv array. Hooks run directly, not through a shell. Each process receives a redacted JSON object on stdin with `event`, `tool`, `input`, `output`, `error`, `metadata`, a timestamp, and correlation IDs. Stdout must be empty or a JSON object. Send diagnostic text to stderr.

`pre_tool` runs synchronously. It can return `decision` (`allow`, `ask`, or `deny`) and an `arguments` object; decisions can only tighten permissions. The other configured events run through a background notification queue.

`failure_policy` controls how the runner handles failures:

- `ignore`: ignore the error and continue.
- `warn`: log a warning and continue.
- `fail`: raise an error to the caller. For synchronous `pre_tool` hooks, this stops the tool attempt. For background notifications, the queue logs the exception without propagating it to the active tool or instruction.

Background notifications cannot enforce synchronous constraints or guarantee that formatting finishes before the next tool runs. Use `pre_tool` for constraints that must run before tool execution.

## Inspect hooks

Enter `/hooks` to list the active lifecycle hooks for the workspace.
