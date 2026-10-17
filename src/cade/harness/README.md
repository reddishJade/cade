# Harness runtime and infrastructure

Harness supplies domain-independent sessions, permission gates, context rollover, sandbox execution, MCP/Skills integration, and auditing between the Agent loop and product layers.

## Components

```text
Agent product, such as coding_agent.app
  → AgentHarness
    → Execution and security: execution_env, security, observability
    → Sessions and memory: session, memory
    → Protocols and extensions: mcp, skills
```

| Package | Responsibility |
| --- | --- |
| [agent_runtime/](agent_runtime/README.md) | AgentHarness, ToolGate, rollover, and run control |
| [security/](security/README.md) | Permissions, shell analysis, human approval, and automatic review |
| [session/](session/README.md) | Append-only logs, branches, and original history retrieval |
| [execution_env/](execution_env/README.md) | Filesystem interfaces and Linux bubblewrap isolation |
| [observability/](observability/README.md) | JSONL audits, redaction, tracing, and lifecycle hooks |
| [mcp/](mcp/README.md) | Stdio MCP integration and tool registration |
| [memory/](memory/README.md) | Workspace Markdown saving and explicit History source validation |
| [skills/](skills/README.md) | Discovery, SKILL.md parsing, summaries, and on-demand activation |
| `auth/` | Host credential persistence and login/logout orchestration; provider OAuth and refresh belong to `cade.ai.auth` |

## Invariants

Harness must not import product layers such as `cade.coding_agent`. Put coding-specific state, such as NOTE management, in product implementations.

Security owns permission decisions and rule analysis, not observability implementations. Observability owns tracing and audits and must not re-export permission types.

Append-only session events are authoritative for runtime history and recoverable state. Derived projections must reconstruct from their recorded sources. Workspace Memory files remain independently maintained content with explicit history references.
