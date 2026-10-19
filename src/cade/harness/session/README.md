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
- [read_model.py](read_model.py): store-owned incremental read index and revision-bound views. Runtime fact queries exclude request audits; exact reads recover original audit bodies by validated byte range. All branch IDs remain available for lineage checks.
- [runtime.py](runtime.py): binds the inbox and recorder; reads cross-run history from the canonical fact projection of a synchronized view.
- [replay.py](replay.py): restores metadata and message-derived product state without installing a mutable history copy.
- [history.py](history.py): the `history` tool provides `list_windows`, `search`, paginated `read`, and `around`. Explicit `session_id` locates original entries across sessions. Explicit-session `around` follows ancestors with `before` and rejects `after`; active-branch queries support both directions.
- [inbox.py](inbox.py): `SessionInbox` persists input arriving during execution. Its cache changes only after a committed lifecycle event; a failed write rebuilds it from facts. Reload holds the inbox lock through projection so it cannot overwrite a concurrent insertion.
- [surface.py](surface.py) and [window_projection.py](window_projection.py): replay canonical model facts, hydrate complete tool artifacts before selection, and regenerate built-in rollover windows from versioned policy recipes. Legacy `replacement` snapshots and externally supplied custom rollover projections remain readable.

The read index is process-local and disposable. It synchronizes committed suffixes under the store lock, rebuilds after replacement or truncation, and is released on session switching. Returned records cannot mutate the index. Partial malformed tails remain uncommitted; valid legacy records without a final newline retain their existing read behavior. Exact reads do not depend on the `session_index.json` navigation cache. Default local storage uses physical location for ownership, allowing workspace moves without changing old `project_path` values. Shared/custom storage validates the first JSONL record's workspace binding. Directory links resolving outside the workspace use external-storage validation.

## Invariants

Do not overwrite persisted event nodes. Undo and rewind use branch changes or new events. Session records are authoritative for recoverable execution state; do not maintain hidden persistent state that cannot be reconstructed from them. Event appends must protect existing records when a process exits unexpectedly. `RuntimeSession` binds the inbox and recorder to one store. The runtime records model facts and final results before delivering events; hosts do not record delivered events. A store-owned file lock excludes concurrent runs and branch transitions of the same persistent session. Session switching checks both the source and target; manual window replacement holds the lock from projection through commit. Rollover metadata and its event are committed under the same branch lock. Before claiming new input, the runtime records an explicit recovery result for each unfinished tool attempt under the session run lock. These records mark the outcome as unknown and do not repeat execution or claim the action failed. Same-run steering remains after a complete tool batch. Older logs with unfinished batches remain readable.

`commit_final()` rejects a second final for the same run and returns a `RunCompletionReference`. A `host_outcome` contains only the host name, referenced final entry ID, host-specific details, and session/run correlation. Reference validation and append share one store transaction. A switched session, an unrelated branch, or a mismatched run cannot accept the attachment. Legacy `exec_result` records remain readable but are no longer written.

Event schema version 3 adds `runtime_context` facts. Readers also accept version 2 records. Replay restores complete tool bodies before applying request limits; missing or invalid artifacts required by the active window block recovery instead of substituting display previews.
