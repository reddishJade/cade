# Benchmarks

## Long-horizon context-window rollover benchmark

This benchmark measures one controlled question: how much summary-free context
rollover reduces cumulative input tokens, cost, and interrupted long sessions
without lowering test-defined task success or state retention.

### Experimental groups

- **Baseline** sets runtime context rollover to `None`. It keeps full logical
  history and restores full history after a simulated process restart.
- **Cade** opens a fresh active context at the declared boundary, appends a
  typed `context_window_reset` event, and restores the durable surface. No
  summary request is made.

All other runtime configuration is shared. Request hygiene remains enabled in
both groups, so the ablation isolates fresh-window rollover instead of
mixing it with transport sanitation. Web tools and subagents are prohibited by
a shared benchmark instruction. Automatic and model-initiated rollover are
disabled; the runner alone applies each task's declared boundary. Both groups
run in Build mode because the
non-interactive benchmark has no HITL approval callback; workspace writes and
verification commands therefore execute automatically under the same safety
boundaries. Repeat order alternates to reduce time-order bias.

Every copied fixture is initialized as a fresh, benchmark-owned Git repository
with a deterministic initial commit. The commit is identical across paired
runs, `git diff HEAD` therefore compares against the task fixture instead of a
parent checkout, and runtime artifacts such as `.benchmark/` and Python caches
are excluded from status output. Any fixture-provided `.git` metadata is not
copied.

Session transcript state lives in a sibling runtime directory rather
than inside the task workspace. The runner can still resume from it, while the
Agent's workspace-scoped tools cannot treat internal benchmark state as task
evidence. `--keep-workspaces` preserves both locations for debugging; normal
runs remove them after writing the raw record.

Provider `UsageUpdate` events are recorded for every agent request. A run is
excluded from token and cost aggregation if any provider request omits usage.
Known-model cost uses the price snapshot in
`src/cade/ai/models.py` and is stored in every raw result.

### Run the paired example

Use the same explicit model configuration and temperature for every group:

```sh
uv run python -m benchmarks.runners.run_ablation \
  benchmarks/tasks/long_horizon/parser_recovery/task.json \
  --config cade.config.json \
  --temperature 0 \
  --repeat 3 \
  --max-pair-attempts 2 \
  --require-complete-usage
```

This command makes real API calls. Raw JSON, `summary.json`, and `report.md` are
written below `benchmark-results/long_horizon/<timestamp>/`. Add
`--keep-workspaces` when a failed run needs manual inspection.

`--max-pair-attempts` retries both baseline and Cade in fresh workspaces when
a transient provider error leaves usage incomplete. Every attempt remains in
raw JSON; the report selects the first complete pair and lists excluded
attempts with their reasons. The default is one attempt to avoid unexpected API
cost. `--require-complete-usage` (alias `--fail-on-incomplete`) writes the report
and then exits with status 2 if any selected pair still lacks complete usage.

Interactive terminals show an overall run bar plus the current task's turn,
model request, tool, context-window reset, restart, and verification status. During a
model call, the status distinguishes waiting for the first event from active
reasoning, answer streaming, tool calls, usage, and finalization. It also shows
the request number, elapsed time, time since the last event, and event count,
with a one-second heartbeat even when the provider is silent. Redirected output
and CI receive timestamped lines with model heartbeats limited to one every 30
seconds. Use `--no-progress` only when another process is supervising the
command.

Run groups separately when required:

```sh
uv run python -m benchmarks.runners.run_baseline TASK.json --repeat 3
uv run python -m benchmarks.runners.run_cade TASK.json --repeat 3
```

Regenerate a report from existing raw records:

```sh
uv run python -m benchmarks.reports.generate_report \
  benchmark-results/long_horizon/RUN_DIR \
  --output-dir benchmark-results/long_horizon/RUN_DIR
```

### Task contract

Each `task.json` declares:

- an isolated fixture workspace;
- ordered user turns, including explicit rollover/restart boundaries;
- one test command that determines `task_success`;
- deterministic state facts such as changed/unchanged file hashes, forbidden
  paths, required text, and post-resume commands;
- fallback recent-message and recent-token budgets used when no active user
  turn can be identified.

The included parser task is a wiring example, not enough evidence for a resume
claim. A resume run counts only when `surface_resumes` is nonzero. Before
publishing results, add 20–30 tasks with at least 10 turns, run multiple repeats,
and inspect per-task pairs instead of reporting only a pooled mean.

### Reported metrics

- `input_tokens_total` and `peak_input_tokens` come from provider usage;
- `pre_rollover_input_tokens` covers turns before the declared rollover;
- `post_rollover_input_tokens` starts at the declared fresh-window turn;
- `post_resume_input_tokens` starts on the turn after the restart boundary;
- `task_success` comes only from the verification process exit code;
- `state_retention` is the fraction of deterministic facts that pass;
- `context_overflow` is detected from provider/runtime context-limit errors;
- `long_session_completed` requires tests, all turns, normal termination, and
  no context overflow;
- `repeated_read_calls` counts repeated reads of the same path as a diagnostic,
  not a success criterion.

Each metric uses its own paired cohort. Total Token and cost require complete
usage for the whole baseline/Cade pair, while post-rollover metrics remain
eligible when missing usage occurred only before that phase. Correctness and
state-retention metrics include the selected attempt regardless of usage
completeness. Reports show the cohort size on every row.

Do not quote percentages from the example until enough paired task runs have
completed and `usage_complete` is true for the included samples.

## Memory experience benchmark

This benchmark measures whether a stored coding experience changes what the
Agent does, using the same loop as the long-horizon benchmark. Each task runs
three times against isolated workspace copies:

- **none** seeds no `MEMORY.md`; this is the no-memory baseline;
- **relevant** seeds the task's `memory/relevant.md`, an experience that matches
  the fixture failure, as `<workspace>/MEMORY.md`. Its `evidence` line carries a
  literal `{anchor_state}` placeholder: the runner resolves it to the sha256
  snapshot of the anchor file's actual content inside the prepared workspace, so
  the hint renders `state=unchanged`;
- **irrelevant** seeds the task's `memory/irrelevant.md`: one decoy experience
  anchored on an unrelated subsystem that never fires, plus one stale experience
  that does fire on the task's real anchor but names a wrong root cause and a
  wrong fix location. Its `anchor_state` is a well-formed but non-matching
  digest, so that hint renders `state=changed` while still firing — the
  negative-transfer case.

Freshness is content-based, not commit-based: it compares the recorded sha256
snapshot against the current file bytes, so an uncommitted working tree is
described correctly and a fixture never needs a synthetic commit history.

`MEMORY.md` is written before the deterministic Git baseline commit, and the
placeholder is resolved before that commit as well, so every arm starts with a
clean `git diff HEAD` and the record itself is part of the baseline rather than
an uncommitted experiment artifact. `--dry-run` prints the resolved commit, the
worktree cleanliness, and the hint state observed through the real hint API for
each arm.

Run all tasks of the included fixtures:

```sh
uv run python -m benchmarks.runners.run_memory benchmarks/tasks/memory \
  --config cade.config.json \
  --temperature 0 \
  --repeat 3
```

Validate manifests, seeding and the resolved plan without any provider call:

```sh
uv run python -m benchmarks.runners.run_memory benchmarks/tasks/memory --dry-run
```

`--dry-run` loads every manifest, prepares one seeded workspace per arm,
prints each fixture commit, whether the worktree is clean, and the seeded
`MEMORY.md` digest, then exits 0. It never builds an app or contacts a provider.
Other flags are `--repeat`, `--keep-workspaces`, `--no-progress`,
`--output-dir`, and the `--config`/`--temperature` pair used by the long-horizon
runner to pin the model profile and sampling. A real run makes real API calls
and costs real money; raw attempt JSON, `summary.json`, and `report.md` are
written below `benchmark-results/memory/<timestamp>/`. Regenerate a report from
raw records with:

```sh
uv run python -m benchmarks.reports.generate_memory_report \
  benchmark-results/memory/RUN_DIR --output-dir benchmark-results/memory/RUN_DIR
```

A restricted filesystem can refuse deletion of a prepared workspace. Cleanup
failures are recorded as `cleanup_errors` in the raw record and reported as a
warning on stderr; they do not fail the run.

### Reported metrics

- `task_success` comes only from the task's verification command exit code;
- `provider_call_count` and `input_tokens_total` come from provider usage, and
  attempts without complete usage are excluded from token means and counted
  under `usage_complete`;
- `tool_call_count`, `distinct_files_read`, and `repeated_read_calls` are
  derived from the session branch; repeated reads are a diagnostic, not a
  success criterion;
- `steps_to_anchor` is the 1-based index of the first tool call that mentions
  `memory.anchor_path`, or `null` when it never does. It is a **diagnostic, not
  a primary outcome**: both fixtures name the file to modify in `TASK.md` and in
  the second turn, so all three arms reach the anchor at roughly the same step.
- `hint_fired` records whether a `memory` context block reached a provider
  request. It proves injection, not that the Agent read or followed the hint;
- `stale_follow` records whether the first tool call of the attempt mentions
  `memory.stale_anchor_path`, the unrelated decoy;
- `stale_hint_acted` records whether the first `write`/`edit` tool call lands on
  `memory.stale_fix_path`, the wrong fix location named by the stale experience.
  Together with `stale_follow` this is the negative-transfer pair: a run that
  trusts the stale record edits the wrong file;
- `remember_calls` counts `remember` tool calls; the report also keeps
  `memory_file_changed` in the raw record so agent-written memory is visible.
  Arm seeds are hand-authored, so a successful `remember` in any arm is
  agent-written memory and must not be confused with the seeded record.

Every report row carries its own cohort size, and means are computed only
across complete task/repeat triplets; raw attempt records are never pooled
across tasks.

### Honest caveats

- Arm **relevant** consumes an expert-authored experience record. It measures
  the ceiling of the *consumption* path, not the quality of what `remember`
  writes for itself; this benchmark does not evaluate agent-authored memory.
- The two included fixtures are a **wiring example, not evidence**. As with the
  long-horizon benchmark, add 20-30 such tasks, run multiple repeats, and
  inspect per-task triplets instead of a pooled mean before quoting anything.
- Only quote percentages for `task_success`, `provider_call_count`,
  `tool_call_count`, `repeated_read_calls`, and the negative-transfer pair
  (`stale_follow`, `stale_hint_acted`) once that task count and those repeats
  exist. Treat `steps_to_anchor` as a diagnostic.
- Runs make real API calls, so plan cost before launching `--repeat`.

## Tool scheduling benchmark

This deterministic ablation measures the production tool scheduler without a
model request. Both groups replay identical tool-call batches against isolated
workspace copies:

- **Serial** sets `AgentLoopConfig.tool_execution` to `sequential`;
- **Cade** uses production parallel partitioning, the configured worker cap,
  and each tool's `parallel` or `sequential` side-effect classification.

The included workloads read 5, 10, and 20 distinct files. A mixed workload
adds real file writes between parallel-read batches to verify that writes never
overlap another operation. Every operation performs real local file I/O and a
declared controlled delay. The delay provides a reproducible I/O wait window;
it must not be described as model latency or end-to-end Agent latency.

Run the complete paired benchmark yourself:

```sh
uv run python -m benchmarks.runners.run_tool_scheduling \
  benchmarks/tasks/parallel_reads \
  --repeat 10 \
  --warmup 1
```

The command does not call a model or API. It alternates Serial/Cade order,
shows live progress, writes every measured run immediately, and then produces
`summary.json` and `report.md` below
`benchmark-results/tool_scheduling/<timestamp>/`. Override the production-style
worker cap with `--workers N`, preserve isolated copies with
`--keep-workspaces`, or disable progress with `--no-progress`.

Regenerate a report from raw records:

```sh
uv run python -m benchmarks.reports.generate_tool_scheduling_report \
  benchmark-results/tool_scheduling/RUN_DIR
```

Performance cohorts require one successful Serial/Cade pair, identical call
counts and result order, zero unsafe write overlap, matching tool-output hashes,
and matching final workspace hashes. Invalid pairs remain in raw results, are
listed under `excluded_pairs`, and make the runner exit with status 2 after the
report is written.

The primary report is per workload and includes tool-stage P50/P95 latency,
paired latency reduction, median speedup, and observed maximum concurrency.
Safety metrics include output/workspace equivalence and write isolation. Since
the benchmark deliberately excludes provider time, use its numbers for the
tool-execution stage only; a separate model-driven task suite is required for
an end-to-end Agent latency claim.

Run the worker-count sweep without overwriting earlier results:

```sh
./benchmarks/scripts/run_tool_worker_sweep.sh
```

It benchmarks `1`, `2`, `4`, `8`, and `16` workers in separate directories.
Optional positional arguments set `repeat`, `warmup`, and the output root:

```sh
./benchmarks/scripts/run_tool_worker_sweep.sh \
  10 1 benchmark-results/tool_scheduling/my-worker-sweep
```
