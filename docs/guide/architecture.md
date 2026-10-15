# Runtime reading guide

The [Cade architecture](../architecture.md) describes components, task lifecycle, state ownership, permission boundaries, and code entry points. TUI, exec, browser, and Python hosts share the application and harness. The architecture explains how user input, runtime events, session records, and host operations relate to each other.

For daily use, see [Execution modes](modes.md), [Sessions and undo](sessions.md), [Configuration](configuration.md), and [Slash commands](slash-commands.md). For budgeting and request auditing, see the [context policy](../context-policy.md). For memory evidence and storage rules, see [Cade's Memory](memory.md).

Before changing the implementation, use the [code entry points](../architecture.md#code-entry-points) to locate the responsible layer. See the [validation guidelines](../testing.md) for behavior checks.
