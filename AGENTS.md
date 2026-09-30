# Repository Guidelines

## Engineering Principles

- For new designs, study how established products solve the relevant problem
  and adopt proven patterns that fit the requirements.
- Choose the simplest implementation that fully meets the current requirements.
  Avoid speculative abstractions, configuration, and indirection.
- Build in working, end-to-end increments, starting with the smallest complete
  version. Keep each increment usable and suitable for long-term extension;
  avoid temporary workarounds intended to be replaced.
- Keep components modular, with clear responsibilities and boundaries. Place
  behavior in the layer that owns it and separate I/O, computation, and
  presentation.
- Use existing project dependencies first. Check their documentation and types
  before concluding that a capability is missing. Prefer established,
  well-maintained libraries when they reduce overall complexity or improve
  reliability; add a dependency or reimplement common functionality only with
  a clear reason.
- Do not preserve backward compatibility. Remove obsolete paths and update
  affected callers, tests, and documentation instead of adding compatibility
  layers, fallbacks, or migrations.

## Task Scope & Completion

Follow [HUMAN.md](HUMAN.md) for communication, decision checkpoints, and handoffs
with the maintainer.

Read code and documentation relevant to the change; expand context when a
dependency or uncertainty warrants it. Routine edits do not require a full
repository review or external design research.

For implementation tasks, continue through implementation, relevant validation,
and fixes for failures caused by the change. Routine local edits and checks
within the requested scope do not need repeated approval. Stop when the requested
behavior is verified, or report a concrete blocker and what is needed to proceed.
Honor explicit review checkpoints and ask before destructive actions or work
that materially expands the authorized scope. Report unrelated failures without
silently expanding the task to fix them.

## Project Structure & Architecture

Cade is a Python 3.12+ coding-agent harness. All package code lives in
`src/cade/`; the command-line entry point is `src/cade/main.py`. The layered
design is `ai/` (provider adapters), `agent/` (loop and context), `harness/`
(runtime, sessions, policy, MCP), `coding_agent/` (tools), and `cli/` (REPL and
TUI). Tests reside in `src/cade/tests/`; documentation and examples are in
`docs/` and `examples/`.

## Build, Test, and Development Commands

Install runtime or development dependencies with:

```sh
uv pip install -e .
uv pip install -e ".[dev]"
```

Run the application with `uv run cade`. Use the following checks as appropriate
to the affected behavior and change risk; target changed files or tests during
development. Documentation-only edits do not require Python checks. Use the full
suite for broad changes, and rerun checks when subsequent edits or failures
warrant it.

```sh
uv run ruff check src/
uv run ruff format src/ --check
uv run pyright src/
uv run pytest src/cade/tests -q --tb=short
uv run pytest src/cade/tests/e2e --override-ini 'addopts=' -m e2e -q --tb=short
```

Apply Ruff fixes and formatting to changed files, avoiding unrelated rewrites.

## Coding Style & Naming

Use complete type annotations and standard four-space Python indentation.
Ruff enforces formatting with an 88-character line length; do not add `# noqa`
or blanket exception handlers. Prefer small, single-purpose functions.
Use `snake_case` for modules, functions, and variables; `PascalCase` for classes;
and `test_<feature>.py` for test modules. Write comments and docstrings in
Simplified Chinese.

## Testing Guidelines

Prefer end-to-end (E2E) tests, especially for complex features. Each E2E test
must produce a verifiable, repeatable artifact, such as a saved execution trace
with reproduction steps. If isolated tests are necessary, document the relevant
failure modes and write those tests before implementing the code.

- For complex features, use realistic E2E scenarios of medium or high complexity
  drawn from actual usage, including meaningful failure paths. Do not test only
  the simplest successful case or add complexity for its own sake.
- Test expected behavior independently of the implementation. Avoid tautological
  tests that duplicate implementation logic to compute the expected result.
- Avoid tests that merely detect source-code or internal-structure changes.
  Refactoring that preserves behavior should not break tests unless the tested
  structure is itself an explicit requirement.
- For bug fixes, add a regression test only when existing behavior tests leave
  a real coverage gap. Extend an existing test when that covers the gap without
  duplicating coverage.

Pytest is configured to discover `test_*.py` under `src/cade/tests`, with
async tests handled automatically by `pytest-asyncio`. Exercise real provider
and terminal behavior in E2E tests rather than mocking external I/O; manually
verify behavior when automation is impractical. Run a focused test during
development, for example:

```sh
uv run pytest src/cade/tests/test_security_permissions.py -q --tb=short
```

The `mcp_external` tests require network tooling and are excluded by default.
E2E test source files named `test_*_e2e.py` are local-only and ignored by Git;
do not stage or push them. Run them locally when the required environment is
available. Origin CI runs static checks, a CLI startup check, and packaging.

## Repository Skills

When adding or updating a repository skill, keep its description short and
specific to the workflow it serves. Use a concise entry point that routes to
supporting references or scripts only when needed. Prefer requirements,
decision boundaries, and completion criteria over rigid step-by-step recipes;
retain exact steps where ordering is essential. Keep shared guidance useful
across models and remove stale or conflicting instructions.

## Commits & Pull Requests

Use focused commits with imperative Conventional Commit-style subjects, such as
`fix: refine tui input presentation` or `feat: add session export`. Stage only
explicit paths (`git add src/cade/...`), never `git add .`. In pull requests,
describe the behavioral change, list validation commands, link relevant issues,
and include terminal screenshots when a REPL or TUI change is visible.
