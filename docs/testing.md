# Validation Guidelines

## Purpose and Primary Path

Use the repository [cade-e2e-test skill](../.cade/skills/cade-e2e-test/SKILL.md)
for the operational workflow and explicit provider configuration.

Validate the Cade workflows users actually run. Real tasks executed through
`cade exec` are the primary acceptance path for changes to the coding-agent
runtime. Use the installed entry point, real provider adapter and service,
actual application assembly, session storage, tools, and configured approval
and execution environment. Specify the task and environment before the run, and
judge the outcome against acceptance criteria written before implementation.

Choose validation by the affected behavior and concrete failure risk. The
breadth of a refactor determines how broadly to check references and startup;
user-visible behavior changes determine which workflows to exercise.

## Select the Smallest Sufficient Validation

| Change or risk | Required evidence |
|---|---|
| Documentation | Readability, current facts, valid links and commands, clean diff |
| Module moves, imports, interface cleanup with preserved behavior | Updated callers, static checks, affected entry-point startup; import checks for explicit dependency boundaries |
| Runtime, context, tools, permissions, sessions | A real `cade exec` task with externally checked results and relevant failure or recovery steps |
| Provider or MCP protocol | Real adapter and service execution, saved sanitized request/response evidence for the changed boundary |
| TUI input, completion, shortcuts, approval or rendering | Actual TUI driven through terminal automation or recorded manual steps |
| Web interaction, connection or session synchronization | Actual browser workflow against the running server |
| Precise crash, file corruption or security boundary | Isolated real files/processes, controlled fault injection, and externally observable recovery or protection outcomes |

State the protected behavior and selected checks before validation. Keep the
checks proportional to that behavior. Reuse a suitable existing scenario when
it covers the risk. Expand checks after relevant edits, failures, or new evidence.
Handle unrelated findings as separate work items with reproduction details.

## Design a Real Task

Choose a task from actual usage, an observed defect, or a representative coding
workflow. Include enough context and steps to expose the affected behavior.
For session changes, continue the task through a new process and verify the
restored history and subsequent results. For permissions, check both the
permitted result and the denied action's effects. For context changes, inspect
the persisted provider request and verify continued task execution.

Before running, record:

1. The concrete user task and failure being exercised.
2. Initial workspace contents, configuration, model, and required services.
3. The entry point appropriate to the validation layer and exact reproduction
   commands.
4. Expected files, command results, events, or recovery state.
5. An independent method to check those results and distinguish environment
   failure from a product defect.

Keep all test-managed workspaces, worktrees, sessions, temporary directories,
caches, and evidence inside the current Cade workspace, under a unique
`e2e-results/<run>/` directory where appropriate. Do not use `/tmp` or other
locations outside the workspace; results must survive external temporary-file
cleanup. Read existing account credentials through Cade without copying them.
Resolve paths and symlinks before creating or cleaning test directories.
Use isolated workspaces and dedicated accounts where appropriate. Keep sandbox
and approval settings representative of the behavior under review. Derive task
success from the resulting workspace, independently executed verification
commands, persisted session evidence, and required event/exit semantics. Treat
model completion claims and successful process exit as supporting evidence.

For network isolation, establish connectivity to a controlled endpoint outside
the sandbox before checking the sandboxed connection. For environment probes,
record the precise prerequisite failure and report validation as blocked.

## Requirements for Reusable Test Code

Adding or extending reusable test code requires a documented coverage gap and
all five elements
listed above. Explain how a realistic defect would fail its assertions and how
a behavior-preserving refactor would keep them valid. Each test must protect a
user outcome or an explicit public contract. Keep harness code small and preserve
production boundaries when arranging dependencies.

Use real execution through the relevant host for workflow acceptance. Classify
checks by the path they actually exercise:

- E2E: public host entry point through the real provider/service, runtime,
  storage, tools, and relevant terminal or browser environment.
- Integration: real connected components with controlled external protocol
  responses, asserting a specific boundary. Drive real adapters and record the
  source of protocol examples used for expectations.
- Golden: small sanitized real payloads or visible output with independently
  reviewed expectations and a documented source.
- Focused regression: controlled reproduction of a specific security,
  persistence, crash, or corruption failure, with observable outcome assertions.

A replacement for an entire Provider exercises the downstream application from
Cade's internal events; record that coverage as application integration.
Validate the provider protocol separately through its real adapter. Base
assertions on behavior and contracts that users depend on. Treat widget labels,
private fields, helper calls, and object layouts as implementation choices unless
the task explicitly defines them as a contract.

Review new test code against its coverage gap before retaining it. Keep each
assertion tied to that gap. Review expectation updates against the intended
behavior and the original evidence. Select automated regression code when it
provides repeatable protection; retain reproducible manual evidence when that
is the appropriate validation method.

## Evidence and Reporting

Save run commands, initial conditions, configuration/model identifiers, exit
status, JSONL events, relevant session records, file changes, verification
results, and key observations in ignored result directories such as
`e2e-results/`. Record failed attempts and environment blockers alongside
successful runs. Document any repeated model runs and judge each against the
same acceptance criteria.

Commit reusable scenario sources, run instructions, and small sanitized fixtures.
Store generated traces, logs, screenshots, recordings, and bulk captures locally
or in CI artifact storage. Sanitize account data and credentials before sharing.
Report exactly which behavior each check establishes and its remaining coverage.

## Commands and Current Repository State

Current validation uses selected static checks, startup checks, and recorded
workflow runs. CI runs pytest when it finds a `test_*.py` file under
`src/cade/tests/` and skips that step when the directory has no tests. Validate
each command against available sources and report CI results according to the
checks that actually executed.

Static and startup checks:

```sh
uv run ruff check src/
uv run ruff format src/ --check
uv run pyright src/
uv run cade --help
```

For a runtime acceptance run, prepare an isolated workspace and a task-specific
prompt, then use the public entry point:

```sh
CADE_E2E_RUN="$PWD/e2e-results/unique-run"
TMPDIR="$CADE_E2E_RUN/tmp" UV_CACHE_DIR="$PWD/.uv-cache" \
uv run cade exec --project-root "$CADE_E2E_RUN/workspace" \
  --config "$CADE_E2E_RUN/run.config.json" \
  --sessions-dir "$CADE_E2E_RUN/sessions" \
  --approval auto-review --event-format jsonl \
  --max-steps 20 --max-llm-calls 30 --timeout 5m \
  --output-last-message "$CADE_E2E_RUN/answer.md" \
  --prompt-file "$CADE_E2E_RUN/task.md" > "$CADE_E2E_RUN/events.jsonl"
```

Prepare the run directory, its `tmp/` and workspace subdirectories, prompt,
and explicit provider configuration before this command. Preserve existing
global API settings; ordinary acceptance runs use the available Codex login as
described in the skill. Select approval and sandbox settings for the scenario, and run the independent
verification commands after Cade finishes. For behavior-preserving import
changes, check affected imports and startup directly. For documentation-only
changes, review the documentation and diff.
