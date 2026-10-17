# Application assembly

This package assembles configuration, infrastructure, tools, and security policies through dependency injection.

## Build flow

```text
resolve_config()
  → build_shared_infra(): sessions, History, cancellation
  → build_tool_registry(): built-ins and MCP schemas
  → build_security_rules(): permissions, mode rules, shell analysis
  → build_agent(): CodingAgentHarness
```

### Files

- [config.py](config.py): merge global, project, local, and environment settings into `ResolvedConfig`.
- [infra.py](infra.py): `build_shared_infra` creates `SharedInfra`, session storage, History, rollover, and cancellation services.
- [security.py](security.py): `build_shell_from_security`, `mode_rulesets_from_runtime_config`, and `permission_policy_from_security` construct shell and permission policies.
- [registry.py](registry.py): assemble workspace tools and a complete-schema MCP snapshot.
- [agent.py](agent.py): inject providers and external hooks and build `CodingAgentHarness`.

## Invariants

Keep factory modules stateless. Do not hold mutable globals or singleton references. Assemble from infrastructure upward; tool registries must not retain reverse references to `CodingAgentHarness`.
