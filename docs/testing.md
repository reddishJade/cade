# Testing Guidelines

## Quality Standards

Prioritize the Cade workflows users actually run. Use real E2E tests for complete
workflows, integration tests for data/API boundaries, and golden tests grounded
in real examples. The default suite includes deterministic integration and
golden tests plus necessary focused regressions. Do not test internal
object shapes, trivial calculations, or implementation details merely to
increase assertion counts.

- Prefer E2E tests for complex features, using realistic scenarios of medium or
  high complexity drawn from actual usage, including meaningful failure paths.
  Do not test only the simplest successful case or add complexity for its own sake.
- Each E2E test must produce a verifiable, repeatable artifact, such as a saved
  execution trace with reproduction steps.
- If isolated tests are necessary, document the relevant failure modes, write
  the tests, and then implement the code.
- Test expected behavior independently of the implementation. Avoid tautological
  tests that duplicate implementation logic to compute the expected result.
- Avoid tests that merely detect source-code or internal-structure changes.
  Refactoring that preserves behavior should not break tests unless the tested
  structure is itself an explicit requirement.
- For bug fixes, add a regression test only when existing behavior tests leave
  a real coverage gap. Extend an existing test when that covers the gap without
  duplicating coverage.

## Test Layers

### E2E

Tests in `src/cade/tests/e2e/` exercise complete user workflows through the real
application, provider, session storage, tools, terminal, and relevant external
services. Do not mock or replace dependencies along the tested execution path.
Use dedicated test accounts and isolated workspaces when required. Drive the
CLI or TUI through terminal automation; use Playwright for browser workflows.
Tests using deterministic provider protocol drivers are integration tests,
not full E2E tests. Document environment requirements and run instructions.
Each E2E test must save a JSON trace, reproduction commands, and key observations
in `e2e-results/`.

```sh
uv run pytest src/cade/tests/e2e --override-ini 'addopts=' -m e2e -q --tb=short
```

The default command excludes E2E tests because the required terminal, sandbox,
and provider environments are not available on every development machine:

```sh
uv run pytest src/cade/tests -q --tb=short
```

### Integration Tests

Exercise connected components at provider, MCP, tool, and session-storage
boundaries. Cover affected request and response schemas, serialization,
persistence and replay, streaming events, and error propagation. Use real
adapters and components; deterministic protocol drivers may replace external
services for repeatable checks. Handwritten doubles must not be the sole evidence
of an external API contract: check real service examples and validate against the
real service when that boundary changes.

### Golden Tests

Commit small, sanitized real examples of provider payloads, MCP responses,
session events, or user-visible output with reviewed expected results. Include
representative edge cases and record each fixture's source and protected behavior.
Compare behavior against these expectations. Normalize only volatile fields,
such as timestamps or generated IDs, without hiding meaningful schema or content
changes. Review golden updates as behavior changes; do not regenerate expected
results merely to make a failing test pass.

### Necessary Focused Tests

In addition to integration and golden tests, retain focused regressions for
behaviors E2E cannot safely or deterministically cover, including:

- Hard denials by security policy, approval scope, and path boundary violations.
- Session recovery after process crashes or torn writes.
- Permission protection for credential and session files.
- Memory file conflicts and durable provenance reads, including workspace
  ownership, navigation-cache loss, and missing evidence artifacts. Keep these
  regressions committed and runnable without a provider or sandbox service.

These tests must explain which failure modes E2E cannot cover safely or
deterministically. Assert security outcomes observable by users, rather than
intermediate values in private functions.

### External Dependency Validation

Manually validate real providers, terminal UI, MCP servers, and platform-specific
shell behavior as needed. An HTTP 200 response, a mock loader, or another service
instance cannot substitute for the execution path users actually run.

Commit reusable test sources, run instructions, and curated golden fixtures.
Keep generated traces, logs, screenshots, recordings, and bulk captures out of
Git. Store them in ignored result directories or CI artifact storage with a
retention period. Never commit credentials or unsanitized account data.

Run E2E tests locally or in configured CI when the required terminal, sandbox,
provider, and test accounts are available. Verify manually when automation is
impractical and record reproduction steps and evidence. Origin CI currently runs
only static checks, a CLI startup check, and packaging; a passing default suite
or CI run does not establish full E2E coverage.

Pytest discovers `test_*.py` under `src/cade/tests/`, with async tests handled
automatically by `pytest-asyncio`. The `mcp_external` tests require network
tooling and are excluded by default.

## Validation Commands

Choose checks based on the affected behavior and change risk. Target changed
files or behavior during development and use the full suite for broad changes.
Rerun checks only when subsequent edits or failures warrant it. Documentation-only
edits do not require Python checks. Apply Ruff fixes and formatting only to
changed files, avoiding unrelated rewrites.

```sh
uv run ruff check src/
uv run ruff format src/ --check
uv run pyright src/
uv run pytest src/cade/tests -q --tb=short
uv run pytest src/cade/tests/e2e --override-ini 'addopts=' -m e2e -q --tb=short
```

Example of a focused test:

```sh
uv run pytest src/cade/tests/test_security_permissions.py -q --tb=short
```

Existing Pyright warnings may be addressed separately, but new code must not
introduce additional errors.

## Change Requirements

- New session events must have encoding, persistence, and replay tests.
- New model inputs must appear in the `before_provider_request` hook envelope
  and change the persisted request fingerprint.
- New tool presentation must use typed intents and cover the shared projection
  in both hosts.
- New composition parameters must be covered by real `build_app()` tests.
- Test isolation of composition inputs after publication. Runtime replacements
  must produce a new generation.
- Test that child cold recovery does not expand tool capabilities and that
  control validates direct-parent ownership.
- Test that parent shutdown cancels and releases children first without deleting
  durable sessions.
- When removing or changing interfaces, update all callers and tests in the
  same commit without adding compatibility branches.
- Inject file and terminal dependencies through narrow local protocols without
  introducing remote execution assumptions.

## Incident Regressions

Each formal postmortem should identify an automated test that prevents recurrence.
Add or extend tests when coverage has a gap, and document the reason when only
manual validation is possible. Test names should describe the protected invariant,
not merely repeat the specific bug.
