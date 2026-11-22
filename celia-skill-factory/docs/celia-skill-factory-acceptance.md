# CELIA Skill Factory — Acceptance Criteria (Phase 1)

**Rule of this document:** a criterion is `MET` only when a named command
produced a recorded result. Nothing here is `MET` by inspection.

---

## A. Verified by execution

Run:

```bash
cd celia-skill-factory
PYTHONPATH=. python3 -W error::ResourceWarning -m unittest discover -s tests -t . -q
```

Recorded result: **56 tests · 0 failures · 0 errors.**

| # | Criterion | Test | Status |
|---|---|---|---|
| A1 | Canonical bytes are reproducible regardless of key order | `test_key_order_is_irrelevant` | `MET` |
| A2 | Floats are rejected as non-canonical | `test_float_rejected` | `MET` |
| A3 | Duplicate JSON keys are rejected on parse | `test_duplicate_key_rejected` | `MET` |
| A4 | Edited payload is detected | `test_edit_payload_detected` | `MET` |
| A5 | Reordered events are detected | `test_reorder_detected` | `MET` |
| A6 | Adjacent swap is detected | `test_swap_two_adjacent_detected` | `MET` |
| A7 | Mid-file deletion is detected | `test_delete_middle_detected` | `MET` |
| A8 | Duplicated line is detected | `test_duplicate_line_detected` | `MET` |
| A9 | Tail truncation is detected by the anchor | `test_tail_truncation_detected_by_anchor` | `MET` |
| A10 | Rollback to an older snapshot is detected | `test_rollback_to_older_state_detected` | `MET` |
| A11 | A **consistent** full rewrite is caught by the anchor | `test_consistent_full_rewrite_detected_by_anchor` | `MET` |
| A12 | Chain is verified at load, not only on demand | `test_edit_breaks_chain_before_anchor` | `MET` |
| A13 | Draft log lives at the ADR path | `test_path_matches_adr` | `MET` |
| A14 | Every draft transition emits an event | `test_every_transition_emits_an_event` | `MET` |
| A15 | Illegal transitions are rejected | `test_illegal_transition_rejected` | `MET` |
| A16 | Terminal states are final | `test_terminal_state_is_final` | `MET` |
| A17 | Draft logs never touch the release log | `test_draft_log_never_touches_release_log` | `MET` |
| A18 | Tampered draft log is rejected on open | `test_tampered_draft_log_detected_on_open` | `MET` |
| A19 | Evidence manifest is order-independent | `test_digest_is_order_independent` | `MET` |
| A20 | Path traversal is rejected | `test_path_escape_rejected` | `MET` |
| A21 | Symlinks are rejected | `test_symlink_rejected` | `MET` |
| A22 | Substituted evidence blocks release | `test_blocked_when_evidence_substituted` | `MET` |
| A23 | Release blocked without tests | `test_blocked_without_tests` | `MET` |
| A24 | Release blocked without a human approver | `test_blocked_without_human` | `MET` |
| A25 | Release blocked while anything is `BLOCKED` | `test_blocked_when_anything_is_blocked` | `MET` |
| A26 | All blockers reported together, not one at a time | `test_multiple_blockers_all_reported` | `MET` |

---

## B. Explicitly NOT met — declared, not hidden

| # | Criterion | Status | Reason |
|---|---|---|---|
| B1 | Factory engine executes a skill build | **`BLOCKED`** | No engine exists in Phase 1 |
| B2 | Two independent implementations agree on canonical bytes | **`BLOCKED`** | Needs a second language; no Node runtime in this environment |
| B3 | Anchor resists an actor with write access to log **and** anchor | **`BLOCKED`** | Needs external trusted timestamp — Phase 2 |
| B4 | Anchor placement is fixed (in-repo vs off-repo) | **`OPEN`** | Parameterised deliberately; decision required |
| B5 | Concurrent writers produce a linear chain | **`BLOCKED`** | Single-writer assumption is undocumented and untested |
| B6 | Signing keys for approval events | **`BLOCKED`** | No key store; human approval is a name, not a signature |
| B7 | Original ZIP artefacts (`CELIASKILL.md`, SVG) | **`NOT RECEIVED`** | Source archive unavailable in this session |

> **B6 is the weakest link.** `HUMAN_APPROVED` currently means "a non-empty
> string was supplied". That is an attribution claim, not an authentication
> one, and it is recorded here rather than glossed over.

---

## C. Phase 2 entry conditions

Phase 2 does not start until all of these hold:

1. This ADR is approved by the repository owner.
2. B4 is decided and written into the ADR.
3. B3 has an external anchor mechanism specified.
4. The single-writer assumption (B5) is documented or removed.
5. B7 artefacts are received and reconciled against this design.

Nothing in section A substitutes for these five.
