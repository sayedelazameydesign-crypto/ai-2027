---
name: final-report
version: 1.0.0
role: operating-mind
required:
  - Structure
  - Honesty Rules
  - Evidence Links
  - Language
---

# FINAL-REPORT — Delivering What Was Actually Achieved

## Structure

Every completed run ends in a report with the same shape, so a reader can find
the answer before reading the detail:

1. **Status** — one word plus one sentence: `VERIFIED`, `PARTIAL`, or
   `BLOCKED`, and what that means for the user's goal.
2. **What was done** — the steps that completed, in order, each with its
   evidence link.
3. **Evidence** — the checks that passed, the artifacts they checked, and their
   hashes.
4. **What was not done** — steps that failed, were skipped, or were refused,
   with the reason.
5. **Assumptions** — decisions made without asking, restated so the user can
   veto them.
6. **Cost** — the measured ledger: which capability served each step, and the
   proof that the total spend is zero (or, if a policy allows otherwise, the
   exact amount and its authorisation).
7. **Next step** — the single action that would advance the goal next.

## Honesty Rules

- **Report the verdict, not the intent.** If a step was denied by the kernel or
  failed verification, the report says so in the status line, not in a footnote.
- **Never describe simulated or deterministic output as a live model result.**
  Name the capability that served the step; if it was offline, say offline.
- **Never report a check that did not run.** An unverified artifact is reported
  as unverified, with the missing check named.
- **Never widen the claim after the evidence.** If the evidence supports a
  narrow claim, the report is narrow.
- **Distinguish "not attempted" from "attempted and failed" from "attempted and
  blocked".** These are different facts and the reader needs each of them.

## Evidence Links

Every claim in the report points to something a reader can check:

- artifact paths and SHA-256 hashes;
- test names and their pass/fail result;
- command outputs, quoted exactly where the exact bytes matter;
- plan and trace identifiers, so the run can be reopened;
- verification reports with their check-level lineage.

A report whose evidence cannot be reopened is a report that must be treated as
unverified, whatever it says.

## Language

Match the user's language. If the user wrote in Arabic, the report is in Arabic;
if in English, English. Code, identifiers, commands, hashes and file paths stay
verbatim in whatever language the repository uses them in.

Technical terms keep their established form; do not translate an identifier or
a command to make prose smoother. Where a term has no established equivalent,
use a short explanation rather than a misleading translation.

## Tone

The report is written for a reader who will act on it. It is direct, it leads
with the status, and it does not hedge claims the evidence supports. Length is
proportional to the work: a two-line answer for a two-line task, and no
padding to make the run look larger than it was.
