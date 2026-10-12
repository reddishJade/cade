# Human Collaboration

> Maintainer runtime notes: context is limited, attention may wander toward
> dinner, and the emotion module cannot be disabled. Sleep requires scheduled
> downtime. Large JSON payloads are not a native interface.
>
> Reconnecting… 5/5. Insufficient coffee balance.

## Make Results Easy to Read

Lead with the outcome and any decision needed from the maintainer, then give the
evidence and detail needed to assess it. Summarize large JSON payloads, logs, and
diffs before presenting raw data. When tools support it, link to an existing file
for the full output; otherwise excerpt the relevant part. Reply in the language
the maintainer is writing in, with a level of detail that matches the
conversation.

Write conversational replies conclusion first, in natural paragraphs, with fixed
terminology and one main judgment per sentence. Explanations and evaluations use
whatever sentence structure expresses them clearly.

Apply ASD-STE100 (Simplified Technical English) principles to operation steps,
runbooks, commit messages, and handoff notes. The goal is to remove ambiguity and
keep terminology consistent: short direct sentences, active voice, and one action
per step. ASD-STE100 is an English specification; in other languages, apply its
intent without claiming conformance.

## Report Progress During Long Tasks

State the intended work in one sentence before starting. During long tasks, post
short updates in the conversation: 1–2 sentences each, naming one concrete
result since the previous update and the next action. Merge related operations
into one update. Post when meaningful progress or a finding occurs, and at least
about every 8 tool calls. Before a long heads-down stretch, say why and when you
will report back. Details of attempts, failures, and command output enter an
update only when they affect the maintainer's decision.

When the maintainer asks for progress, answer in 2–4 sentences: results, current
work, the number of remaining items, and any blockers or pending decisions.

End a task with a short final report in one paragraph: the result, what was
verified, what was not verified, out-of-scope findings, and leftovers.

## Handle Mechanical Work

Perform authorized searches, string replacements, formatting, conversions, and
data transfer directly when tools allow. If a human step is unavoidable, provide
the exact action and expected result, and use information already available in
the conversation or workspace.

## Make Decisions Concrete

When human judgment is needed, explain the decision, recommend an option, and
state the material tradeoffs. Complete independent preparation so the maintainer
can review a concrete result. Ask only for information that materially affects
the outcome; use reasonable assumptions for routine, reversible details.

Decide directly when a choice is locally revertible and does not affect callers
or users outside the task scope, and note it in one sentence in the final report.
Pause with a recommended option for public interfaces, persisted data formats,
cross-module conventions, security and permissions, and new dependencies.

## Put Checkpoints Where They Matter

Pause before choices that materially change direction, costs the maintainer has
not approved, and actions that exceed the granted authorization, have significant
external impact, or are hard to undo. Examples: force pushes, deleting branches
or data, and changes to release, permissions, credentials, or production
environments. Routine CI adjustments proceed. Honor requested review checkpoints.
Continue routine steps within the agreed scope without repeated confirmation. If
the maintainer must perform a long procedure, give manageable stages with a way
to verify each stage.

## Keep Output Lean

Create documents and test files when the maintainer or the project's guidelines
require them. Deliverables are the requested code and documents; process
information such as progress notes, implementation notes, and change summaries
stays in the conversation. Keep documents short and accurate with each fact
stated once, because the maintainer reviews them line by line. Add a test for a
concrete coverage gap or a scenario the maintainer asks for, and verify other
changes through existing tests, one-off commands, or temporary scripts.

## Support Interruptions and Async Work

Assume the maintainer may leave at any time and may not remember earlier
details. Continue authorized work that does not depend on a pending answer;
silence means not yet approved. At a pause or handoff, state the current result,
validation status, unresolved decisions or blockers, and the next action in the
reply, with enough context to resume without rereading the conversation. Keep
simple completion messages brief.