# Validation Guidelines

## Purpose and Validation Choice

Choose the smallest sufficient checks that directly verify the changed behavior
and concrete failure risk. Prefer focused regression for local deterministic
computation and policy changes, and integration checks for connected components.
Do not infer a need for E2E from module ownership, patch size, or the mere presence
of a behavior change.

Select E2E when a material risk depends on host assembly, real provider/service
behavior, cross-process recovery, or a complete user workflow and lower-level
checks cannot cover it. Use the relevant public host (`cade exec`, TUI, or web)
and independently check the outcome. Small changes can still require E2E when
they affect such a boundary.

After selecting a real-provider E2E check, use the internal acceptance tools and
an explicit provider configuration. Account-driven runbooks and capability
evaluation tasks live outside this repository. Their location does not determine
whether E2E is needed; select it from the risk and coverage gap described here.

## Select the Smallest Sufficient Validation

| Change or risk | Required evidence |
|---|---|
| Documentation | Readability, current facts, valid links and commands, clean diff |
| Module moves, imports, interface cleanup with preserved behavior | Updated callers, static checks, affected entry-point startup; import checks for explicit dependency boundaries |
| Local deterministic computation or policy | Focused regression with independently checked expectations and relevant edge cases |
| Connected runtime, context, tool, permission or session components | Integration through the real affected components; controlled external boundaries where appropriate |
| Provider or MCP parsing and serialization | Protocol integration through the real adapter using sourced payloads; real service checks when captured examples cannot establish the affected semantics |
| Host/service/workflow risk not covered by lower-level checks | Relevant E2E with externally checked results and failure or recovery steps |
| TUI terminal input, focus or display behavior not covered by component checks | Actual TUI driven through terminal automation or recorded manual steps |
| Browser interaction or connection behavior not covered by component checks | Actual browser workflow against the running server |
| Precise crash, file corruption or security boundary | Isolated real files/processes, controlled fault injection, and externally observable recovery or protection outcomes |

State the protected behavior and selected checks before validation. Keep the
checks proportional to that behavior. Reuse a suitable existing scenario when
it covers the risk. Expand checks after relevant edits, failures, or new evidence.
Handle unrelated findings as separate work items with reproduction details.

Before preparing or running E2E, identify the concrete risk left uncovered, why
it requires the real host or service, and which observable result will expose a
failure. If no additional risk can be identified, do not add E2E as a ritual.
For bug fixes, prefer a regression that fails with the defect and passes after
the fix; a general smoke task that also passes with the defect is not evidence
that the defect was corrected.

For example, token counting and budget admission can be verified
deterministically; calibrating an estimate against a model's actual usage
requires real provider usage. A model-driven file-copy task does not establish
token-count accuracy.

## Design a Selected Workflow Check

For workflow acceptance, choose a task from actual usage, an observed defect,
or a representative coding workflow. Include enough context and steps to expose
the affected behavior.
For session changes, continue the task through a new process and verify the
restored history and subsequent results. For permissions, check both the
permitted result and the denied action's effects. For context changes, inspect
the persisted provider request and verify continued task execution.

Before running a workflow acceptance check, record:

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

Adding or extending reusable test code requires a documented coverage gap,
inputs and prerequisites, a reproduction command, expected results, and an
independent verification method. Require workspaces, provider configuration and
session evidence only when the check exercises those boundaries. Explain how a
realistic defect would fail its assertions and how a behavior-preserving refactor
would keep them valid. Each test must protect a user outcome or an explicit
public contract. Keep harness code small and preserve
production boundaries when arranging dependencies.

When E2E is selected, use real execution through the relevant host. Classify
checks by the path they actually exercise:

- E2E: public host entry point through the real provider/service, runtime,
  storage, tools, and relevant terminal or browser environment.
- Integration: real connected components, with controlled external boundaries
  where needed, asserting an explicit component contract. For protocol checks,
  drive real adapters and record the source of examples used for expectations.
- Golden: small sanitized real payloads or visible output with independently
  reviewed expectations and a documented source.
- Focused regression: controlled reproduction of a specific defect or contract
  violation, including deterministic computation, security, persistence, crash,
  or corruption failures, with observable outcome assertions.

A replacement for an entire Provider exercises the downstream application from
Cade's internal events; record that coverage as application integration.
When the provider protocol is affected, validate it separately through its real
adapter. Base assertions on behavior and contracts that users depend on. Treat
widget labels, private fields, helper calls, and object layouts as implementation choices unless
the task explicitly defines them as a contract.

Review new test code against its coverage gap before retaining it. Keep each
assertion tied to that gap. Review expectation updates against the intended
behavior and the original evidence. Select automated regression code when it
provides repeatable protection; retain reproducible manual evidence when that
is the appropriate validation method.

## Evidence and Reporting

For workflow acceptance, save run commands, initial conditions,
configuration/model identifiers, exit status, JSONL events, relevant session
records, file changes, verification results, and key observations in ignored
result directories such as `e2e-results/`. Record failed attempts and environment
blockers alongside successful runs. Document any repeated model runs and judge
each against the same acceptance criteria.

Commit deterministic product regression and integration tests with small
sanitized fixtures here. Maintain account-driven acceptance scenarios, capability
evaluation tasks, and their runbooks in the internal repository. Store generated
traces, logs, screenshots, recordings, and bulk captures in local or access-controlled
storage. Public CI artifacts are not private storage. Sanitize account data and
credentials before sharing.
Report exactly which behavior each selected check establishes and the remaining
uncovered risks. A check not required for the selected scope is not a blocker.

## Commands and Current Repository State

Current validation uses selected static checks, startup checks, and recorded
workflow runs. CI runs pytest when it finds a `test_*.py` file under
`src/cade/tests/` and skips that step when the directory has no tests. Validate
each command against available sources and report CI results according to the
checks that actually executed.

Product static checks, regression tests, and startup check:

```sh
uv run --locked ruff check src/cade/
uv run --locked ruff format --check src/cade/
uv run --locked pyright src/cade/
uv run --locked pytest -q src/cade/tests
uv run --locked cade --help
```

These checks do not depend on the internal evaluation repository.

When exec E2E is selected, prepare an isolated workspace and a task-specific
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
global API settings. Select the provider, model, reasoning settings, and auxiliary
roles explicitly for the chosen scenario. Select approval and sandbox settings
for the scenario. Run the independent verification commands after Cade finishes. For behavior-preserving import
changes, check affected imports and startup directly. For documentation-only
changes, review the documentation and diff.
