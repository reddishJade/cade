# CLI command handlers

This directory handles configuration, authentication, and session subcommands. `src/cade/main.py` parses arguments, selects runtime modes, and manages entry-point lifecycles.

- [auth_cmd.py](auth_cmd.py): authentication commands with TUI selection and API key prompts.
- [config_cmd.py](config_cmd.py): configuration commands using the terminal settings browser.
- [session_cmd.py](session_cmd.py): persistent session queries, export, and control.
- [session_control.py](session_control.py): cross-process run state and interruption for exec and session commands.

See [modes/](../modes/README.md) for hosts, [interaction/](../interaction/README.md) for shared operations, and [server/](../../server/) for the browser host.
