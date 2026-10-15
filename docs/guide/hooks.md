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
        "command": ["ruff", "format", "src/"],
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

`command` is a nonempty argv array. Hooks run directly, not through a shell. `failure_policy` controls failures:

- `ignore`: ignore the error and continue.
- `warn`: print a terminal warning and continue.
- `fail`: treat the failure as an error and stop the current tool or instruction.

## Inspect hooks

Enter `/hooks` to list the active lifecycle hooks for the workspace.
