# Repository Guidelines

## Engineering Principles

- For new designs, study how established products solve the relevant problem
  and adopt proven patterns that fit the requirements.
- Choose the simplest implementation that fully meets the current requirements.
  Avoid speculative abstractions, configuration, and indirection.
- Build in working, end-to-end increments, starting with the smallest complete
  version. Keep each increment usable and suitable for long-term extension;
  avoid temporary workarounds intended to be replaced. Do not remove existing
  working functionality to make way for an unfinished complex design.
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

For implementation tasks, implement the change, validate it, and fix failures
caused by it until the requested behavior is verified or a concrete blocker is
identified. Report unrelated failures without expanding scope.
Use HUMAN.md for approval boundaries and review checkpoints.

## Project Structure & Architecture

Cade is a Python 3.12+ coding-agent harness. All package code lives in
`src/cade/`; the command-line entry point is `src/cade/main.py`. The layered
design is `ai/` (provider adapters), `agent/` (loop and context), `harness/`
(runtime, sessions, policy, MCP), `coding_agent/` (tools), and `cli/` (REPL and
TUI). Tests reside in `src/cade/tests/`; documentation and examples are in
`docs/` and `examples/`.

## Coding Style & Naming

Use complete type annotations and standard four-space Python indentation.
Ruff enforces formatting with an 88-character line length; do not add `# noqa`
or blanket exception handlers. Prefer small, single-purpose functions.
Use `snake_case` for modules, functions, and variables; `PascalCase` for classes;
and `test_<feature>.py` for test modules. Write comments and docstrings in
Simplified Chinese.

## Task-Specific References

Read the relevant reference when its condition applies, not the entire list:

- Before implementing behavior changes or adding tests, use
  [Testing Guidelines](docs/testing.md) for test design, artifacts, and commands.
  Choose validation by scope and risk; documentation-only edits do not require
  Python checks.
- For environment setup, repository skill changes, or preparing commits and PRs,
  read the matching section of [Development Guide](docs/development.md).
- For architecture changes, consult [Architecture](docs/architecture.md).
- For code reviews, use [Review Standards](docs/review-standards.md).
