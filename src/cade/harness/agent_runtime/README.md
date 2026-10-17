# Runtime scheduling and context

`agent_runtime` coordinates tool gates, context rollover without model summaries, cancellation, and queued execution.

## Execution flow

```text
User input / SessionRunController
  → AgentHarness.run()
    → Check ContextWindowRollover
    → Assemble request prefix and current directory
    → Provider.stream()
    → ToolGate: pre-hook, permissions, approval, post-hook, audit
    → Persist events through SessionRecorder
```

### Files

- [harness.py](harness.py): `AgentHarness`, long-running execution, events, and recovery.
- [tool_gate.py](tool_gate.py): argument validation, permissions, approval callbacks, and hooks.
- [context_window.py](context_window.py): `ContextWindowRollover`, which starts a new working window, retains required facts, and leaves original content available through `history` instead of recursively summarizing it.
- [run_control.py](run_control.py): `SessionRunController`, busy-input lanes and session execution ownership.
- [subagents.py](subagents.py): child lifecycle and session topology.
- [cancellation.py](cancellation.py): cooperative `CancellationToken`.

## Invariants

Do not inject mandatory model-generated summaries during rollover. Preserve original evidence and explicit recovery references instead. All tool execution must pass through ToolGate for checks and auditing; do not call handlers directly to bypass it.

## Submodules

[prompting/](prompting/README.md) builds runtime prefixes and tool guidance. Request assembly owns budget admission.
