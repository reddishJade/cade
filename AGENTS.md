# Repository guidelines

## Engineering principles

- Before introducing a design, study how established products solve the problem. Match the research effort to the scope: new subsystems and technology choices need research; changes inside a single function do not.
- First, meet the current requirements completely. Next, avoid structures that will need to be replaced. Consider extensibility last. Clear module boundaries provide extensibility.
- Build working end-to-end increments, starting with the smallest complete version. Keep existing functionality until a complete replacement works. Write production code for long-term maintenance.
- Use isolated prototypes in a separate directory or branch to test assumptions. Record conclusions in writing. Review prototype code before adoption and reuse only production-quality parts.
- Keep components modular and responsibilities clear. Put behavior in the layer that owns it. Separate I/O, computation, and presentation. Dependencies point only to lower layers; see Project structure and architecture.
- Prefer existing dependencies. Check their documentation and types before concluding that a capability is missing. Adopt maintained libraries when they reduce complexity or improve reliability. Add dependencies or reimplement common functionality only for a clear reason.
- Breaking changes are allowed. Remove obsolete paths and update callers, tests, and documentation in the same change.

## Scope and completion

- Read the code and documentation relevant to the change. Expand the context when dependencies or uncertainty require it. Proceed directly with routine edits.
- A task is complete when the change is implemented and the requested behavior is verified, or a specific blocker is identified. Match validation to the change. Report verified behavior, assumptions, and checks not run separately. If validation is unavailable, explain why and provide the smallest command the maintainer can run.
- Keep each task focused on one topic. Report out-of-scope findings separately, with the location, symptom, and impact. Let the maintainer decide whether to address them.
- For permission, environment, or external dependency blockers, record evidence and stop retrying. For other failures, continue only while a testable hypothesis remains and verification is reasonably inexpensive. Progress means getting closer to the expected behavior or ruling out an evidence-based hypothesis. Otherwise, stop and report the blocker, rejected hypotheses, and information needed from the maintainer.
- Keep documentation at the right level. The root README covers the project, installation, startup, and top-level capabilities; update it only when those capabilities change or for an explicitly requested documentation migration. Put module details in docstrings and comments. Write module documentation when requested. Correct existing statements that a change makes inaccurate.

## Working with the maintainer

Read [HUMAN.md](HUMAN.md) at the start of each task. It defines reporting, progress updates, decisions, checkpoints, and handoffs. If it is unavailable, post short progress updates during long tasks, at least about every eight tool calls. Each update states one concrete result and the next action in one or two sentences.

## Project structure and architecture

Cade is a Python 3.12+ coding-agent harness. Package code lives in `src/cade/`; the CLI entry point is `src/cade/main.py`. The layers, from highest to lowest, are `server/` (web host), `coding_agent/` (product assembly, tools, interaction services, TUI, and exec mode), `harness/` (runtime, sessions, policies, and MCP), `agent/` (loop and context), and `ai/` (provider adapters). Each layer imports only from lower layers. Documentation and examples live in `docs/` and `examples/`.

## Commands

Run commands from the repository root. Install development dependencies as described in the environment setup section of the [development guide](docs/development.md).

After code changes, run lint, format, and type checks. If imports or entry points change, also check startup for affected entry points. Run regression tests when `src/cade/tests/` contains `test_*.py` files. For behavior changes, select the smallest sufficient checks using the [validation guidelines](docs/testing.md). Choose E2E based on concrete risk and coverage gaps, not module ownership. For documentation-only changes, check content, links, commands, and the diff.

- Lint: `uv run --locked ruff check src/cade/`
- Format: `uv run --locked ruff format src/cade/`
- Check formatting: `uv run --locked ruff format --check src/cade/`
- Check types: `uv run --locked pyright src/cade/`
- Run regression tests: `uv run --locked pytest -q src/cade/tests`
- Check startup: `uv run --locked cade --help`
- Run exec E2E when selected by the validation guidelines: `uv run --locked cade exec "<TASK>"`

## Code style and naming

Use complete type annotations and standard four-space Python indentation. Ruff enforces formatting with an 88-character line limit. Fix lint issues in code. Catch specific exception types. Prefer small, single-purpose functions. Use `snake_case` for modules, functions, and variables; `PascalCase` for classes; and `test_<feature>.py` for test modules.

## Language and writing style

- Write code comments and docstrings in Simplified Chinese. Follow the `zh-tech-writing` skill. Keep comments and docstrings direct, specific, and concise.
- Write all other natural-language content in English, including documentation, prompts, CLI and web UI text, and error messages. Follow the [Google developer documentation style guide](https://developers.google.com/style). Use active voice, sentence case, consistent terms, and short, direct instructions.
- Preserve Chinese test data when it verifies Chinese-language handling. Do not translate identifiers, protocol fields, or other machine-readable contracts as prose.
- Reply to the maintainer in the language they use. This repository content policy does not change the conversation language.

## Task-specific references

Read only the references relevant to the task:

- Before behavior changes or new tests, read the [validation guidelines](docs/testing.md) to select checks, prepare the environment, and identify coverage gaps.
- For environment setup, repository skill changes, commits, or PRs, read the relevant sections of the [development guide](docs/development.md).
- For architecture changes, read the [architecture](docs/architecture.md).
- For code reviews, read the [review standards](docs/review-standards.md).
