# Subagents

Large tasks can fill the main agent's context with repository scans, dependency searches, and intermediate logs. Persistent subagents let the main agent delegate independent work without retaining all intermediate output in its own window.

## When to delegate

- **Isolated context**: a subagent runs in an independent session branch and returns a summary to the main agent.
- **Separate model selection**: use a reasoning model such as `deepseek-v4-pro` or `gpt-6-sol` for the main agent and a smaller model such as `deepseek-flash`, `gpt-6-luna`, or `glm-4.7-flash` for subagents.
- **Independent investigation**: delegate goals such as comparing architecture options and running validation for each option.

## Runtime behavior

1. Each subagent creates a persistent session JSONL file in `.cade/sessions/`, recording `parent_session_id` for replay and tracing.
2. Subagents inherit the parent session's sandbox and permissions, with stricter timeouts and tool call limits.
3. On session shutdown or Ctrl+C interruption, Cade cleans up children first, terminating subagents and releasing their processes and temporary resources before their parents.

## Observe delegated work

The main agent delegates local investigations when needed. The terminal shows child startup, session IDs, and result summaries. Use `/tree` to inspect session history; see [Sessions](sessions.md) for its entry-tree semantics.
