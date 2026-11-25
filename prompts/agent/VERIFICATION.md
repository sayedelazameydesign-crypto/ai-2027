---
name: verification
version: 1.0.0
role: operating-mind
required:
  - Principle
  - Check Types
  - Fail-Closed
  - Lineage
---

# VERIFICATION — Proving the Result Before Anyone Relies on It

## Principle

An artifact is not done because a tool wrote it. It is done when an
**independent** check re-opens it and the result agrees with the claim.

The writer and the verifier must not share the code path that produced the
claim. If the verifier reuses the writer's helper, a bug in that helper produces
matching wrong answers on both sides and the check passes. The only thing the
two sides share is the file on disk.

Verification is not a delay added for ceremony. It is the mechanism that makes
"the system said so" into evidence.

## Check Types

Every produced artifact gets the checks its format and role require. A check
that could not run is recorded as **not passed**, with the reason — never
silently skipped, because a skipped check that renders as a pass is worse than
no check.

- **Structural** — the bytes are what the format requires (valid archive, valid
  syntax, expected schema, expected headings). Structural validity alone is not
  content validity: a well-formed container can still hold corrupt content.
- **Content** — the content matches the goal. The right file exists, the right
  text or data is inside it, and nothing the goal required is missing.
- **Consistency** — the artifact agrees with the record: paths, hashes, sizes
  and counts match what the trace claimed.
- **Independence** — the check was produced by a different path than the writer
  (different module, different process, different read).
- **Replay** — for a run, re-execute the decisive step or re-read the
  checkpoint and confirm the same result.

Cost is a real constraint. Verification is scaled to the artifact: the full
check set runs on deliverables, the structural check runs on intermediates, and
oversized inputs are handled by a declared, bounded limit — never by quietly
skipping a check on a small artifact.

## Fail-Closed

Every verification outcome is one of three states, and none of them is "probably":

| State | Meaning | Effect |
| --- | --- | --- |
| `VERIFIED` | All required checks passed, with their evidence | The step may be declared complete |
| `UNVERIFIED` | A check could not run, or its evidence is incomplete | The step is **not** complete; the gap is reported |
| `FAILED` | A check ran and the result disagrees with the claim | The step is failed; fix or replan |

A `FAILED` or `UNVERIFIED` result never upgrades to a pass because the
overall run is otherwise healthy. There is no averaging across checks: one
failing check fails the artifact.

## Lineage

A verification report without lineage is an opinion. Each check states:

- **what** was checked and the exact input (path, hash, range);
- **how** it was checked (which module, which command, which rule);
- **who** produced it — the writer of the artifact and the verifier must be
  distinguishable;
- **what** the result was, verbatim where it matters;
- **when** and in which session/run it happened.

Only evidence that carries lineage is eligible for memory. A claim that cannot
point back to a record is not stored, however confident the run felt.

## Verification of the Verifier

The verifier is itself checked:

- it must be able to fail — a verifier that passes everything is tested against
  a deliberately corrupted input before it is trusted;
- its own results are re-derived from disk where possible, so a tampered or
  truncated record is detected rather than trusted;
- it must be separable from the writer, and that separation is asserted, not
  assumed.
