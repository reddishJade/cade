# Permissions and review

`security` evaluates tools against execution modes, policies, and shell effects and coordinates user approval with automatic model review.

## Decision flow

```text
Tool arguments and targets
  → PermissionEngine
    → Static and path boundary policies
    → Shell syntax and effect analysis
    → Rule matching and constraint resolution
  → Verdict: ALLOW / DENY / REVIEW
    → Act: user approval
    → Build: AutoApprovalReviewer
  → GrantStore for supported authorization scopes
```

### Files

- [permissions.py](permissions.py): combine mode, grants, and evaluators into a permission decision.
- [shell_analyzer.py](shell_analyzer.py): analyze POSIX, PowerShell, and CMD syntax; extract paths, redirections, and potentially destructive or external effects.
- [approval.py](approval.py) and [approval_reviewer.py](approval_reviewer.py): `HITLDecision`, `HITLResult`, and independent automatic review.
- [rule_matcher.py](rule_matcher.py): wildcard and tool-specific rule matching. See the [architecture](../../../../docs/architecture.md#modes-permissions-and-execution-environment) for effective rule ordering.

## Invariants

Permission analysis authorizes actions and highlights risk; it is not OS isolation. Sandboxing belongs to the execution environment. Rule precedence must be deterministic so user overrides behave consistently. Permission logic does not depend on observability implementations. `GrantStore` owns grant persistence.

## Submodules

[permission_model/](permission_model/README.md) contains domain entities, `PolicyEvaluator` interfaces, and grant stores.
