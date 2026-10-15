# Cade design tradeoffs

This page explains the benefits, constraints, and maintenance costs of current design choices. The [architecture](architecture.md) defines components and behavior, the [context policy](context-policy.md) defines budgeting, and [AGENTS.md](../AGENTS.md) defines engineering rules.

## Session facts and request projections

An append-only log preserves user input, model output, tool interactions, and rollover boundaries so recovery, branching, and audits share a factual source. The branch head selects history; the surface selects model-visible messages. Persistent event protocols must remain readable, so storage changes require coordinated encoding, replay, and branch validation.

Request projections select content for the model window while original results remain in logs or artifacts. Previews, history references, and reclamation reduce request cost; ID-based retrieval preserves exact evidence. Models must reread reclaimed evidence. Paths, validation state, and unresolved work need ongoing maintenance in NOTE or other protected state.

Rollover builds a working set from existing messages, state, and operation indexes. It avoids an extra model summarization call while preserving tool pairing and traceable sources. Working-set budgets limit recent interactions. If required input still exceeds the limit, adjust the model window, task scope, or context sources.

## Configuration publication and runtime resources

Immutable composition binds providers, tool schemas, configuration, and static policy into a generation identified by a composition ID. Separating publication from active runs keeps request configuration consistent. Cancellation, grants, connections, and inboxes retain their own lifecycles.

The runtime owns session execution. A persistent inbox represents queuing and steering and can be rebuilt after restart. In-process tasks and connections must be recreated. A process can exit between a tool side effect and its log record, so recovery depends on recorded facts and workspace inspection.

## Permissions and execution limits

Typed `Action` values convert tool names, paths, and shell semantics into common decision inputs. Rules, path boundaries, hooks, and grants share a resolver. Explicit denial retains precedence. Automatic approval applies to one call, limiting the accumulation of long-lived model-approved access.

Approval allows an action to start; OS sandboxing limits accessible resources. Together, they cover anticipated actions and runtime behavior. Linux namespace isolation covers Agent Shell. MCP servers and trusted hooks use host permissions and need separate process access controls in their deployment environment.

File undo uses user-turn snapshots so a task's changes can be reviewed together. Comparing ending snapshots with current files reduces the risk of overwriting later edits. Size limits, exclusions, and deleted-file handling constrain coverage. Conflicts can produce partial restoration.

## Extensions and hosts

Provider, Shell, FileSystem, and tool protocols are replacement boundaries for network, file, and process access. Product registries select capabilities. Harness owns runtime and permissions. Hosts consume events and render intents. New capabilities need explicit execution semantics, permission targets, persistent results, and lifecycle handling.

Skills publish summaries before loading full instructions on demand. Memory stores sourced cross-session knowledge for ordinary file reads. Both depend on the model choosing when to use them; resulting actions remain subject to tool permissions.

Subagents use independent persistent sessions. Parents exchange self-contained prompts, child-run events, and tool results, supporting continuation after an instance is released. Parent permission domains, direct-parent control, and bounded delegation depth constrain execution. Parent hosts manage cancellation propagation, cold recovery, and dependency shutdown.
