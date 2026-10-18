# Session trees and replayable records

`session` persists conversations, tool intent, rollover boundaries, and child lineage in append-only event trees for replay and auditing.

## Data flow

```text
Agent / user events
  → SessionRecorder
  → SessionStore
    → Root → Node 1 → Node 2: branch A
                    → Node 3: branch B
  → replay_session() / SessionHistory / SessionSurface
```

### Components

- [recorder.py](recorder.py) and [store.py](store.py): immutable facts, event trees, branches, and display indexes, stored in `.cade/sessions/` by default. Persisted `TreeNode` entries support forks from history.
- [replay.py](replay.py): ordered replay reconstructs messages and tool history.
- [history.py](history.py): the `history` tool provides `list_windows`, `search`, paginated `read`, and `around`. Explicit `session_id` locates original entries across sessions. Explicit-session `around` follows ancestors with `before` and rejects `after`; active-branch queries support both directions.
- [inbox.py](inbox.py): `SessionInbox` persists input arriving during execution.
- [surface.py](surface.py) and [window_projection.py](window_projection.py): replay canonical model facts, hydrate complete tool artifacts before selection, and regenerate built-in rollover windows from versioned policy recipes. Legacy `replacement` snapshots and externally supplied custom rollover projections remain readable.

Exact reads do not depend on the `session_index.json` navigation cache. Default local storage uses physical location for ownership, allowing workspace moves without changing old `project_path` values. Shared/custom storage validates the first JSONL record's workspace binding. Directory links resolving outside the workspace use external-storage validation.

## Invariants

Do not overwrite persisted event nodes. Undo and rewind use branch changes or new events. Session records are authoritative for recoverable execution state; do not maintain hidden persistent state that cannot be reconstructed from them. Event appends must protect existing records when a process exits unexpectedly. Model facts are recorded synchronously through `on_agent_event`; delivering the same event to a host does not append it again. Rollover metadata and its event are committed under the same branch lock. A claim in a new run closes interrupted tool batches without inventing results; same-run steering remains after the batch.

Event schema version 3 adds `runtime_context` facts. Readers also accept version 2 records. Replay restores complete tool bodies before applying request limits; missing or invalid artifacts required by the active window block recovery instead of substituting display previews.
