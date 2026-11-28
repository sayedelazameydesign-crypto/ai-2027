#!/usr/bin/env python3
"""Unit test wrapper for scripts/whatsapp_cloud_rehearsal.py (runs from repo root in CI)."""

from __future__ import annotations

import unittest

from scripts import whatsapp_cloud_rehearsal


class WhatsAppCloudRehearsalTests(unittest.TestCase):
    def test_rehearsal_passes_all_gates_hermetically(self) -> None:
        report = whatsapp_cloud_rehearsal.run_rehearsal()
        self.assertTrue(report["ok"])
        self.assertEqual(report["signature_stage"], "ok")
        self.assertEqual(report["inbound_stage"], "accepted_text")
        self.assertEqual(report["preflight_stage"], "ok")
        self.assertEqual(report["execute_outcome"], "allowed")
        self.assertEqual(report["replay_denied_reason"], "approval_already_consumed")
        self.assertEqual(report["audit_rows_recorded"], 2)
        self.assertEqual(whatsapp_cloud_rehearsal.main(["--assert"]), 0)


if __name__ == "__main__":
    unittest.main()
