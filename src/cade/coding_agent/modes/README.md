# Product runtime modes

`cade` and `cade tui` use [tui/](tui/README.md). `cade exec` uses [exec_mode.py](exec_mode.py) for single tasks, text or NDJSON events, automation approval, and stable exit codes.

Both use the same coding product and Harness. Shared application services live in [interaction/](../interaction/README.md), and CLI handlers live in [cli/](../cli/README.md). [server/](../../server/) provides browser HTTP, WebSocket, and event presentation.

The TUI owns prompt-toolkit, Rich, layout, keys, folding, approval forms, and configuration wizards. Exec consumes runtime events directly and owns its automation I/O protocol.
