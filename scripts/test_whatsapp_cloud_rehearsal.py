#!/usr/bin/env python3
"""Unit test wrapper for scripts/whatsapp_cloud_rehearsal.py (runs from repo root in CI)."""

from __future__ import annotations

import unittest

from scripts import whatsapp_cloud_rehearsal


class WhatsAppCloudRehearsalTests(unittest.TestCase):
    def test_rehearsal_passes_all_gates_and_isolates_ambient_env(self) -> None:
        import os

        saved = os.environ.get("WHATSAPP_AMBIENT_LIVE_HINT")
        os.environ["WHATSAPP_AMBIENT_LIVE_HINT"] = "ambient-value"
        try:
            report = whatsapp_cloud_rehearsal.run_rehearsal()
            self.assertTrue(report["ok"])
            self.assertEqual(report["signature_stage"], "ok")
            self.assertEqual(report["inbound_stage"], "accepted_text")
            self.assertEqual(report["preflight_stage"], "ok")
            self.assertEqual(report["execute_outcome"], "allowed")
            self.assertEqual(report["replay_denied_reason"], "approval_already_consumed")
            self.assertTrue(
                report["daily_cap_denied_reason"].startswith("limits_exceeded:max_per_day")
            )
            self.assertTrue(
                report["delegation_denied_reason"].startswith("action_explicitly_denied")
            )
            self.assertEqual(report["audit_rows_recorded"], 4)
            self.assertEqual(os.environ.get("WHATSAPP_AMBIENT_LIVE_HINT"), "ambient-value")
            self.assertEqual(whatsapp_cloud_rehearsal.main(["--assert"]), 0)
        finally:
            if saved is None:
                os.environ.pop("WHATSAPP_AMBIENT_LIVE_HINT", None)
            else:
                os.environ["WHATSAPP_AMBIENT_LIVE_HINT"] = saved


if __name__ == "__main__":
    unittest.main()
