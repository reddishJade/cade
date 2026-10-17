# Terminal workspace

The prompt-toolkit workspace uses an inline transcript with `full_screen=False`. Conversation, progress, and input share a continuous layout with embedded pickers and approval forms.

```text
Wordmark and version / discovered resources / history / streaming output
Completion menu or interactive picker
Input with background and vertical padding
Contextual hint during execution, completion, or approval
Project and branch                         Context and usage
Execution mode                             Model and effort
```

## Files

| File | Responsibility |
| --- | --- |
| [app.py](app.py) | Lifecycle, layout, keys, background runs, recovery |
| [state.py](state.py) | Messages, stream state, folding, visible output |
| [rendering.py](rendering.py) | Markdown and message formatting |
| [inline_renderer.py](inline_renderer.py) | Incremental main-screen rendering, terminal history, reconstruction |
| [widgets.py](widgets.py) / [completion.py](completion.py) | Input highlighting and completion |
| [commands.py](commands.py) / [command_output.py](command_output.py) | Host commands and shared command output |
| [settings.py](settings.py) / [config_registry.py](config_registry.py) / [setup_wizard.py](setup_wizard.py) | Model, permission, and configuration interaction |
| [approval.py](approval.py) / [sessions.py](sessions.py) | Approval previews and startup session selection |
| [thinking.py](thinking.py) / [tool_summary.py](tool_summary.py) | Reasoning timing, terminal previews, tool summaries |
| [chrome.py](chrome.py) | Welcome content, status layout, colors |

## Interaction contracts

- Idle Enter submits. Busy Enter queues by default. Alt+Enter steers; Ctrl+J inserts a new line.
- Welcome content and short conversations use their actual height. Long conversations are written to main-screen terminal history; prompt-toolkit paints only the visible tail. Resources come from actual tools, collectors, and skill catalogs. Short terminals use a compact welcome area.
- The terminal owns history scrolling through its wheel and page shortcuts, commonly Shift+PageUp/PageDown. The application does not capture PageUp/PageDown/End for history and does not use the alternate screen or mouse capture.
- Following the Pi main-screen update strategy, ordinary updates repaint the visible area and append overflow to history. Folding, session changes, or resizing that alter off-screen content clear the screen and scrollback, then replay the document. This also clears terminal history from before Cade started.
- Input has a full background and starts at three lines. Idle layout shows two status lines and welcome key hints.
- Input hints reflect execution, completion, and approval state. Completion Enter accepts a candidate without submitting it.
- Busy input enters the persistent session inbox through shared run control and is consumed at the relevant model or task boundary.
- Pickers, completion, and status participate in height calculation. Narrow terminals trim by display width.
- Reasoning and tool details start collapsed. Errors and permission notices remain visible.
- Recovery loads shared session state and displays a notice. `cade -c` can start a task if no history exists.
- Exit preserves the conversation, clears input and status, and displays the wordmark, duration, session ID, and resume command. Nondefault session directories and explicit configuration paths are retained.

See the [quickstart](../../../../../docs/guide/quickstart.md) for usage. Shared operations live in [interaction/](../../interaction/README.md); TUI owns only terminal input and presentation.
