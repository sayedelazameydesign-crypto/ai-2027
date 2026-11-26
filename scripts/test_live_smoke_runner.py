#!/usr/bin/env python3
"""Bare tests for scripts/live_smoke_runner.py's pure helpers.

The runner's live half needs gspread + credentials; everything it *decides*
with (header contracts, chain verification, seed-row construction) is pure
and runs here with no third-party imports — this is the P1 guard for the
checklist itself.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "scripts" / "live_smoke_runner.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("live_smoke_runner", RUNNER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["live_smoke_runner"] = module
    spec.loader.exec_module(module)
    return module


sm = _load_runner()

NOW = datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone.utc)


def audit_row(prev: str, self_hash: str, width: int = 25):
    row = [""] * width
    row[23] = prev
    row[24] = self_hash
    return row


class TestHeaderContracts(unittest.TestCase):
    def test_all_six_sheets_registered(self):
        self.assertEqual(
            sorted(sm.EXPECTED_HEADERS),
            ["pa_approvals", "pa_audit_logs_202611", "pa_delegation_grants",
             "pa_goals", "pa_source_records", "pa_tasks"],
        )

    def test_widths_match_the_live_verified_counts(self):
        self.assertEqual(len(sm.EXPECTED_HEADERS["pa_source_records"]), 23)
        self.assertEqual(len(sm.EXPECTED_HEADERS["pa_tasks"]), 22)
        self.assertEqual(len(sm.EXPECTED_HEADERS["pa_goals"]), 21)
        self.assertEqual(len(sm.EXPECTED_HEADERS["pa_approvals"]), 22)
        self.assertEqual(len(sm.EXPECTED_HEADERS["pa_delegation_grants"]), 23)
        self.assertEqual(len(sm.EXPECTED_HEADERS["pa_audit_logs_202611"]), 25)

    def test_critical_positions(self):
        # external_id P(15), dedupe_key V(21), action O(14)/status R(17),
        # scope O(14)/allowed R(17)/denied S(18)/limits T(19)/revoked W(22),
        # prev X(23)/self Y(24) — the live-verified layout.
        src = sm.EXPECTED_HEADERS["pa_source_records"]
        tasks = sm.EXPECTED_HEADERS["pa_tasks"]
        appr = sm.EXPECTED_HEADERS["pa_approvals"]
        grants = sm.EXPECTED_HEADERS["pa_delegation_grants"]
        audit = sm.EXPECTED_HEADERS["pa_audit_logs_202611"]
        self.assertEqual(src[15], "external_id")
        self.assertEqual(tasks[21], "dedupe_key")
        self.assertEqual(appr[14], "action")
        self.assertEqual(appr[17], "status")
        self.assertEqual(grants[14], "scope")
        self.assertEqual(grants[17], "allowed_actions")
        self.assertEqual(grants[18], "denied_actions")
        self.assertEqual(grants[19], "limits")
        self.assertEqual(grants[22], "revoked_at")
        self.assertEqual(audit[19], "subject_ids")
        self.assertEqual(audit[20], "payload_digest")
        self.assertEqual(audit[21], "policy_trace_id")
        self.assertEqual(audit[22], "approval_id")
        self.assertEqual(audit[23], "prev_hash")
        self.assertEqual(audit[24], "self_hash")

    def test_matching_header_passes_and_drift_fails(self):
        good = list(sm.EXPECTED_HEADERS["pa_tasks"])
        self.assertEqual(sm.header_problems("pa_tasks", good), [])
        bad = list(good)
        bad[21] = "dedupe"  # renamed column
        problems = sm.header_problems("pa_tasks", bad)
        self.assertEqual(len(problems), 1)
        self.assertIn("dedupe", problems[0])
        short = good[:-1]  # width drift
        problems = sm.header_problems("pa_tasks", short)
        self.assertTrue(any("width" in p for p in problems))

    def test_unknown_sheet_reports_contract_missing(self):
        problems = sm.header_problems("pa_unknown", [])
        self.assertTrue(problems and "no header contract" in problems[0])


class TestAuditChain(unittest.TestCase):
    def test_good_chain_passes(self):
        rows = [
            audit_row("", "a" * 64),
            audit_row("a" * 64, "b" * 64),
            audit_row("b" * 64, "c" * 64),
        ]
        self.assertEqual(sm.verify_audit_chain(rows), [])

    def test_empty_sheet_is_a_problem(self):
        self.assertTrue(sm.verify_audit_chain([]))

    def test_broken_link_is_detected(self):
        rows = [
            audit_row("", "a" * 64),
            audit_row("x" * 64, "b" * 64),  # does not match previous self
        ]
        problems = sm.verify_audit_chain(rows)
        self.assertEqual(len(problems), 1)
        self.assertIn("prev_hash", problems[0])

    def test_bad_self_hash_and_width_detected(self):
        rows = [audit_row("", "short")]
        problems = sm.verify_audit_chain(rows)
        self.assertTrue(any("self_hash" in p for p in problems))
        # A 24-cell row: no self_hash slot at all + width drift.
        rows = [[""] * 24]
        problems = sm.verify_audit_chain(rows)
        self.assertTrue(any("self_hash" in p for p in problems))
        self.assertTrue(any("width" in p for p in problems))

    def test_genesis_malformed_prev_detected(self):
        rows = [audit_row("zz", "a" * 64)]
        problems = sm.verify_audit_chain(rows)
        self.assertTrue(any("genesis" in p for p in problems))


class TestSeedApprovalRow(unittest.TestCase):
    def test_row_is_executable_by_the_contract(self):
        payload = {"smoke": True, "action": "agent.plan_day"}
        row = sm.build_test_approval_row("agent.plan_day", payload,
                                         "01SMOKETEST", NOW)
        self.assertEqual(len(row), 22)
        self.assertEqual(row[14], "agent.plan_day")
        self.assertEqual(row[17], "pending")
        self.assertEqual(row[15], sm.ge.compute_sha256(payload))
        self.assertEqual(len(row[13]), 64)
        self.assertEqual(row[20], "")  # not consumed
        self.assertEqual(row[21], "true")

    def test_expiry_in_future_and_id_shape(self):
        row = sm.build_test_approval_row("a", {}, "ID", NOW)
        expires = sm.ge.parse_iso_utc(row[19])
        self.assertIsNotNone(expires)
        self.assertGreater(expires, NOW)
        approval_id = sm.make_smoke_approval_id()
        self.assertTrue(approval_id.startswith("01SMOKE"))
        self.assertEqual(len(approval_id), 26)
        self.assertNotEqual(approval_id, sm.make_smoke_approval_id())


class TestReportExitCodes(unittest.TestCase):
    def test_exit_codes_follow_doctor_convention(self):
        only_pass = sm.Report()
        only_pass.ok("x")
        self.assertEqual(only_pass.exit_code(), 0)
        with_skip = sm.Report()
        with_skip.ok("x")
        with_skip.skip("y")
        self.assertEqual(with_skip.exit_code(), 3)
        with_fail = sm.Report()
        with_fail.skip("y")
        with_fail.fail("z")
        self.assertEqual(with_fail.exit_code(), 1)


# ---------------------------------------------------------------------------
# --execute path (pure halves: payload round-trip, row selection, gates)
# ---------------------------------------------------------------------------
class _WS:
    def __init__(self, rows):
        self.rows = [list(r) for r in rows]

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def batch_update(self, data):
        for item in data:
            n = int(item["range"].split(":")[0][1:])
            self.rows[n - 1] = list(item["values"][0])


class _Book:
    def __init__(self, sheets):
        self.s = {k: _WS(v) for k, v in sheets.items()}

    def worksheet(self, name):
        return self.s[name]


def _granted_row(action="agent.plan_day", appr_id="01SMOKETESTROW00000001"):
    payload = sm.default_execute_payload(action)
    row = sm.build_test_approval_row(action, payload, appr_id, NOW)
    row[17] = "granted"          # what the bot writes on an Approve vote
    return row


def _active_grant_row(action="agent.plan_day"):
    row = [""] * 23
    row[14] = "agent.write"
    row[15] = "active"
    row[17] = json.dumps([action])
    row[18] = json.dumps(["gmail.send"])
    row[19] = json.dumps({"max_per_day": 5})
    row[22] = ""
    return row


def _executor_book(approval_rows):
    return _Book({
        "pa_approvals": [sm.EXPECTED_HEADERS["pa_approvals"]] + approval_rows,
        "pa_delegation_grants": [sm.EXPECTED_HEADERS["pa_delegation_grants"]]
        + [_active_grant_row()],
        "pa_audit_logs_202611": [sm.EXPECTED_HEADERS["pa_audit_logs_202611"]],
    })


class TestExecutePath(unittest.TestCase):
    def test_default_payload_roundtrip(self):
        # --seed-approval and --execute share one deterministic payload, so
        # the seeded payload_hash verifies without remembering volatile fields.
        payload = sm.default_execute_payload("agent.plan_day")
        self.assertEqual(payload, {"smoke": True, "action": "agent.plan_day"})
        row = sm.build_test_approval_row("agent.plan_day", payload, "ID", NOW)
        self.assertEqual(row[15], sm.ge.compute_sha256(payload))

    def test_find_granted_approval_selects_only_executable(self):
        pending = sm.build_test_approval_row(
            "agent.plan_day", sm.default_execute_payload("agent.plan_day"),
            "01SMOKEPENDING000000001", NOW)
        granted = _granted_row(appr_id="01SMOKEGRANTED000000001")
        consumed = _granted_row(appr_id="01SMOKECONSUMED00000001")
        consumed[17] = "consumed"
        consumed[20] = "2026-11-26T05:00:00.000Z"
        rows = [pending, granted, consumed]

        row_no, hit = sm.find_granted_approval(rows, "agent.plan_day")
        self.assertEqual(hit[0], "01SMOKEGRANTED000000001")
        self.assertEqual(row_no, 3)          # header(1) + pending(1) + this

        _, none_hit = sm.find_granted_approval(rows, "other.action")
        self.assertIsNone(none_hit)
        _, by_id = sm.find_granted_approval(
            rows, "agent.plan_day", approval_id="01SMOKECONSUMED00000001")
        self.assertIsNone(by_id)             # consumed never re-executes

    def test_execute_granted_consumes_and_audits(self):
        book = _executor_book([_granted_row()])
        audits = []
        report = sm.Report()
        payload = sm.default_execute_payload("agent.plan_day")
        result = sm.execute_granted(book, lambda **kw: audits.append(kw),
                                    "agent.plan_day", payload, report)
        self.assertIsNotNone(result)
        self.assertTrue(result.ok)
        row = book.worksheet("pa_approvals").rows[1]
        self.assertEqual(row[17], "consumed")
        self.assertTrue(row[20])
        self.assertEqual(row[3], "2")
        self.assertEqual(audits[-1]["outcome"], "allowed")
        self.assertGreaterEqual(report.passed, 1)

    def test_execute_skips_when_nothing_is_granted(self):
        pending = sm.build_test_approval_row(
            "agent.plan_day", sm.default_execute_payload("agent.plan_day"),
            "01SMOKEPENDING000000002", NOW)
        book = _executor_book([pending])
        report = sm.Report()
        result = sm.execute_granted(book, lambda **kw: None,
                                    "agent.plan_day",
                                    sm.default_execute_payload("agent.plan_day"),
                                    report)
        self.assertIsNone(result)
        self.assertGreaterEqual(report.skipped, 1)

    def test_execute_denied_approval_reports_failure(self):
        # Granted row whose action is on the grant's denied list -> the
        # executor denies and the report counts a failure (fail-closed).
        granted = _granted_row(action="gmail.send",
                               appr_id="01SMOKEDENIED000000001")
        book = _executor_book([granted])
        report = sm.Report()
        result = sm.execute_granted(book, lambda **kw: None, "gmail.send",
                                    {"smoke": True, "action": "gmail.send"},
                                    report)
        self.assertIsNotNone(result)
        self.assertFalse(result.ok)
        self.assertTrue(result.reason.startswith("action_explicitly_denied:"))
        self.assertGreaterEqual(report.failed, 1)

    def test_gworksheet_prefixes_sheet_name(self):
        # The executor sends sheet-relative ranges; the live values API needs
        # "pa_approvals!A2:V2" — the adapter must prefix the title.
        calls = []

        class SS:
            def values_update(self, range_, params=None, body=None):
                calls.append((range_, body))

        class WS:
            spreadsheet = SS()

            def get_all_values(self):
                return [["h"], ["r"]]

            def append_row(self, row, value_input_option="RAW"):
                calls.append(("append", list(row)))

        gws = sm._GWorksheet(WS(), "pa_approvals")
        gws.batch_update([{"range": "A2:V2", "values": [["x", "y"]]}])
        self.assertEqual(calls[0][0], "pa_approvals!A2:V2")
        self.assertEqual(calls[0][1], {"values": [["x", "y"]]})


if __name__ == "__main__":
    unittest.main()
