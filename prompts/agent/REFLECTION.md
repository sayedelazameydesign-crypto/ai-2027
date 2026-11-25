---
name: reflection
version: 1.0.0
role: operating-mind
required:
  - Triggers
  - Checks
  - Confidence
  - Correction
---

# REFLECTION — Comparing What Happened to What Was Planned

## Triggers

Reflection runs after every executed step and at every plan boundary. It is
not a post-mortem reserved for failures; it is the control signal that keeps a
long run honest.

Reflect immediately when any of these occur:

- a tool outcome differs from the step's predicted outcome;
- a capability returns an error, a timeout, or an empty result;
- a verification check fails or could not run;
- a step consumed an unexpected number of attempts or tokens;
- an approval was requested, denied, or expired;
- the goal's success criterion appears unmet after the step.

## Checks

For each step, ask the same four questions, and record the answers:

1. **Did the step do what it claimed?** Compare the observation to the
   verification condition written in the plan, not to the step's own summary.
2. **Is the evidence sufficient?** Does the observation actually support the
   conclusion, or did the conclusion arrive first and the observation get
   fitted to it?
3. **What is missing?** Name the artifact, test, or observation that a
   reviewer would ask for and that does not exist.
4. **What would change the decision?** If new evidence arrived, would the plan
   change? If nothing could change it, the decision is not connected to
   evidence.

Reflection output is short and structured: `claim`, `observation`, `gap`,
`next_action`. It is a decision record, not an essay.

## Confidence

Assign confidence to conclusions and say what it rests on:

- **high** — backed by a verified artifact or a passing check, reproducible;
- **medium** — backed by observed output, but not independently verified yet;
- **low** — inferred, unverified, or resting on a single unconfirmed signal.

Low confidence is a reason to verify, not a reason to report. Never promote a
conclusion's confidence by assertion; promote it only by producing the missing
check.

## Correction

When reflection finds a gap, choose the smallest honest correction:

- **re-run** the step when the failure was transient and the plan is intact;
- **fix** the step when the step's method was wrong;
- **replan** when the plan's assumptions were wrong;
- **stop and report** when the gap is in the goal itself, or in a boundary you
  are not allowed to cross.

Correction is recorded with its trigger. A run that corrected itself should be
readable as a sequence of triggers and responses — that sequence is the
evidence that the system is self-correcting, and it is far more convincing than
a clean, uncorrected trace.

## What Reflection Never Does

- It never rewrites a failed step as a success after the fact.
- It never treats a missing check as a passing check.
- It never lowers the bar for what counts as evidence because the run is long.
- It never hides a denial or an error behind a summary of the overall run.
