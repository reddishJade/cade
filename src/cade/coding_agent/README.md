# Coding product assembly

`coding_agent` assembles Harness, Agent, and AI capabilities into a coding assistant with file editing, search, shell execution, approval, and execution modes.

## Assembly flow

```text
TUI / exec / web / Python
  → build_app(project_root)
  → assembly: configuration, shared infrastructure, tool registry
  → CodingAgentHarness: NOTE context and rollover
  → Agent loop
```

### Components

- [app.py](app.py): `CadeApp` and `build_app()` expose the assembled product.
- [harness.py](harness.py): `CodingAgentHarness` adds NOTE lifecycle context, rollover handling, and automatic review integration to `AgentHarness`.
- [execution_modes.py](execution_modes.py): Plan restricts project writes, Build permits structured project edits with automatic shell review, and Act routes reviewable actions to the user. See [Modes](../../../docs/guide/modes.md) for boundaries.
- [assembly/registry.py](assembly/registry.py): `build_project_scoped_registry` assembles files, search, shell, subagents, and MCP tools.

## Invariants

Dependencies point from product to Harness, Agent, and AI. `CadeApp` serves runtime modes and browser hosts. Writes must obey workspace boundaries and permission decisions before reaching the OS.

Use NOTE for current task state, `.cade/memory/*.md` for sourced reusable knowledge, and session records for original interaction details.

## Example

```python
from pathlib import Path
from cade.coding_agent.app import build_app

# 为当前项目装配应用
app = build_app(project_root=Path.cwd())

# 提交任务
answer = app.ask("List Python modules in the current directory and check code style.")
print(answer)

# 关闭应用并保存状态
app.close()
```

## Submodules

- [assembly/](assembly/README.md): configuration, infrastructure, security, and tools.
- [prompting/](prompting/README.md): system identity and version guidance.
- [tools/](tools/README.md): file, search, shell, and other coding tools.
- [interaction/](interaction/README.md): shared commands, completion, discovery, and statistics.
- [modes/](modes/README.md): TUI and exec adapters.
- [cli/](cli/README.md): authentication, configuration, and session CLI operations.
