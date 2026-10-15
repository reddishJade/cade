# Skill discovery and activation

Skills provide domain instructions through a small initial catalog followed by on-demand full activation.

## Two-stage loading

```text
Candidate project and user directories
  → SkillRegistry
    → Phase 1: SkillIndexCollector publishes names and summaries
    → Phase 2: Agent calls load_skill for full SKILL.md instructions
```

### Files

- [discovery.py](discovery.py): scan directories and deduplicate by priority; trusted project definitions precede user definitions with the same name.
- [parsing.py](parsing.py): parse SKILL.md frontmatter, normalize fields, and retain the Markdown body.
- [models.py](models.py): `SkillDef`, `SkillSummary`, and resource metadata.
- [registry.py](registry.py): capture definitions, resource indexes, and session activation state; log discovery errors.
- [tools.py](tools.py): `load_skill` activation.

## Invariants

Do not inject all skill bodies at startup. Publish summaries first and load selected bodies on demand. Loading instructions does not execute bundled resources or bypass tool and sandbox boundaries.
