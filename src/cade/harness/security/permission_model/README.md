# Permission domain model

This package converts tool arguments into actions, targets, and rules and defines policy evaluation and grant storage.

## Evaluation flow

```text
Tool name and arguments
  → ActionExtractor: action intent and target
  → PolicyEvaluator pipeline
    → Static policy
    → Path boundary
    → Shell analysis
    → Structured boundary
  → PermissionResolver and GrantStore
  → Verdict: ALLOW / DENY / REVIEW
```

### Files

- [types.py](types.py): `Action`, `Target`, `Rule`, `Constraint`, and `Verdict` enums and data classes.
- [action.py](action.py): `TargetFingerprint` extraction and `compute_approval_candidate`.
- [evaluators.py](evaluators.py): `StaticPolicyEvaluator`, `PathBoundaryPolicyEvaluator`, and `ShellAnalysisPolicyEvaluator`.
- [stores.py](stores.py): `InMemoryGrantStore` for nonpersistent grants and `FileGrantStore` for persistent user grants.
- [resolver.py](resolver.py): `PermissionResolver` combines evaluation results.

## Invariants

Evaluation must not persist or mutate external grants. Normalize target fingerprints consistently, including resolved paths and platform case rules, so equivalent path forms do not bypass permissions.
