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
4. **Project memory** is `MEMORY.md`. It contains durable project rules,
   architecture decisions, verified facts, and recorded experiences.
5. **User memory** is `~/.cade/memory/MEMORY.md`. It contains durable
   cross-project preferences. Experiences are project-scoped and never live here.

Current progress and next actions belong in `NOTE.md`, never in memory. The
transcript remains the source of truth when a record needs evidence, and the
repository always outranks memory: a record is a historical hint, not a claim
about current code.

## Experiences

```markdown
## fd single-file discovery mismatch
type: experience
root_cause: directory-oriented discovery assumes traversal semantics
fix: classify explicit file input before directory traversal
applies_when: fd backend with an explicit single-file path
anchors: src/cade/coding_agent/tools/fd.py, dir=src/cade/coding_agent/tools, err=NotADirectoryError
evidence: validation=ab12cd34ef56; verify=uv run pytest -q; exit_code=0; session=9f2c1a7b; anchor_state=sha256:1f0c…; commit=1a2b3c4
```

- The H2 title is the problem statement; there is no duplicate `problem` field.
- `anchors` is a comma list: a bare path is a file, `dir=` a directory,
  `sym=` a symbol, `err=` a verbatim error signature. Any single anchor is
  enough to record an experience; file/dir anchors additionally drive
  path-triggered hints and content freshness, `err=` drives error-triggered
  hints, and `sym=` records stay recall-only.
- Required fields are `root_cause`, `fix`, `applies_when`, at least one anchor,
  and at least one evidence pointer.

### Evidence is stamped, not dictated

`remember` never accepts model-authored evidence. The host stamps:

| Pointer | Meaning |
| --- | --- |
| `validation` | entry id of the recorded successful verification event |
| `verify` | the command that event actually ran |
| `exit_code` | that event's observed exit code |
| `session` | session that produced it |
| `anchor_state` | sha256 snapshot of the anchor files' current bytes |
| `commit` | HEAD at write time, auxiliary only |

`remember` refuses to write unless the **most recent** explicit verification
succeeded: a failed or unknown-outcome run blocks recording, and it never falls
back to an older success. That way a fabricated `test=…` string can no longer
become memory, and "just verified" cannot be claimed after a regression. This
makes the evidence *observed*, not *true*: it cannot prove that
`root_cause` is the real cause. What it guarantees is that the claim is
attached to a real, dereferenceable event that a later reader can inspect with
`history read session=<id> message_id=<validation id>`.

Evidence therefore has two tiers, and every index line states which one applies:

- `evidence=event` — carries `session=` and `validation=` pointers;
- `evidence=claim` — carried to memory by a human or an older version, with no
  recorded event behind it.

### Freshness is a content snapshot

`anchor_state` hashes the anchor files' bytes at write time. At read time the
same paths are re-hashed:

| state | meaning |
| --- | --- |
| `unchanged` | anchor bytes still match the snapshot |
| `changed` | anchor bytes differ; re-verify before applying |
| `missing` | an anchor file no longer exists |
| `unknown` | no snapshot (claim-tier or dir/symbol/error-only anchors) |

Git is not the baseline: a typical fix is recorded *before* it is committed, and
`git diff HEAD` cannot see an untracked file at all — both cases made the old
commit-based signal wrong in opposite directions. `commit=` stays in the record
as an extra pointer, and a `missing` anchor is reported as `missing` rather than
"nothing changed".

### Malformed records are never consumed

Anyone can edit `MEMORY.md` by hand, so validation is enforced at read time, not
only at write time. A record that declares `type: experience` but fails the
contract is refused by every consumption path:

- it produces no automatic hint;
- `recall` shows it only as an index line reading
  `INVALID experience: <reasons>`;
- `recall memory_id=…` refuses to return its body;
- the resume overview renders a repair line instead of the body.

The record stays visible so a human or the agent can fix it; it just never
becomes context that looks like a fact.

## Runtime flow

### Normal turns

The prompt states where memory lives and how to use it; no search results are
injected on every turn. Retrieval is two-stage and mirrors `history`:
`recall` returns index lines (id, problem, applicability, anchors, evidence
tier, freshness), and `recall memory_id=<id>` returns one full record. The index
exists so that reading three records costs three deliberate reads instead of
flooding the window.

One bounded hint block may be injected per request. `memory_hints` emits at most
three one-line pointers, and only when a record's file/dir anchor intersects the
files this session actually read or wrote, or when an `err=` anchor appears
verbatim in a failed tool result or the latest user message. There is no query,
no embedding, no top-k parameter, and no relevance weighting in that path.
Selection is one pass over all records — a broad `dir=` record can never crowd
out a precise file anchor just because it appears earlier in the file — ranked
only by match precision (file, then dir, then error) and freshness.

The block is `LOW` priority, so `ContextPolicy` drops it before rules, notes, or
validation facts. Each experience is auto-hinted at most once per context
window, and an explicit `recall` of that record suppresses its hint for the rest
of the window. That state lives in `MemoryHintState`, is reset on rollover, and
is never written to `MEMORY.md`.

### Recording an experience

`remember` writes one validated experience through the same `MemoryManager` used
by `/memory add`. Its parameters are the lesson only; the host supplies the
evidence. It costs at most one ordinary tool round trip — the same cost as any
other verification-following call — and the benchmark records
`provider_call_count` so that cost is measured rather than assumed. Nothing on
the memory path calls a model: there is no judge, reranker, summarizer, or
embedding, at write time or read time.

### Rollover

Cade uses the provider profile's `context_window` override when present;
otherwise it reads the active model's registered context window. Automatic
rollover begins at 95% or at the output-reserve boundary, whichever comes
first. The old window is closed without a summary. Startup context, activated
skills, and the latest real user request form the new working set. The full
assistant/tool trajectory is released even inside a running task. At 80% of
the rollover budget, provider usage triggers a reminder to save NOTE.md;
missing notes do not block model-requested or automatic rollover.

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

## Invariants

- Markdown is the source of truth for durable memory.
- Surface replacements are isolated by session branch.
- Resume never drops the verbatim transcript tail.
- Memory search is deterministic BM25 over project and user files.
- Every consumption path revalidates: no malformed record is ever rendered as
  memory content, whichever way it was written.
- Memory never calls a model: no judge, reranker, summarizer, or embedding.
- No per-record governance state is persisted; freshness is derived per request
  and hint suppression lives only in the runtime window.
- Writes are explicit and atomically replace the target file.
- Experiences are project-scoped, because repo-relative anchors are meaningless
  in another project.
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

An experience becomes durable only by an explicit, validated write. Validation
gates consumption, not promotion: there is no candidate or quarantine state.

## Stop point

The implemented surface/history cycle plus the experience loop is the product
boundary. Any new memory mechanism must first show, in the three-arm benchmark
described in `benchmarks/README.md`, that a relevant verified experience reduces
exploration without lowering task success and that irrelevant or stale
experience does not degrade the no-memory baseline. Otherwise it is removed.
