# Session read model

Status: implemented. The persistent session format is unchanged.

## Goal

Keep Cade's product capabilities and single authoritative session log. Remove repeated full-log decoding from normal execution. Preserve replay, branching, durable input, tool recovery, approvals, and exact request auditing.

The defect is an access-boundary problem: consumers independently rebuild state from a log that also contains large audit snapshots. Recursive serialization amplifies the cost. Changing serialization alone does not remove the repeated work.

## Ownership

- `SessionStore` owns committed records, branch identity, the storage lock, and one disposable read index per open store. It synchronizes the index with committed file data.
- `RuntimeSession` owns runtime access to session facts. The active run retains its existing working messages. The read index must not become another writable conversation history.
- Product collectors select product evidence from a store-owned read view. They do not scan session files or maintain independent log caches.
- Exact history and audit readers retrieve original records through the store's reader. A filtered runtime view must never masquerade as an exact audit record.
- Hosts remain event consumers. They do not update runtime projections or submit another final result.

The index is derived, process-local, and disposable. It has no independent write API and is never required for recovery.

## Read paths

`SessionReadView` provides the internal revision-bound read boundary. A revision identifies the file instance, committed extent, and selected head. A view exposes ordered branch IDs, event selection, and exact record lookup.

The store decodes and validates a new committed record once per index lifetime. It indexes IDs, parents, heads, event kinds, and byte ranges. It retains semantic payloads needed by runtime readers, but does not retain complete historical request messages and tool schemas in the hot payload cache. Exact audit reads decode the original byte range on demand.

Keep complete request snapshots in the existing JSONL records. Do not move them to artifacts, remove fields, introduce SQLite, or add persistent checkpoints in this change. Cold opening remains proportional to the existing log size; warm reads must not decode historical audit bodies again. Warm processing must depend on newly committed data and the semantic records actually queried, not the aggregate size of old request snapshots.

The index may retain metadata proportional to the session's record count. Bound any exact-payload cache. Release the index when its owning store closes or switches away. Do not cache all sessions globally.

Preserve every branch ID, including audit IDs. Rollover lineage checks and final references must use the original ordered branch, not the filtered event sequence. Existing `read_entries()` and `build_branch()` keep their exact-read meaning until affected callers explicitly migrate to the read view.

## Commit and recovery

1. Acquire the existing store lock before synchronizing or changing the log and read index.
2. Validate and durably append a record using the existing commit protocol.
3. Publish derived index changes only after the append commits. Reuse the validated record where safe.
4. If a write fails with uncertain commit status, invalidate the derived view and reconcile from committed bytes. Do not silently repeat the append or tool action.
5. Keep `append_derived()` validation and append in the same store transaction. Do not accept a stale view for final uniqueness or host-result validation.

Each reader synchronizes the committed suffix before exposing a new view, including suffixes appended by another process through the supported storage protocol. A malformed partial trailing record does not advance the cursor. Valid legacy records without a final newline retain their existing read semantics. A malformed committed record remains an error.

File replacement, truncation, session switching, and an incompatible branch revision invalidate the relevant view. Head changes must not be mistaken for an append to the old active branch. Legacy records retain the current index-head fallback; new records continue to derive their head from committed log data. Arbitrary in-place mutation outside the append-only storage protocol is not made a supported operation by this optimization.

Keep the run lock and inbox lock ordering. Do not remove recovery checks or make durable commits asynchronous to obtain a speedup.

## Consumer migration

- Inbox and unresolved-tool recovery read the same store revision without separately decoding the log.
- Cross-run history uses the existing canonical surface projector over the read view. Do not add a second incremental message reducer in this change.
- `ValidationCollector` queries branch tool facts. It retains its existing selection, workspace-fingerprint, and current-run-message behavior.
- Final uniqueness, host-result references, and summaries read indexed branch facts under the existing transaction boundaries.
- `SessionHistory` retains workspace ownership checks, path restrictions, pagination, and exact audit access. It must not become a parallel persistence authority.

Replace the read-side `TreeEntryModel.model_dump()` round trip with an explicit field transfer after validation. Preserve all record values and JSON validation. Do not combine this with a new event schema or relaxed validation.

## Delivery

1. Remove redundant read-side serialization. Verify exact record equivalence.
2. Add the store-owned read index and view while retaining exact-read APIs. Verify cold rebuild and suffix synchronization.
3. Migrate runtime, validation, and history consumers. Preserve current behavior until each replacement works end to end.
4. Run the complete session and execution contract suite, then repeat the controlled long-session comparison.

## Acceptance

- Cold and warm views produce the same messages, inbox state, unresolved tool state, branch lineage, and request bytes as the existing implementation.
- Warm reads with no new data parse zero old records and read zero historical audit-body bytes. Appends process only the committed suffix.
- Exact audit reads return the complete original messages, tool definitions, and fingerprints. Mutating returned data cannot change a later view.
- Cover rewind, fork, clone, rollover, session switching, legacy head fallback, and two-process append visibility.
- Preserve exclusion of concurrent runs and correct ordering of input, claims, tool facts, and final delivery.
- Cover incomplete tails, invalid committed records, duplicate IDs, unknown parents, failed appends, failures after a durable append, replacement, and truncation.
- Preserve uncertain-tool recovery without repeating effects. Preserve missing-artifact and invalid-lineage failures.
- Use structural work counters for regression assertions. Record wall time and memory separately on the same large-session fixture; do not enforce machine-specific timing thresholds in unit tests.

Provider transport errors, reviewer conversation caching, and request-token reduction remain separate work. This change must not claim to resolve them.
