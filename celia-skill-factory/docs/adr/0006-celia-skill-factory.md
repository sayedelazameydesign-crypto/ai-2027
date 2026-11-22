# ADR-0006 — CELIA Skill Factory (Phase 1)

| Field | Value |
|---|---|
| **Status** | Proposed — awaiting owner approval |
| **Date** | 2026-11-22 |
| **Deciders** | Repository owner (sole approver) |
| **Supersedes** | nothing |
| **Phase** | 1 of 2 — design + reference core. **No factory engine.** |

---

## 1. Context

The repository needs a governed path for turning a skill idea into a
published, auditable skill artefact. The design review raised four points
that blocked approval (`P1`, `P1b`, `P2`, `P2b`). This ADR resolves the
two blocking ones by **specifying and executing** a reference core rather
than by promising one.

**Scope boundary, stated up front:**

| In scope | Out of scope |
|---|---|
| Event-log integrity (chain + anchor) | The factory engine |
| Draft lifecycle log | Nimna |
| Release gate + evidence digests | The kernel |
| Canonical serialization | Initial skills, CI, runtime scripts |

---

## 2. Decision

### 2.1 Event log — three layers, three failure classes

`registry.events.jsonl` — one canonical JSON object per line, LF-terminated.

```json
{"sequence":1,"event_type":"RELEASE","payload":{...},
 "previous_event_sha256":"0000...0000","event_sha256":"..."}
```

| Layer | Field | Detects | Does **not** detect |
|---|---|---|---|
| **Chain** | `sequence` + `previous_event_sha256` + `event_sha256` | edit, reorder, mid-file deletion, duplicate line | tail truncation |
| **Anchor** | `(sequence, head_sha256)` stored outside the log | tail truncation, rollback to an older snapshot, consistent full rewrite | rewrite of log **and** anchor |
| **Digests** | evidence bundle digest bound to the approval event | substituted or reordered evidence | — |

**Honest limit, binding:** an actor with write access to both the log and
the anchor can rewrite history consistently. In-repo anchoring is
`BLOCKED` against that threat and is recorded as such — not claimed as
solved. Defeating it requires an external trusted timestamp or a
third-party witness, which is out of Phase 1 scope.

**Anchor placement (open question, deliberately not silently decided):**
the anchor path is a parameter, not a constant. In-repo placement gives
weaker independence than off-repo placement. Phase 2 must fix this
explicitly; until then the weakness is declared in code and in tests.

### 2.2 Draft log — a separate record, not a release subtype

```
skills/drafts/{draft_id}/events.jsonl
skills/drafts/{draft_id}/anchor.json
```

`draft_id` must match `^[a-z0-9][a-z0-9._-]{0,62}$` — no traversal, no case
ambiguity. Every transition appends `DRAFT_TRANSITION`; artefacts append
`DRAFT_ARTEFACT` with a sha256. A draft log **never** writes to
`registry.events.jsonl`; a test asserts this.

Declared lifecycle (`DRAFT` → … → `PUBLISHED` | `ABANDONED`); any
transition not in the table is rejected with `ILLEGAL_TRANSITION`.

### 2.3 Canonical serialization

Sorted keys, no whitespace, UTF-8 without BOM, integers only, duplicate
keys rejected on parse, one LF per line. **Floats are rejected**: their
textual form is not portable, so a digest over a float is not reproducible
across languages. A measured ratio is stored as an integer plus a scale,
or as a string.

### 2.4 Release gate

Release is refused unless **all** hold:

| Code | Condition |
|---|---|
| `TESTS_PASSED` | the test suite for the skill ran and passed |
| `EVIDENCE_PINNED` | current bundle digest **equals** the approved bundle digest |
| `HUMAN_APPROVED` | a named human approver, non-empty |
| `NO_BLOCKED` | zero items in a `BLOCKED` state |
| `APPROVAL_NOT_IN_LOG` | the approval event exists in the log |

All blockers are reported together, never one at a time.

---

## 3. Consequences

**Positive.** Every claim in this ADR is backed by an executing test —
56 tests, 0 failures, run with `-W error::ResourceWarning`. Tampering is
detected by reason code, so the failure mode is machine-checkable rather
than prose.

**Negative.** Anchor independence is unresolved; the log is append-only,
so correcting a mistaken event means appending a compensating event, never
editing — which grows the file monotonically.

**Neutral.** Standard library only, `$0`, no new dependencies.

---

## 4. Two bugs found by writing the tests

Recorded because they are the point of the exercise:

1. **`_norm_path` normalized before validating.** `lstrip("./")` turned
   `"../../etc/passwd"` into `"etc/passwd"` and accepted a traversal.
   Fixed: validate first, then normalize.
2. **`_load` hashed without verifying continuity.** A reordered or
   duplicated log opened cleanly. Fixed: `verify_chain()` runs at load.

Neither would have surfaced from a document review alone.

---

## 5. Alternatives considered

| Alternative | Rejected because |
|---|---|
| Single `sequence` field only | Detects reorder, not tail loss |
| Anchor inside the log file | Not an anchor — it moves with the data |
| Database with triggers | Not `$0`-portable; adds a runtime dependency |
| Signing every event with a key | Key custody is Phase 2; no key store exists yet |
| Storing ratios as floats | Not canonically reproducible across languages |

---

## 6. Approval

Requires the repository owner's explicit approval. This ADR does not
self-approve, and Phase 2 does not begin on the strength of a green test
suite alone.
