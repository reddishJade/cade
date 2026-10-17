# Cade user and developer guide

This guide covers installation, daily use, session management, permissions, and extensions. For component responsibilities and implementation entry points, see the [architecture](../architecture.md).

## Getting started

- [Installation and environment setup](install.md): Python 3.12+, uv, ripgrep, and bubblewrap requirements.
- [Quickstart](quickstart.md): configure authentication and complete your first coding task.

## Daily use

- [Architecture](../architecture.md): components, task lifecycle, state ownership, and code entry points.
- [Execution modes](modes.md): when to investigate and when to apply changes automatically.
- [Slash commands](slash-commands.md): terminal commands and common workflows.
- [CLI and terminal interaction](cli.md): shortcuts, file references, shell commands, and noninteractive execution.
- [Browser workspace](web.md): the web interface and diff review.

## Sessions and security

- [Sessions, undo, and context rollover](sessions.md): recovery, user-turn undo, and budget-based rollover.
- [Tools and execution scheduling](tools.md): built-in tools and scheduling rules.
- [Permissions and sandboxing](security.md): bubblewrap isolation, read-only protection, and reviewer decisions.
- [Configuration](configuration.md): configuration sources, runtime settings, budget defaults, and credentials.

## Extensions

- [Models and providers](providers.md): OpenAI, DeepSeek, GLM, MiMo, and local services.
- [MCP](mcp.md): connect external tools such as GitHub and PostgreSQL.
- [Skills](skills.md): project-specific instructions in `SKILL.md`.
- [Cade's Memory](memory.md): purpose, examples, source verification, and implementation boundaries.
- [Subagents](subagents.md): delegate tasks with isolated contexts.
- [Lifecycle hooks](hooks.md): run scripts at lifecycle events.

## Maintenance

- [Versions and releases](releases.md): version policy, tag checks, and release workflow.
