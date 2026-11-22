# CELIASKILL — Constitution

**Version 1.0.0 · Phase 1 · Proposed, not approved**

This is the constitution for the CELIA Skill Factory. It governs how a
skill moves from idea to published artefact, and it is written to be
checkable rather than convincing.

---

## Article 1 — What this factory is

A governed pipeline with three auditable records and one gate. It is not
an agent, not a runtime, and not an execution engine. It produces
**artefacts and their evidence**, and it refuses to produce them when the
evidence is incomplete.

| Component | Responsibility |
|---|---|
| `registry.events.jsonl` | Release and revocation events, hash-chained |
| `skills/drafts/{id}/events.jsonl` | Draft lifecycle, separately chained |
| Evidence manifests | Digests pinning what was approved |
| Release gate | Refuses release unless every condition holds |

---

## Article 2 — Non-execution

The factory **never executes** skill code. A `scripts/` directory may
exist inside a skill as **inert text**: it is hashed, stored, and reviewed
as content.

> **A script inside a skill is data, not a program.** The factory has no
> code path that spawns a process from skill content.

Any future execution happens outside this factory, through a registered
tool, under host permissions, and inside an execution sandbox. None of
that exists in Phase 1, and Phase 1 must not imply that it does.

---

## Article 3 — The five state labels

Every artefact carries exactly one label. The labels are literal.

| Label | Means | Does not mean |
|---|---|---|
| `PROVEN` | a command ran here and succeeded; result recorded | production-ready, or secure |
| `DECLARED` | the artefact exists; the command was never run here | that it works |
| `SIMULATED` | the declared behaviour is a simulation | live capability |
| `FAILED` | a specific, documented defect | hidden |
| `BLOCKED` | needs something that cannot run free or locally | an engineering failure |

**No artefact is promoted by description.** Promotion requires a recorded
command and a recorded result.

---

## Article 4 — Canonical bytes

A digest is only meaningful if the bytes are defined. Definitions:

1. UTF-8, no BOM.
2. JSON keys sorted ascending, no insignificant whitespace.
3. Integers only. **Floats are forbidden** — their textual form is not
   portable, so a digest over a float is not reproducible.
4. Duplicate keys are rejected at parse time.
5. One object per line, one LF terminator.

Any artefact type needing a ratio stores an integer plus a scale, or a
string. This is a restriction accepted deliberately in exchange for
portable verification.

---

## Article 5 — Event integrity

Every event carries:

| Field | Purpose |
|---|---|
| `sequence` | 1-based, contiguous; detects gaps and reorder |
| `previous_event_sha256` | links to the prior event; detects edit |
| `event_sha256` | digest of the event body; detects modification |

Plus an **anchor** outside the log holding `(sequence, head_sha256)`,
which detects tail truncation, rollback, and a consistent full rewrite.

**Declared limit:** an actor who can rewrite both the log and the anchor
can rewrite history consistently. This factory does **not** claim to
defeat that. It is labelled `BLOCKED` and requires an external trusted
anchor, which is out of Phase 1 scope.

---

## Article 6 — Draft lifecycle

A draft is a distinct object with a distinct log. Its states:

```
DRAFT → SUBMITTED → IN_REVIEW → APPROVED → PUBLISHED
             ↑            ↓
             └── CHANGES_REQUESTED
   any non-terminal ──→ ABANDONED
```

Rules:

1. Every transition appends one `DRAFT_TRANSITION` event.
2. A transition not in the declared table is rejected.
3. `PUBLISHED` and `ABANDONED` are terminal.
4. A draft log never writes to the release log. Publishing a draft emits a
   **separate** release event; it does not convert draft events.

---

## Article 7 — The release gate

A release is refused unless **all** of these hold:

| Condition | Failure code |
|---|---|
| Tests ran and passed | `TESTS_PASSED` |
| Current evidence digest equals the approved digest | `EVIDENCE_PINNED` |
| A named human approved | `HUMAN_APPROVED` |
| Zero items are `BLOCKED` | `NO_BLOCKED` |
| The approval event exists in the log | `APPROVAL_NOT_IN_LOG` |

All failures are reported together. A gate that reports one blocker at a
time invites fixing one and shipping.

**Known weakness:** `HUMAN_APPROVED` verifies a non-empty name, not a
signature. It is attribution, not authentication. Recorded in the
acceptance document as `BLOCKED` for Phase 2.

---

## Article 8 — Evidence binding

An approval binds a **bundle digest**, not a description:

1. Each evidence file is hashed individually.
2. The manifest of those digests is hashed as a unit.
3. The approval event records the bundle digest.
4. The release must present the same digest.

Substituting, reordering, or dropping an evidence file changes the bundle
digest and closes the gate.

---

## Article 9 — Amendments

This constitution changes only by appending an ADR. It is never edited in
place to match new behaviour — the same rule the factory applies to its
own event log.
