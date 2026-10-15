# Incident reviews

This directory contains reviews of incidents affecting real user paths, violating core invariants, or exposing systemic validation gaps. The purpose is to improve the system, not assign personal blame.

## When to write a review

- Data is lost, history is rewritten, or recovery fails.
- Permissions, approval, or local execution boundaries are bypassed.
- Models receive unrecorded, incorrect, or unreconstructable context.
- Unit tests pass but the real `build_app`, process, or UI path fails.
- A recurring defect reveals a missing systemic constraint.

Use filenames in the form `NNNN-short-title.md`. Copy [TEMPLATE.md](TEMPLATE.md) and record verifiable facts, a timeline, violated invariants, why tests missed the defect, and corrective actions with owners and status. At least one action should add a regression test through the assembled components.
