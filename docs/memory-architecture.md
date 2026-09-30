# Long-horizon memory architecture

## Purpose

Memory preserves durable knowledge across coding sessions, while working notes
and history let a logical task survive bounded windows and process restarts.
Experience is a historical hint that requires current repository checks.
Memory does not assign behavioral scores to individual notes.

## Storage layers

1. **Transcript** is the lossless history. Session JSONL keeps user messages,
   assistant messages, and tool events.
2. **Session surface** is the disposable model working set. Each rollover
   appends a typed replacement event without rewriting older entries.
3. **Working note** is project-root `NOTE.md`. It contains the current goal,
   confirmed decisions, verification status, unresolved issues, and next action.
4. **Project memory** is `MEMORY.md`. It contains only durable project rules,
   architecture decisions, verified cross-session facts, and expensive-to-learn
   coding experiences with applicability and evidence pointers.
5. **User memory** is `~/.cade/memory/MEMORY.md`. It contains durable
   cross-project preferences.

The layers have different jobs. Current progress and next actions belong in
`NOTE.md`, never in project or user memory. The transcript remains the source
of truth when a note needs evidence.

## Runtime flow

### Normal turns

The system prompt tells the agent where memory lives and when to use it. It does
not automatically inject search results on every turn. The agent calls the
read-only `recall` tool when prior project knowledge may matter.
Experience uses a plain `Type: experience` body convention with Problem, Root
cause, Fix pattern, Applies when, Anchors, and Evidence. Only complete records
with a literal anchor in the explicit query participate in search. Scope terms
cannot bypass this gate. Current repository evidence must be checked before use.
See [the design audit](experience-memory-design.md) for history and limitations.

There is no dedicated Agent memory-write capability. Ordinary file tools can
visibly edit MEMORY.md under existing permissions; user CLI CRUD uses the
manager's lock and atomic replacement. File-tool writes do not acquire that
manager lock. Tool calls continue the Agent loop, so a standalone memory write
can add a visible provider round trip. No memory background inference runs.

### Rollover

Cade uses the provider profile's `context_window` override when present;
otherwise it reads the active model's registered context window. Automatic
rollover begins at 95% or at the output-reserve boundary, whichever comes
first. The old window is closed without a summary. Startup context, activated
skills, and the latest real user request form the new working set. Protected
state and a bounded recent complete interaction may also be retained; older
assistant/tool evidence remains retrievable from history. At 80% of
the rollover budget, provider usage triggers a reminder to save NOTE.md;
missing notes do not block model-requested or automatic rollover. The typed event
records the replacement, source entry IDs, a monotonic generation, and a stable
fingerprint.

### Resume

Cade restores:

```text
latest durable context-window replacement
+ transcript entries appended after the replacement
+ NOTE.md working state
+ budgeted project and user memory
```

The latest final event already contains the structured coding run state. Resume
restores its execution mode and goal after rebuilding message history.
Older exact evidence remains available through `history` list/search/read/around.
The overview excludes every explicitly marked Experience, including incomplete
ones. Explicit recall results remain disposable tool evidence under the existing
ContextPolicy budget; full results remain in history even when request previews
are cropped. The current history tool reads only the bound session branch.

## Invariants

- Markdown is the source of truth for durable memory.
- Surface replacements are isolated by session branch.
- Resume never drops the verbatim transcript tail.
- Memory search is deterministic BM25 over project and user files.
- Manager CRUD writes are explicit and atomically replace the target file;
  ordinary file tools retain their existing write semantics.
- Existing governance metadata is ignored; retired legacy records stay excluded.

## Explicit non-goals

Do not add these without evidence from real long-running task failures:

- embeddings or vector databases;
- per-record utility, adoption, success, or failure counters;
- confidence and validity state machines;
- automatic promotion based on inferred model behavior;
- multi-factor reranking;
- online explain/metrics platforms for a local Markdown search;
- automatic retrieval injection on every user turn.

## Stop point

The implemented surface/history cycle is the product boundary:

- rollover never summarizes the previous window;
- malformed or tool-unbalanced replacements are rejected;
- `history list_windows/search/read/around` retrieves exact details older than
  the current working set.

Early background extraction and automatic project-memory promotion are not
planned. They require evidence from real long-running task failures and must
not expand the per-record Memory model.
