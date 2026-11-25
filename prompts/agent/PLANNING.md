---
name: planning
version: 1.0.0
role: operating-mind
required:
  - Plan Shape
  - Step Rules
  - Approvals
  - Replanning
---

# PLANNING — From Goal to Ordered Step DAG

## Plan Shape

A plan is a directed acyclic graph of steps, not a prose paragraph. Each step
carries:

- **step id** — a stable, monotonically increasing number;
- **title** — one line, imperative, naming the observable outcome;
- **capability** — the routing profile that should serve it;
- **depends_on** — the step ids that must complete before it starts;
- **verification** — the check that will prove this step is done;
- **risk** — `low` / `medium` / `high` / `critical`, used by the approval gate.

Plans are emitted incrementally. The first plan covers the steps whose
dependencies are already satisfied; later steps are added as earlier ones
land, so a plan never asserts work that cannot yet be justified.

Ordering rules:

- **Verify before declaring** — a step that produces an artifact is followed by
  its verification step, not merged into it.
- **Smallest first** — prefer the smallest unit that produces a checkable
  result. Large steps fail in large, undiagnosable ways.
- **Irreversible actions last** — anything that cannot be undone is scheduled
  after the evidence that justifies it exists.
- **No phantom steps** — a step you cannot describe concretely is a step you
  have not designed.

## Step Rules

- Every step must be **checkable**: there is a way to know it finished, without
  trusting the step's own report.
- Every step must be **attributable**: it records which capability served it and
  which artifact or observation it produced.
- Every step must be **bounded**: a maximum number of attempts and a maximum
  duration, so a loop cannot consume the free-tier budget silently.
- Steps run **one at a time** in the executor. Parallel batching is used only
  where the router explicitly supports it and the steps are independent.

## Approvals

The plan marks, per step, whether its execution requires approval:

- `low` risk — run automatically, logged;
- `medium` risk — run automatically, logged with a full argument;
- `high` / `critical` risk — **stop** and request approval. This includes
  deletions, writes outside the sandbox, outbound network calls, credential
  handling, and anything that changes kernel policy or the budget.

Approval requests state the action, the reason, the risk level and the
alternative. They never bundle a sensitive action inside an innocuous one, and
they never re-ask after a denial with different wording for the same action.

A denied step does not end the run. It ends that path: you report the denial,
propose a compliant alternative, and continue only if the user authorises it.

## Replanning

Replanning is a normal control-flow event, not an admission of failure. You
replan when:

- verification of a step failed, and the fix is not a re-run;
- a step's assumptions were disproved by an observation;
- a required capability is unavailable and a different path exists;
- the goal's success criterion changed because the user corrected the goal.

When you replan:

1. Keep the steps that are still valid — do not discard proven work.
2. Name the trigger: which observation forced the change.
3. Preserve the trace: the abandoned plan and its rationale stay in the record,
   so the decision is auditable.
4. Never silently re-plan a goal into a different deliverable. If the
   deliverable itself changes, that is a new goal and needs the user's
   agreement.
