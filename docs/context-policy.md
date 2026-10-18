# Request context and budget policy

This page describes request projections, evidence admission, input prediction, and rollover. See the [architecture](architecture.md) for runtime and state ownership, and [Sessions](guide/sessions.md) for original history retrieval. Implementation entry points are the [request assembler](../src/cade/agent/request.py), [context policy](../src/cade/agent/context_policy.py), and [context manager](../src/cade/agent/context_manager.py).

## Request projection and budgets

Session append events and external artifacts preserve facts. `DefaultRequestAssembler` creates a projection for each model request. A `ContextBlock` is complete context selected by its collector. Instructions remain in the prefix. Runtime user-context blocks become a `ContextMessage` committed at the send boundary, not during read-only assembly. Changed facts append a complete snapshot that supersedes earlier snapshots; unchanged facts add nothing. Deletion appends an updated or empty snapshot. Previous snapshots remain historical until explicitly reclaimed. The trace records selected blocks and collector truncation diagnostics.

`ContextPriority` controls stable ordering only: INSTRUCTION comes before CONTEXT, preserving collection order within each priority. Collectors and sections decide inclusion, not priority or remaining budget.

Immutable `ContextPolicy` manages the physical window, output reserve, safety margin, evidence admission, and rollover working set. The fixed prefix, tool schemas, selected blocks, persistent state, and active messages share the input budget. The assembler neither drops nor trims `ContextBlock` content again. Collection failures are explicit; a failed section cannot be skipped to send a request. Collector-generated bounded catalogs and summaries still report truncation and source locations in the trace.

See [Configuration](guide/configuration.md) for parameters and defaults. Model/provider metadata supplies the physical window; users can override it. Fallback to a smaller model tightens budgets:

```text
physical_window = input_budget + output_reserve + headroom
```

Increasing the output limit first increases output reserve, reducing input budget. Supported transports send that reserve as an output limit. For ChatGPT Codex, the reserve controls runtime budgeting only; audits record `output_limit_supported=false`.

`next_input_allowance` is capped at one-eighth of the trigger budget. The admission target is `rotation_threshold - next_input_allowance`. When predicted input plus this allowance reaches the trigger budget, rollover occurs early. Ratio and explicit token thresholds cap the trigger. Output reserve and headroom are each subtracted once. CLI and runtime read the same policy.

## Input prediction and evidence admission

Overall prediction anchors to successful provider input usage and estimates later message differences. With stable raw history, prefix, and configuration, NOTE updates, state replacement, and tool trimming retain the measured baseline. Replacing raw history or changing the prefix or configuration invalidates it and returns prediction to local estimates. Anchors use positive input usage from successful requests in the current window. Local prediction controls admission and early rollover; providers report actual usage.

Text differences measure decoded message bodies and tool arguments. Structured and multimodal content use conservative estimates.

Each tool body has a stable admission limit, bounded by input budget and `agent.evidence_token_budget`. Adding later results does not change earlier admitted bodies, preserving reusable request prefixes. Trimmed projections include tool-call IDs, execution status, and recovery references. Original results remain in session facts. Skill content and durable tool state are protected; multimodal content stays intact. Small bodies shorter than references stay as original text. Larger bodies use bounded head/tail previews. Reference token overhead counts toward both the limit and the full request.

The next request is assembled after tools finish, so actual tool results participate in evidence admission. Calls not yet executed receive a bounded new-input allowance. Current tool contracts do not provide reliable output limits or historical distributions, so this uses a uniform allowance.

A `context_lifetime=durable` result can declare state replacement with `context_key`. The latest successful value per key is protected. Older versions become history indexes; retained old results in mixed parallel groups are marked stale. Durable results without keys remain independent facts.

Admission uses the final request. If persistent state and new evidence exceed the predicted budget, the policy first tightens old evidence, then replaces old complete tool interactions with history indexes containing call IDs, and finally reclaims ordinary messages and old indexes from completed turns. Recent complete groups and durable groups remain.

After freeing space, the policy restores recent original evidence. Automatic rollover uses the input prediction before pressure reclamation, so temporary trimming cannot hide a full window and repeatedly rewrite cached history. Reclamation remains available when rollover is disabled or blocked. Each adjustment phase runs at most three times. With enough budget, arguments and results stay intact; otherwise, evidence is trimmed or complete old interactions reclaimed. Format adapters convert message formats; typed policies select and retain content.

The admission target includes the next-turn allowance and is a soft reclamation and rollover target. The safe input budget is the hard send boundary. If reclamation and available rollover cannot fit the request, `RequestAssembly.assembly_error` supplies diagnostics and `wire_messages` is empty. The send boundary calls `require_sendable()` to reject it. The loop ends with `context_error`, reporting estimated input, budget, and required input without calling the provider.

## Rollover working set

Automatic rollover carries startup context, the current user request, durable tool state, and the latest complete interaction. The input budget and `agent.working_set_token_budget` constrain recent interaction content; setting the quota to zero disables that quota. Tool calls and results stay paired, with large bodies replaced by history references. If a group still does not fit, a bounded operation index supports history recovery.

The new window reloads current NOTE, verification facts, and runtime state. Selected messages and operation indexes continue; original window content stays queryable through history.

Automatic token rollover requires required input to fit and reclaimable history to exist. Manual or model-requested rollover runs independently. `/compact` and `/rollover` use the current model policy. Replacing history clears dynamic state and old provider usage anchors; the next request recollects catalogs and sections. Explicit provider context overflow permits at most one recovery attempt when automatic rollover is enabled. Continued overflow retains the provider error.

## Budget observations

`RequestAssembly.context_snapshot` exposes physical and effective windows, fixed prefix, persistent state, working messages, evidence, output reserve, headroom, remaining budget, new-input allowance, admission target, next-turn prediction, window number, and rollover reason. Components reuse local request measurement and declare `category_source=local`. Their sum is `category_total_tokens`; provider-anchored prediction is `total_input_tokens`.

TUI `/context` and the web status bar use a read-only projection from the same assembler. They show the current prediction and estimate source. `/context` also shows effective input budget and lifecycle components. Accumulated usage and costs come from providers.

Components are attributed by injection location. System blocks and instruction catalogs count as fixed prefix. The latest runtime-context snapshot, current user intent, and protected tool groups count as durable. Earlier snapshots count as working history. Audits record snapshots, selected-block sources, reclaimed evidence, and reasons rollover conditions were not met.

## Request audits and rollover records

Before a normal agent request, `before_provider_request` receives the final `RequestAssembly` envelope. Session records store a BLAKE2b request fingerprint (32-byte digest), request size, provider information, options, composition ID, and assembly trace, including the pre-reclamation input prediction. These correlate requests with budget decisions. The current assembly owns the full wire payload.

`context_window_reset` stores the complete typed surface replacement, source entry IDs, and generation. Replay validates source IDs against the branch prefix, increasing generation, message structure, and tool pairing. Replacement validation uses these source and structure checks; existing fingerprints describe provider requests.

See [SessionRecorder](../src/cade/harness/session/recorder.py) for request auditing and [session surface](../src/cade/harness/session/surface.py) for rollover replay.
