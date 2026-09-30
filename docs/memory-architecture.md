# Long-horizon memory architecture

## Purpose

Memory exists to let one logical coding task survive bounded model windows and
process restarts, and to reuse coding experience that was expensive to learn.
It is not a knowledge-management product and does not assign behavioral scores
to individual notes.

## Storage layers

1. **Transcript** is the lossless history. Session JSONL keeps user messages,
   assistant messages, and tool events.
2. **Session surface** is the disposable model working set. Each rollover
   appends a typed replacement event without rewriting older entries.
3. **Working note** is project-root `NOTE.md`. It contains the current goal,
   confirmed decisions, verification status, unresolved issues, and next action.
4. **Project memory** is `MEMORY.md`. It contains only durable project rules,
   architecture decisions, verified cross-session facts, and recorded
   experiences.
5. **User memory** is `~/.cade/memory/MEMORY.md`. It contains durable
   cross-project preferences.

The layers have different jobs. Current progress and next actions belong in
`NOTE.md`, never in project or user memory. The transcript remains the source
of truth when a note needs evidence.

## Experiences

An experience records that a problem was hit before, what its root cause was,
what fix worked, and when it applies. It never asserts what the current code
does; repository files, git, and tests outrank memory.

```markdown
## fd single-file discovery mismatch
type: experience
root_cause: directory-oriented discovery assumes traversal semantics
fix: classify explicit file input before directory traversal
applies_when: fd backend with an explicit single-file path
anchors: src/cade/coding_agent/tools/fd.py, dir=src/cade/coding_agent/tools, err=NotADirectoryError
evidence: commit=1a2b3c4; session=9f2c1a7b; test=uv run pytest -q
```

- The H2 title is the problem statement; there is no duplicate `problem` field.
- `anchors` is a comma list: a bare path is a file, `dir=` a directory,
  `sym=` a symbol, `err=` a verbatim error signature.
- `evidence` is a `;`-separated list of `key=value` pointers. `commit` and
  `session` are stamped by the `remember` tool, never authored by the model.
- Required fields are `root_cause`, `fix`, `applies_when`, at least one file or
  directory anchor, and at least one evidence pointer. Records that fail this
  check are refused at write time by every write path and are never consumed.
- Retired governance fields (`confidence`, `status`, `validity`, `utility`,
  counters) are rejected in experience records, so the deleted state machine
  cannot regrow. `status` also stays dangerous on read: a retired value removes
  the record during parsing.

## Runtime flow

### Normal turns

The system prompt tells the agent where memory lives and when to use it. It does
not inject search results on every turn. The agent calls the read-only `recall`
tool when prior project knowledge may matter, optionally with
`anchor=<repo-relative path>` to get only experiences anchored at a file.

One bounded, deterministic hint may be injected per request: `memory_hints`
emits at most three one-line pointers when a record's file/directory anchor
intersects the files this session actually read or wrote, or when an `err=`
anchor appears verbatim in a failed tool result or the latest user message.
The block is `LOW` priority, so `ContextPolicy` drops it before rules, notes, or
validation facts, and it carries pointers only — never experience bodies. There
is no query, embedding, top-k parameter, or relevance score in that path, and no
per-record state is persisted: `state=` is computed per request with
`git diff --name-only <evidence commit> -- <anchors>`.

### Recording an experience

`remember` writes one validated experience through the same `MemoryManager` used
by `/memory add`. It runs inside the normal agent loop, so no extra model request
is made for memory: validation is deterministic, and the model is asked to place
the call in the same assistant message as its verification step.

### Rollover

Cade uses the provider profile's `context_window` override when present;
otherwise it reads the active model's registered context window. Automatic
rollover begins at 95% or at the output-reserve boundary, whichever comes
first. The old window is closed without a summary. Startup context, activated
skills, and the latest real user request form the new working set. The full
assistant/tool trajectory is released even inside a running task. At 80% of
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

Rules and decisions are restored as records. Experiences are restored as
pointers only, so a mature experience file cannot consume the resume budget;
their bodies require an explicit `recall`.

The latest final event already contains the structured coding run state. Resume
restores its execution mode and goal after rebuilding message history.
Older exact evidence remains available through `history` list/search/read/around.

## Invariants

- Markdown is the source of truth for durable memory.
- Surface replacements are isolated by session branch.
- Resume never drops the verbatim transcript tail.
- Memory search is deterministic BM25 over project and user files.
- Memory never calls a model: no judge, reranker, summarizer, or embedding.
- No per-record governance state is persisted; freshness is derived per request.
- Writes are explicit and atomically replace the target file.
- Existing governance metadata is ignored; retired legacy records stay excluded,
  except `evidence`, which carries experience provenance.

## Explicit non-goals

Do not add these without evidence from real long-running task failures:

- embeddings or vector databases;
- per-record utility, adoption, success, or failure counters;
- confidence and validity state machines;
- automatic promotion based on inferred model behavior;
- multi-factor reranking or tunable relevance weights;
- background extraction, session-end recap, or any hidden model call;
- online explain/metrics platforms for a local Markdown search;
- automatic retrieval injection on every user turn.

An experience is durable only because it was written by an explicit, validated
action. Validation gates consumption, not promotion: there is no candidate or
quarantine state.

## Stop point

The implemented surface/history cycle plus the experience loop is the product
boundary:

- rollover never summarizes the previous window;
- malformed or tool-unbalanced replacements are rejected;
- `history list_windows/search/read/around` retrieves exact details older than
  the current working set;
- `remember`/`recall`/`memory_hints` reuse expensive-to-learn coding experience
  without a model in the memory path.

Any new memory mechanism must first show, in the three-arm benchmark described
in `benchmarks/README.md`, that a relevant verified experience reduces
exploration without lowering task success and that irrelevant or stale
experience does not degrade the no-memory baseline. Otherwise it is removed.

