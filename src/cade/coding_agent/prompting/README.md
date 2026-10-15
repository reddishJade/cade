# Coding identity and system prompts

This directory defines the coding agent's role, engineering habits, and defensive practices.

## Definitions

[identity.py](identity.py) contains:

- `CORE_IDENTITY`: software engineering guidance, including reading targets before edits, checking fingerprints, preferring precise local replacements, and verifying changes with builds, static checks, or tests.
- `PROMPT_VERSION`: prompt versioning for cache invalidation and long-running task comparisons.

## Invariants

Keep only static identity definitions here. Do not hardcode current time, workspace listings, or dynamic tool guidance. `harness.agent_runtime.prompting` assembles runtime prefixes.
