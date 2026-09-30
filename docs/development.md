# Development Guide

Read the section relevant to the current task.

## Environment Setup

Install runtime or development dependencies with:

```sh
uv pip install -e .
uv pip install -e ".[dev]"
```

Run the application with `uv run cade`. For validation commands and test design,
see [Testing Guidelines](testing.md).

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
explicit paths (`git add src/cade/...`), never `git add .`. E2E sources under
`src/cade/tests/e2e/` and files named `test_*_e2e.py` are local-only; do not stage
or push them.

In pull requests, describe the behavioral change, list validation commands,
link relevant issues, and include terminal screenshots when a REPL or TUI
change is visible.
