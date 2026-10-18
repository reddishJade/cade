# Auditing, correlation, and hooks

`observability` records decisions and tool execution with credential redaction and typed lifecycle hooks.

## Event flow

```text
Agent / ToolGate / Provider
  → HookManager
    → JsonlAuditLogger: structured append-only records
    → RuntimeCorrelation: trace identifiers
    → ExternalHookRunner: host commands
  → redact_text: mask known key and token patterns
```

### Files

- [audit.py](audit.py): `JsonlAuditLogger` appends JSONL records; `redact_text` masks known API key, authorization header, and private-key patterns before logging or external delivery.
- [correlation.py](correlation.py): `RuntimeCorrelation` and `EventCorrelation` link sessions, turns, and child runs.
- [hooks.py](hooks.py): `HookManager` dispatches `HookRecord` callbacks for `before_agent_start`, `before_provider_request`, `pre_tool`, `post_tool`, and other events, supporting synchronous handlers and background execution.
- [external_hooks.py](external_hooks.py): run external commands and collect diagnostics.

## Invariants

Keep auditing and event dispatch separate from permission decisions. Do not re-export `harness.security` types or couple implementation details across those boundaries.

Audit write failures and noncritical external hook failures must produce diagnostics rather than crash the task. Explicit hook failure policies still apply. Session fact persistence is critical: failures stop execution instead of continuing with an unrecorded model history.
