# Incident title

Date: YYYY-MM-DD
Status: Draft
Severity: S1 | S2 | S3

## Summary

In three to five sentences, describe what happened, who was affected, the duration, and how service recovered.

## Impact

- User-visible impact:
- Data or security impact:
- Affected versions/platforms:
- Start and end times:

## Detection

State whether a user, monitoring, tests, or a developer first detected the incident. Include detection delay.

## Timeline

- HH:MM — Event
- HH:MM — Event

## Root cause

Describe the technical root cause and contributing conditions. Distinguish the immediate trigger, latent design defects, and process gaps.

## Violated invariant

Identify the violated invariant in `docs/architecture.md`. If it is missing, document it there first.

## Why tests did not catch it

Explain which surface was checked and which real loader, process, session, or UI path was missing.

## Resolution

Describe containment, repair, data recovery, and release.

## Corrective actions

| Action | Owner | Status | Validation |
| --- | --- | --- | --- |
| Add an assembled-component regression test | TBD | Open | Test path |
| Fix the root cause | TBD | Open | Test or observed evidence |

## Lessons

Record engineering conclusions applicable to other modules and any Agent Note to create or update.
