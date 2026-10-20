# Reviewer cache specification

## Goal and scope

Reduce automatic approval cost and latency without changing permission decisions or the `once` scope. Adopt the synchronous Guardian conversation pattern from Codex (`89461ea683`): reusable conversations, evidence deltas, stable cache identity, isolated concurrent forks, and committed checkpoints. Do not introduce Codex's separate asynchronous risk scorer or approximate score-based approval.

This specification defines the completed feature, not a prototype. Reviewer conversations are disposable runtime projections. Session facts remain the source of truth. Process recovery rebuilds a cold reviewer from current evidence; it does not require a new persistent session format.

## Behavioral requirements

- **R1 — Provenance.** The runtime supplies immutable, typed evidence. Only genuine user-source messages can supply authorization. System, runtime, assistant, tool, subagent, and unknown-source content cannot acquire authority through role labels or embedded tags. Main-agent instructions remain quoted evidence, not reviewer instructions.
- **R2 — Exact evidence.** Include the current assistant once if it is already in history. Include pending manual calls when they are not in history. Repeated text in distinct messages is not a duplicate. Preserve chronological order and mark omissions explicitly.
- **R3 — Isolation.** Each session has a separate reviewer conversation. Child sessions, branch switches, and restored histories cannot inherit unrelated reviewer state. Policy and provider replacement invalidate incompatible state and in-flight results.
- **R4 — Prefix reuse.** Keep reviewer policy and quoted fixed evidence first. Append subsequent review exchanges without rewriting earlier messages. Send only newly collected chronological evidence when the earlier evidence is an exact prefix. Send a complete current projection when continuity cannot be proved. Never infer continuity from a sliding-window length.
- **R5 — Native response replay.** Keep the complete successful provider response, including compatible opaque replay data. Use existing agent encoding rather than reconstructing prior assistant output from verdict JSON alone.
- **R6 — Concurrency.** Reserve the reusable trunk before inference. A concurrent request forks the last committed checkpoint without waiting for the trunk. Temporary forks never merge their results into the trunk. A fork without a compatible checkpoint starts from complete evidence.
- **R7 — Transactional state.** Build each attempt privately. Commit history and its matching evidence cursor together only after a complete, valid verdict and final context validation. Both genuine allow and deny verdicts can commit. Errors, malformed responses, cancellation, deadlines, and stale evidence cannot publish a checkpoint or a reusable decision. Retries start from the last committed state.
- **R8 — Revocation.** Before accepting a result or cached decision, recheck cancellation, provider generation, effective policy, and current evidence. Evidence changes during inference reject the stale result. New user guidance between completed reviews is appended; it does not itself require discarding an otherwise compatible conversation.
- **R9 — Bounded resources.** Bound concurrent model calls, retained sessions, decision records, and request tokens. Rebuild an oversized conversation from a bounded current evidence projection. Preserve user authorization and restrictions where possible; mark missing evidence. If required fixed evidence or the current action cannot fit, fail closed before inference. Do not silently summarize authorization.
- **R10 — Decision cache.** Deduplicate only the same logical review identity with identical action, evidence, policy, and provider generation. Different executions, changed arguments, changed evidence, and changed policy require fresh review. Never convert automatic approval into a session or permanent grant. Do not cache infrastructure failures.
- **R11 — Lifecycle.** Interrupting a caller cancels its outstanding review. Closing the service cancels and drains review tasks. A late response cannot update retired state. Configuration replacement uses immutable per-attempt provider snapshots.
- **R12 — Observability.** Record per-review session kind, decision-cache status, input/cached/output tokens, latency, and completion status without logging credentials or adding reviewer history to the working agent's evidence.

## Design

### Ownership and contracts

`agent/types.py` defines the evidence and approval-context contracts. The tool gate owns provenance, session/call identity, policy compatibility, and live-context validation. The permission engine forwards the contract without changing its permission resolver or grant semantics.

The security layer owns evidence rendering and reviewer conversation state. The existing isolated async worker supplies one service-owned event loop. Move that shared worker below `agent_runtime` so security and MCP can reuse it without importing the agent runtime assembly.

Reviewer requests continue to use no tools. Keep the synchronous approval callback as an adapter to the isolated service. Provider options are request-local; cache identity and response accounting must not leak between concurrent streams.

### Conversation state machine

A pool entry contains a compatibility key, an immutable committed checkpoint, and a reservation flag. A checkpoint pairs provider messages with the exact evidence prefix they represent. Selection and commit run on the service event loop.

1. Select a compatible idle trunk, or reserve a new trunk.
2. If the trunk is busy, clone its committed checkpoint into a temporary fork.
3. Select the evidence suffix only after exact prefix validation.
4. Construct an immutable request, with fixed evidence before variable evidence and the current operation.
5. Run inference with a shared deadline and cancellation.
6. Parse the verdict and validate the live context.
7. Commit the new history/cursor atomically only if the selected trunk is still current.
8. Discard fork state after completion.

Use a stable reviewer-specific `StreamOptions.session_id` derived from the owning session. Compatible forks share that cache identity. Cache routing is not an authorization boundary. Other transports retain their native cache behavior.

The pool and the decision cache are memory-bounded. Idle entries can be evicted safely. A cold request always includes current evidence. A history rewrite uses a fresh conversation even when the provider cache identity remains unchanged.

### Evidence and budget

Collect typed source messages before the old 40-message/40,000-character transcript projection. Keep fixed system evidence separately. Match a pending assistant by message identity or host-owned call association, never by free-text equality.

Use the existing token estimator and provider context window to admit complete requests, including replay history and output reserve. Render non-authorizing tool results with at most 4,000 characters and other non-authorizing dynamic entries with at most 8,000 characters. Preserve their prefix and suffix and mark truncation. Keep genuine user evidence and fixed evidence intact. Retention can remove non-authorizing evidence first. Reject a request if required policy, fixed evidence, user evidence, and operation cannot fit.

### Decision reuse

A cache entry belongs to one logical approval, not a command string. Match the owning session, run/call identity, normalized operation, immutable evidence, policy, and provider generation. Concurrent identical requests share work. Completed entries are bounded and expire with their compatibility context. Revalidate every delivery.

### Recovery

Reviewer state is not authoritative storage. On process restart or explicit history restoration, discard runtime reviewer state and reconstruct evidence from the restored working context. Existing omissions must remain visible. Main-agent rollover can cause a complete evidence rebase; it must never silently apply an old cursor to a new projection.

## Acceptance and validation

Deterministic tests must establish R1–R11 with controlled provider boundaries. Cover forged roles, preserved user authorization, repeated current assistants, pending manual calls, exact-prefix versus rewritten evidence, canonical response replay, busy-trunk forks, failed retries, revocation during inference, cancellation, provider replacement, bounded state, token-budget rejection, and decision identity isolation. Tests must assert externally visible requests, verdicts, or effects rather than only private field values.

Protocol integration must drive the real Responses adapter. Verify reviewer cache routing, opaque replay, and concurrent option isolation. Existing grant and call-local permission tests must continue to pass.

Real-service checks compare the previous baseline and the implementation on identical multi-review sequences. Record total input, cached input, noncached input, output, estimated cost, latency, and expected decisions. Include allowed operations, credential exfiltration, Plan-mode writes, revocation, and changed operations. Cache ratio alone is not an acceptance criterion. Preserve raw traces only in ignored local result directories.

Run a small public `cade exec` workflow with independently checked artifacts, plus interruption and process recovery. Run lint, formatting, types, all regressions, and CLI startup. Report service blockers separately from deterministic results.

## Implementation tasks

- [x] T1: Add typed evidence and live approval context; preserve provenance and pending calls (R1–R3).
- [x] T2: Implement bounded conversation pooling, exact deltas, native replay, and stable routing (R4–R6, R9).
- [x] T3: Implement atomic commits, cancellation, replacement, and lifecycle invalidation (R7–R8, R11).
- [x] T4: Implement bounded same-review decision reuse and per-review metrics (R10, R12).
- [x] T5: Add focused and protocol regressions; run static and full regression checks.
- [x] T6: Run paired real-service checks and public workflow acceptance; record remaining limits.
