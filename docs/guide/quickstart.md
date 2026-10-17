# Quickstart

Start Cade in your project to analyze code, refactor it, or implement a feature.

## Configure a model and API key

Cade defaults to `gpt-5.6-luna` through Codex login with `high` reasoning effort. You can keep API keys for other providers at the same time. Settings select the default model independently of authentication.

To supply an API key through the environment:

```bash
# 方式 A：设置 DeepSeek 环境变量
export DEEPSEEK_API_KEY="sk-xxxxxxxxxxxxxxxxxxxxxxxx"

# 方式 B：设置标准 OpenAI 环境变量
export OPENAI_API_KEY="sk-xxxxxxxxxxxxxxxxxxxxxxxx"
# 第三方网关或自建服务也可指定 BASE_URL
export OPENAI_BASE_URL="https://api.deepseek.com/v1"
```

You can also put these settings in a `.env` file in the target project root. Cade reads it on startup.

## Start the terminal interface

From the repository you want to work on, run:

```bash
cd /path/to/your/project
cade
```

If no configured key is found on first startup, the setup wizard prompts you to select a provider and enter an API key.

The TUI shows the current execution mode (`act` by default), active model, and context status at the bottom.

## Complete a task

### Investigate in Plan mode

To inspect the code and propose changes first:

```text
> /mode plan
> Review the authentication module. Identify security risks and propose a refactoring plan.
```

Plan mode uses read-only exploration tools and writes plans to `.cade/plans/` for review. See [Execution modes](modes.md) for the shell review rules.

### Implement and test in Build mode

When the requirements are clear:

```text
> /mode build
> Add unit tests for format_date in src/utils.py. Run pytest and verify that they pass.
```

In Build mode, Cade searches files, applies edits, and submits shell commands such as `pytest` to the built-in review mechanism without asking you to approve each file change.

### Undo changes

If the result is not what you expected, enter:

```text
> /undo
```

Cade uses file snapshots to restore changes. See [Sessions and undo](sessions.md) for the undo boundary.

## Exit and resume

- To exit, enter `/exit` or press Ctrl+C twice while idle with an empty input.
- To resume work, return to the project directory and run `cade -c`.
