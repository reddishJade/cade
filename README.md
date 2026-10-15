# Cade

Cade is a terminal coding agent that works in your project directory. Submit tasks, review progress, approve operations, and resume work after you exit.
For runtime behavior, state ownership, and code entry points, see the [architecture](docs/architecture.md).

## Install and start

You need Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv tool install --python 3.12 git+https://github.com/reddishJade/cade.git
cd /path/to/your/project
cade
```

This command follows `main` rather than a pinned version. After the first release, the installation instructions will use a release tag. See [Versions and releases](docs/guide/releases.md) for the release policy.

On first startup, Cade guides you through account login or API key authentication and model configuration. Save personal defaults to use them across projects, or save settings only for the current project. Use `cade login` to change authentication or `cade setup` to configure the provider again. See [Installation and environment setup](docs/guide/install.md) for Linux shell sandbox dependencies.

Enter your first task, for example:

```text
Read the README and project entry point. Explain how the project runs in three points.
```

The default Act mode allows reading and searching. File writes and shell commands require approval according to permission rules. Enter `/mode build` to implement changes or `/mode plan` to investigate an approach first. See [Modes and permissions](docs/guide/modes.md) for automatic execution rules and mode switching.

## Daily use

| Action | How to use it |
| --- | --- |
| Submit a task | Enter text and press Enter |
| Reference a file | Enter `@` to find project files |
| Find a command | Enter `/`; select with ↑/↓, press Enter to accept, then press Enter to run |
| Enter multiple lines | Press Ctrl+J for a new line |
| Add a task during a run | Press Enter to queue it until the current task finishes |
| Steer the current task | Press Alt+Enter to send guidance to the current task |
| Stop a task | Press Ctrl+C with an empty input; clear existing input first |
| Browse conversation history | Use the scroll wheel or terminal page shortcuts, commonly Shift+PageUp/PageDown |
| Expand reasoning or tool results | Press Ctrl+T or Ctrl+O |
| Run a shell command | Enter `!git status`; permission approval still applies |

The welcome area lists common actions. Hints below the input change during completion, approval, and task execution. Some terminals send Alt+Enter as Esc followed by Enter; when idle, this combination inserts a new line. You can also enter `/steer YOUR_GUIDANCE`. Guidance takes effect before the next model request. Results from completed tools remain in the conversation.

The TUI supports explicit queuing with `/queue MESSAGE`. Use `/queue steer|followup|interrupt` to change what Enter does while a task is running.

## Resume work

```bash
cade -c                 # 继续当前项目最近的任务
cade --resume           # 从历史会话中选择
cade --session ID       # 恢复指定会话
```

If the project has no sessions, `cade -c` prompts you to start a new task. In the interface, use `/resume` to select a session or `/new` to start one. Enter `/exit` to quit. When idle with an empty input, you can also press Ctrl+C twice within three seconds.

On exit, Cade shows elapsed time and a copyable resume command. Sessions are stored in the project's `.cade/sessions/` directory by default. See [Sessions](docs/guide/sessions.md) for recovery, branching, and context management.

## Other entry points and configuration

```bash
cade exec "Review recent changes"     # 单次任务与自动化
cade web --open                       # 浏览器工作台
```

- [From your first task to resuming work](docs/guide/quickstart.md)
- [Commands and shortcuts](docs/guide/slash-commands.md) · [CLI options and exec automation](docs/guide/cli.md)
- [Models and providers](docs/guide/providers.md) · [Configuration](docs/guide/configuration.md)
- [Skills](docs/guide/skills.md) · [MCP](docs/guide/mcp.md) · [Hooks](docs/guide/hooks.md)
- [User guide](docs/guide/README.md) · [Architecture](docs/architecture.md)

## Development and Python integration

```bash
git clone https://github.com/reddishJade/cade.git
cd cade
uv sync --extra dev
uv run cade
```

Package code lives in `src/cade/`. See [AGENTS.md](AGENTS.md) for development checks and contribution rules. Python integrations can call `build_app` directly. See the [architecture](docs/architecture.md) for runtime entry points and lifecycle.

```python
from pathlib import Path
from cade.coding_agent.app import build_app

app = build_app(project_root=Path.cwd())
try:
    print(app.ask("List the Python files in the current directory."))
finally:
    app.close()
```

## License

MIT
