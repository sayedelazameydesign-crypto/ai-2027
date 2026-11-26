#!/usr/bin/env python3
"""Hermetic tests for the governed WRITE executor (stdlib only).

No network, no credentials, no gspread: the executor takes an injected sheet
and an injected audit logger, so the fail-closed contract runs bare in CI.

Covered gates:

- approval gate: missing / deleted / consumed / non-approved status /
  action mismatch / missing-or-tampered payload_hash / expired-or-unparsable;
- delegation gate: no grants sheet rows, inactive (proposed) grant, revoked
  grant, action in denied_actions (deny wins), action not in allowed_actions,
  malformed policy JSON;
- limits gate: max_per_day enforced from today's executor audit rows,
  malformed / unenforceable / unverifiable limits deny;
- consume gate: single-use (second execute denies), version bump, checksum;
- audit: every deny and allow appends a chained unit=executor row;
- drift guards: compute_sha256 == watcher's, approval_checksum ==
  approval_bot's (the three modules keep one canonicalisation).
"""

from __future__ import annotations

import importlib.util
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXECUTOR_PATH = ROOT / "src" / "executors" / "governed_executor.py"

NOW = datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone.utc)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves annotations via sys.modules.
    import sys
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ge = _load("governed_executor", EXECUTOR_PATH)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeWorksheet:
    def __init__(self, width: int, rows=None):
        self.rows = [list(r) for r in (rows or [["c%d" % i for i in range(width)]])]

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def batch_update(self, data):
        for item in data:
            row_no = int(item["range"].split(":")[0][1:])
            while len(self.rows) < row_no:
                self.rows.append([])
            self.rows[row_no - 1] = list(item["values"][0])


class FakeSpreadsheet:
    def __init__(self, sheets):
        self.worksheets = {k: FakeWorksheet(w, r) for k, (w, r) in sheets.items()}

    def worksheet(self, name):
        return self.worksheets[name]


class AuditSpy:
    """Stands in for the watcher's chained append_audit_log."""

    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)


def make_approval_row(payload, appr_id="01APPR001", action="gmail.send_draft",
                      status="granted", expires_at="2026-11-26T13:00:00.000Z",
                      consumed_at="", deleted_at="", payload_hash=None):
    row = [""] * 22
    row[0] = appr_id
    row[1] = "2026-11-26T11:00:00.000Z"
    row[2] = "2026-11-26T11:00:00.000Z"
    row[3] = "1"
    row[11] = deleted_at
    row[13] = "ab" * 32
    row[14] = action
    row[15] = payload_hash if payload_hash is not None else ge.compute_sha256(payload)
    row[16] = "summary"
    row[17] = status
    row[18] = "2026-11-26T11:00:00.000Z"
    row[19] = expires_at
    row[20] = consumed_at
    row[21] = "true"
    return row


def make_grant_row(status="active", allowed=None, denied=None, revoked_at="",
                   limits="", grant_id="01GRANT001"):
    row = [""] * 23
    row[0] = grant_id
    row[14] = "agent.write"          # O: scope
    row[15] = status                 # P: status
    row[17] = json.dumps(allowed if allowed is not None else ["gmail.send_draft"])
    row[18] = json.dumps(denied if denied is not None else ["gmail.delete_forever"])
    row[19] = limits                 # T: limits
    row[20] = "false"                # U: requires_approval
    row[21] = "user"                 # V: granted_by
    row[22] = revoked_at             # W: revoked_at
    return row


def make_audit_row(at="2026-11-26T11:30:00.000Z", unit="executor",
                   action="write.gmail.send_draft", outcome="allowed"):
    row = [""] * 25
    row[14] = at
    row[15] = unit
    row[16] = action
    row[18] = outcome
    return row


def make_sheet(approval_rows, grant_rows, audit_rows=None):
    return FakeSpreadsheet({
        "pa_approvals": (22, [make_header(22)] + approval_rows),
        "pa_delegation_grants": (23, [make_header(23)] + grant_rows),
        "pa_audit_logs_202611": (25, [make_header(25)] + (audit_rows or [])),
    })


def make_header(width):
    return ["h%d" % i for i in range(width)]


def make_executor(sheet, audit=None):
    audit = audit if audit is not None else AuditSpy()
    executor = ge.GovernedExecutor(sheet, audit, now_fn=lambda: NOW)
    return executor, audit


GOOD_PAYLOAD = {"to": "test@example.com", "body": "hello"}


class TestAllowPath(unittest.TestCase):
    def test_execute_succeeds_and_consumes_single_use(self):
        sheet = make_sheet([make_approval_row(GOOD_PAYLOAD)], [make_grant_row()])
        executor, audit = make_executor(sheet)
        called = []
        executor.register_tool("gmail.send_draft", lambda p: called.append(p) or {"sent": True})

        res1 = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertTrue(res1.ok)
        self.assertEqual(res1.outcome, "allowed")
        self.assertEqual(len(called), 1)
        self.assertEqual(audit.calls[-1]["outcome"], "allowed")

        # Single-use: the same approval must deny afterwards.
        res2 = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertFalse(res2.ok)
        self.assertEqual(res2.reason, "approval_already_consumed")
        self.assertEqual(len(called), 1)

        # Row flipped: status consumed, consumed_at set, version bumped.
        row = sheet.worksheet("pa_approvals").rows[1]
        self.assertEqual(row[17], "consumed")
        self.assertTrue(row[20])
        self.assertEqual(row[3], "2")
        self.assertEqual(len(row[13]), 64)

    def test_approved_word_also_accepted(self):
        # The contract allows an explicit "approved" state as well as "granted".
        payload = {"x": 1}
        sheet = make_sheet(
            [make_approval_row(payload, status="approved")],
            [make_grant_row()],
        )
        executor, _audit = make_executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft", payload)
        self.assertTrue(res.ok)


class TestApprovalGate(unittest.TestCase):
    def test_denies_unregistered_action(self):
        sheet = make_sheet([make_approval_row(GOOD_PAYLOAD)], [make_grant_row()])
        executor, audit = make_executor(sheet)
        res = executor.execute("01APPR001", "unknown.action", GOOD_PAYLOAD)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "unregistered_write_action")
        self.assertEqual(audit.calls[-1]["outcome"], "denied")

    def test_denies_unknown_approval(self):
        sheet = make_sheet([], [make_grant_row()])
        executor, _audit = make_executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("NOPE", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "approval_record_not_found")

    def test_denies_deleted_approval(self):
        sheet = make_sheet(
            [make_approval_row(GOOD_PAYLOAD, deleted_at="2026-11-26T11:30:00.000Z")],
            [make_grant_row()],
        )
        executor, _audit = make_executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "approval_deleted")

    def test_denies_non_approved_status(self):
        sheet = make_sheet(
            [make_approval_row(GOOD_PAYLOAD, status="pending")],
            [make_grant_row()],
        )
        executor, _audit = make_executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "invalid_approval_status:pending")

    def test_denies_action_mismatch(self):
        sheet = make_sheet(
            [make_approval_row(GOOD_PAYLOAD, action="calendar.create_event")],
            [make_grant_row()],
        )
        executor, _audit = make_executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertFalse(res.ok)
        self.assertTrue(res.reason.startswith("action_mismatch:"))

    def test_denies_tampered_payload(self):
        sheet = make_sheet(
            [make_approval_row({"to": "safe@example.com"})],
            [make_grant_row()],
        )
        executor, _audit = make_executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft",
                               {"to": "attacker@example.com"})
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "payload_hash_mismatch")

    def test_denies_missing_payload_hash(self):
        # The contract requires payload_hash to EXIST — absence denies.
        sheet = make_sheet(
            [make_approval_row(GOOD_PAYLOAD, payload_hash="")],
            [make_grant_row()],
        )
        executor, _audit = make_executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "missing_payload_hash")

    def test_denies_expired_and_unparsable_expiry(self):
        for bad_expiry in ("2026-11-26T11:00:00.000Z", "", "not-a-date"):
            sheet = make_sheet(
                [make_approval_row(GOOD_PAYLOAD, expires_at=bad_expiry)],
                [make_grant_row()],
            )
            executor, _audit = make_executor(sheet)
            executor.register_tool("gmail.send_draft", lambda p: "ok")
            res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
            self.assertFalse(res.ok, bad_expiry)
            self.assertEqual(res.reason, "approval_expired_or_missing_expiry")

    def test_denies_already_consumed(self):
        sheet = make_sheet(
            [make_approval_row(GOOD_PAYLOAD, consumed_at="2026-11-26T11:30:00.000Z")],
            [make_grant_row()],
        )
        executor, _audit = make_executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "approval_already_consumed")


class TestDelegationGate(unittest.TestCase):
    def test_denies_when_no_grants_configured(self):
        sheet = make_sheet([make_approval_row(GOOD_PAYLOAD)], [])
        executor, _audit = make_executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "no_delegation_grants_configured")

    def test_denies_proposed_grant(self):
        sheet = make_sheet(
            [make_approval_row(GOOD_PAYLOAD)],
            [make_grant_row(status="proposed")],
        )
        executor, _audit = make_executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertFalse(res.ok)
        self.assertIn("no_active_grant", res.reason)

    def test_denies_revoked_grant(self):
        sheet = make_sheet(
            [make_approval_row(GOOD_PAYLOAD)],
            [make_grant_row(revoked_at="2026-11-26T11:00:00.000Z")],
        )
        executor, _audit = make_executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertFalse(res.ok)
        self.assertIn("no_active_grant", res.reason)

    def test_denied_actions_win_even_alongside_allowed(self):
        # Same action present in allowed and denied of two active rows, or in
        # the same row's denied list — deny must win.
        sheet = make_sheet(
            [make_approval_row(GOOD_PAYLOAD)],
            [
                make_grant_row(allowed=["gmail.send_draft"],
                               denied=["gmail.send_draft"], grant_id="G1"),
            ],
        )
        executor, _audit = make_executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertFalse(res.ok)
        self.assertTrue(res.reason.startswith("action_explicitly_denied:"))

    def test_deny_wins_across_rows_in_any_order(self):
        # Allowing row FIRST, denying row second — the scan must still deny.
        sheet = make_sheet(
            [make_approval_row(GOOD_PAYLOAD)],
            [
                make_grant_row(allowed=["gmail.send_draft"], grant_id="G1"),
                make_grant_row(allowed=["other"], denied=["gmail.send_draft"],
                               grant_id="G2"),
            ],
        )
        executor, _audit = make_executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertFalse(res.ok)
        self.assertTrue(res.reason.startswith("action_explicitly_denied:"))

    def test_denies_when_action_not_allowed(self):
        sheet = make_sheet(
            [make_approval_row(GOOD_PAYLOAD)],
            [make_grant_row(allowed=["agent.plan_day"])],
        )
        executor, _audit = make_executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertFalse(res.ok)
        self.assertIn("no_active_grant_for_action", res.reason)

    def test_denies_malformed_policy_json(self):
        sheet = make_sheet([make_approval_row(GOOD_PAYLOAD)], [])
        # Bypass the helper to inject a malformed allowed_actions cell.
        grant = make_grant_row()
        grant[17] = "{not json"
        sheet.worksheets["pa_delegation_grants"].rows.append(grant)
        executor, _audit = make_executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "malformed_delegation_policy_json")


class TestLimitsGate(unittest.TestCase):
    def _sheet_with_limit(self, cap, audit_rows):
        return make_sheet(
            [make_approval_row(GOOD_PAYLOAD)],
            [make_grant_row(limits=json.dumps({"max_per_day": cap}))],
            audit_rows=audit_rows,
        )

    def test_max_per_day_allows_up_to_cap(self):
        # Two prior successes today -> the third attempt denies.
        prior = [
            make_audit_row(at="2026-11-26T09:00:00.000Z"),
            make_audit_row(at="2026-11-26T10:00:00.000Z"),
        ]
        sheet = self._sheet_with_limit(2, prior)
        executor, audit = make_executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "limits_exceeded:max_per_day:2>=2")
        self.assertEqual(audit.calls[-1]["outcome"], "denied")

    def test_max_per_day_under_cap_executes(self):
        prior = [make_audit_row(at="2026-11-26T09:00:00.000Z")]
        sheet = self._sheet_with_limit(2, prior)
        executor, _audit = make_executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertTrue(res.ok)

    def test_other_days_do_not_count(self):
        prior = [
            make_audit_row(at="2026-11-25T09:00:00.000Z"),
            make_audit_row(at="2026-11-25T10:00:00.000Z"),
        ]
        sheet = self._sheet_with_limit(2, prior)
        executor, _audit = make_executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertTrue(res.ok)

    def test_malformed_limits_deny(self):
        sheet = make_sheet(
            [make_approval_row(GOOD_PAYLOAD)],
            [make_grant_row(limits="{broken")],
        )
        executor, _audit = make_executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "malformed_limits_json")

    def test_unenforceable_limit_key_denies(self):
        sheet = make_sheet(
            [make_approval_row(GOOD_PAYLOAD)],
            [make_grant_row(limits=json.dumps({"max_tokens": 100}))],
        )
        executor, _audit = make_executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertFalse(res.ok)
        self.assertTrue(res.reason.startswith("unenforceable_limit:"))

    def test_unverifiable_audit_denies(self):
        # Audit worksheet missing entirely -> limits cannot be verified.
        sheet = make_sheet(
            [make_approval_row(GOOD_PAYLOAD)],
            [make_grant_row(limits=json.dumps({"max_per_day": 5}))],
        )
        del sheet.worksheets["pa_audit_logs_202611"]
        executor, _audit = make_executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "limits_unverifiable")

    def test_empty_limits_cell_passes(self):
        sheet = make_sheet(
            [make_approval_row(GOOD_PAYLOAD)],
            [make_grant_row(limits="")],
            audit_rows=[],
        )
        executor, _audit = make_executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertTrue(res.ok)


class TestToolFailure(unittest.TestCase):
    def test_tool_exception_is_failed_and_audited(self):
        sheet = make_sheet([make_approval_row(GOOD_PAYLOAD)], [make_grant_row()])
        executor, audit = make_executor(sheet)

        def boom(payload):
            raise RuntimeError("smtp down")

        executor.register_tool("gmail.send_draft", boom)
        res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertFalse(res.ok)
        self.assertEqual(res.outcome, "failed")
        self.assertIn("smtp down", res.reason)
        self.assertEqual(audit.calls[-1]["outcome"], "failed")
        # The approval stays consumed: no retry on the same single-use row.
        row = sheet.worksheet("pa_approvals").rows[1]
        self.assertEqual(row[17], "consumed")


class TestAuditChainInputs(unittest.TestCase):
    def test_every_gate_appends_executor_rows(self):
        sheet = make_sheet([], [])
        executor, audit = make_executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        executor.execute("X", "gmail.send_draft", GOOD_PAYLOAD)  # not found
        self.assertEqual(len(audit.calls), 1)
        call = audit.calls[0]
        self.assertEqual(call["unit"], "executor")
        self.assertEqual(call["action"], "write.gmail.send_draft")
        self.assertEqual(call["outcome"], "denied")
        self.assertEqual(call["policy_id"], "AD-004-FAIL-CLOSED")
        self.assertEqual(call["subjects"], ["X"])


class TestAssertionGate(unittest.TestCase):
    """ADR-0006: a pending row executes only with a valid signed assertion."""

    SECRET = bytes.fromhex(
        "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7")

    @classmethod
    def setUpClass(cls):
        cls.pub_hex = ge.ed25519.secret_public_key(cls.SECRET).hex()

    def _executor(self, sheet, with_key=True):
        audits = AuditSpy()
        ex = ge.GovernedExecutor(
            sheet, audits, now_fn=lambda: NOW,
            verification_public_key=self.pub_hex if with_key else None)
        return ex, audits

    def _assertion(self, row, payload, decision="grant",
                   exp="2026-11-26T13:00:00.000Z", signature=None,
                   approval_id=None, action=None, payload_hash=None):
        import base64 as _b64
        aid = row[0] if approval_id is None else approval_id
        act = row[14] if action is None else action
        phash = row[15] if payload_hash is None else payload_hash
        message = ge.canonical_assertion_message(aid, act, phash, decision, exp)
        sig = signature
        if sig is None:
            sig = _b64.b64encode(ge.ed25519.sign(message, self.SECRET)).decode()
        return {"approval_id": aid, "action": act, "payload_hash": phash,
                "decision": decision, "exp": exp, "signature": sig}

    def _pending_sheet(self, payload=GOOD_PAYLOAD, **appr_kwargs):
        row = make_approval_row(payload, status="pending", **appr_kwargs)
        return make_sheet([row], [make_grant_row()]), row

    def test_pending_with_valid_assertion_executes_and_consumes(self):
        sheet, row = self._pending_sheet()
        executor, audit = self._executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: {"ok": True})
        res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD,
                               assertion=self._assertion(row, GOOD_PAYLOAD))
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(res.outcome, "allowed")
        stored = sheet.worksheet("pa_approvals").rows[1]
        self.assertEqual(stored[17], "consumed")
        self.assertEqual(audit.calls[-1]["outcome"], "allowed")

    def test_assertion_without_configured_key_denies(self):
        sheet, row = self._pending_sheet()
        executor, _ = self._executor(sheet, with_key=False)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD,
                               assertion=self._assertion(row, GOOD_PAYLOAD))
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "assertion_verification_key_missing")

    def test_replay_after_consume_denies(self):
        sheet, row = self._pending_sheet()
        executor, _ = self._executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        assertion = self._assertion(row, GOOD_PAYLOAD)
        first = executor.execute("01APPR001", "gmail.send_draft",
                                 GOOD_PAYLOAD, assertion=assertion)
        self.assertTrue(first.ok)
        second = executor.execute("01APPR001", "gmail.send_draft",
                                  GOOD_PAYLOAD, assertion=assertion)
        self.assertFalse(second.ok)
        self.assertEqual(second.reason, "approval_already_consumed")

    def test_tampered_payload_denies_binding(self):
        sheet, row = self._pending_sheet()
        executor, _ = self._executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft",
                               {"to": "attacker@example.com"},
                               assertion=self._assertion(row, GOOD_PAYLOAD))
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "assertion_field_mismatch:payload_hash")

    def test_invalid_signature_denies(self):
        import base64 as _b64
        sheet, row = self._pending_sheet()
        executor, _ = self._executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        garbage = _b64.b64encode(b"\x11" * 64).decode()
        res = executor.execute(
            "01APPR001", "gmail.send_draft", GOOD_PAYLOAD,
            assertion=self._assertion(row, GOOD_PAYLOAD, signature=garbage))
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "assertion_signature_invalid")

    def test_expired_assertion_denies(self):
        sheet, row = self._pending_sheet()
        executor, _ = self._executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute(
            "01APPR001", "gmail.send_draft", GOOD_PAYLOAD,
            assertion=self._assertion(row, GOOD_PAYLOAD,
                                      exp="2026-11-26T11:00:00.000Z"))
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "assertion_expired")

    def test_non_grant_decision_denies(self):
        sheet, row = self._pending_sheet()
        executor, _ = self._executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute(
            "01APPR001", "gmail.send_draft", GOOD_PAYLOAD,
            assertion=self._assertion(row, GOOD_PAYLOAD, decision="deny"))
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "assertion_invalid_decision:deny")

    def test_wrong_approval_id_denies_before_signature(self):
        sheet, row = self._pending_sheet()
        executor, _ = self._executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute(
            "01APPR001", "gmail.send_draft", GOOD_PAYLOAD,
            assertion=self._assertion(row, GOOD_PAYLOAD,
                                      approval_id="01APPR_OTHER"))
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "assertion_field_mismatch:approval_id")

    def test_wrong_action_in_envelope_denies(self):
        sheet, row = self._pending_sheet()
        executor, _ = self._executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute(
            "01APPR001", "gmail.send_draft", GOOD_PAYLOAD,
            assertion=self._assertion(row, GOOD_PAYLOAD,
                                      action="calendar.create_event"))
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "assertion_field_mismatch:action")

    def test_malformed_assertions_deny(self):
        for bad in (None, "not-a-dict", {}, {"approval_id": "x"}):
            # None is handled by the status gate, not the assertion path.
            if bad is None:
                continue
            sheet, row = self._pending_sheet()
            executor, _ = self._executor(sheet)
            executor.register_tool("gmail.send_draft", lambda p: "ok")
            res = executor.execute("01APPR001", "gmail.send_draft",
                                   GOOD_PAYLOAD, assertion=bad)
            self.assertFalse(res.ok, repr(bad))
            self.assertEqual(res.reason, "assertion_malformed", repr(bad))

    def test_bad_base64_signature_denies(self):
        sheet, row = self._pending_sheet()
        executor, _ = self._executor(sheet)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute(
            "01APPR001", "gmail.send_draft", GOOD_PAYLOAD,
            assertion=self._assertion(row, GOOD_PAYLOAD,
                                      signature="!!!not-base64!!!"))
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "assertion_malformed")

    def test_granted_path_needs_no_key_or_assertion(self):
        # Transitional fallback (ADR-0006): bot-granted rows keep working
        # without any passkey configuration.
        sheet = make_sheet([make_approval_row(GOOD_PAYLOAD)],
                           [make_grant_row()])
        executor, _ = self._executor(sheet, with_key=False)
        executor.register_tool("gmail.send_draft", lambda p: "ok")
        res = executor.execute("01APPR001", "gmail.send_draft", GOOD_PAYLOAD)
        self.assertTrue(res.ok, res.reason)


class TestCanonicalisationDrift(unittest.TestCase):
    """The modules share one canonicalisation — these tests stop silent drift."""

    @classmethod
    def setUpClass(cls):
        from tests.test_proactive_watcher import (
            _ensure_module,
            _load_watcher_module,
        )
        _ensure_module("gspread", authorize=lambda creds: None)
        _ensure_module("google.oauth2.credentials", Credentials=object)
        _ensure_module("google.apiclient.discovery", build=lambda *a, **k: None)
        _ensure_module("googleapiclient.discovery", build=lambda *a, **k: None)
        cls.pw = _load_watcher_module()
        cls.ab = _load("approval_bot", ROOT / "src" / "watchers" / "approval_bot.py")

    def test_sha256_matches_watcher(self):
        sample = {"b": 1, "a": ["x", {"y": "ز"}]}
        self.assertEqual(ge.compute_sha256(sample), self.pw.compute_sha256(sample))

    def test_approval_checksum_matches_bot(self):
        row = make_approval_row(GOOD_PAYLOAD)
        ap = ge.approval_from_row(row)
        self.assertEqual(ge.approval_checksum(ap),
                         self.ab.approval_checksum(ap))

    def test_executor_and_bot_share_header(self):
        self.assertEqual(ge.APPROVAL_HEADER, self.ab.APPROVAL_COLUMNS)


if __name__ == "__main__":
    unittest.main()
