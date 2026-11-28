#!/usr/bin/env python3
"""Tests for scripts/watchdog_probe.py — real loopback HTTP, no internet, no secrets."""

from __future__ import annotations

import contextlib
import io
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List
from unittest import mock

from scripts import free_stack_runner
from scripts import watchdog_probe as wp


def serve(handler_cls) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class OpenGateHandler(BaseHTTPRequestHandler):
    """A broken deployment: healthy, but accepts unsigned webhook posts."""

    def do_GET(self) -> None:  # noqa: N802
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a) -> None:
        return


class DegradedHandler(BaseHTTPRequestHandler):
    """200 but {"ok": false} — e.g. a proxy page or a degraded app."""

    def do_GET(self) -> None:  # noqa: N802
        body = b'{"ok": false}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(401)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a) -> None:
        return


class FakeApis(BaseHTTPRequestHandler):
    """GitHub run history + Telegram sendMessage on one loopback port."""

    conclusions: List[str] = []
    sent: List[Dict[str, object]] = []
    paths: List[str] = []

    def do_GET(self) -> None:  # noqa: N802
        FakeApis.paths.append(self.path)
        runs = [{"id": 1000 + i, "conclusion": c} for i, c in enumerate(FakeApis.conclusions)]
        runs.insert(0, {"id": 999, "conclusion": None})  # the current run must be excluded
        body = json.dumps({"workflow_runs": runs}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        FakeApis.paths.append(self.path)
        FakeApis.sent.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a) -> None:
        return


class UrlTests(unittest.TestCase):
    def test_base_url_normalisation(self) -> None:
        self.assertEqual(wp.public_base_url("https://h.example.org/webhook"), "https://h.example.org")
        self.assertEqual(wp.public_base_url("https://h.example.org/webhook/"), "https://h.example.org")
        self.assertEqual(wp.public_base_url("https://h.example.org"), "https://h.example.org")
        self.assertEqual(wp.public_base_url("https://h.example.org/app/webhook"), "https://h.example.org/app")
        self.assertEqual(wp.public_base_url("http://127.0.0.1:8080/webhook"), "http://127.0.0.1:8080")

    def test_unsafe_urls_are_refused(self) -> None:
        for bad in ("http://h.example.org/webhook", "https://u:p@h.example.org/webhook",
                    "https://h.example.org/webhook?hub.verify_token=SECRET", "ftp://h.example.org", "h.example.org"):
            with self.subTest(url=bad), self.assertRaises(ValueError):
                wp.public_base_url(bad)


class ProbeTests(unittest.TestCase):
    def test_real_webhook_passes_both_checks(self) -> None:
        conn = free_stack_runner.init_db(":memory:")
        srv = serve(free_stack_runner.make_webhook_handler(free_stack_runner.ZeroCostOrchestrator(conn)))
        saved = dict(os.environ)
        try:
            for k in list(os.environ):
                if k.startswith(("YCLOUD_", "WHATSAPP_", "AI2027_WHATSAPP")):
                    os.environ.pop(k)
            checks = wp.run_probe(f"http://127.0.0.1:{srv.server_address[1]}", retries=0)
            self.assertEqual([(n, ok) for n, ok, _ in checks], [("healthz", True), ("signature_gate", True)])
        finally:
            srv.shutdown(); srv.server_close(); conn.close()
            os.environ.clear(); os.environ.update(saved)

    def test_open_signature_gate_is_a_security_failure(self) -> None:
        srv = serve(OpenGateHandler)
        try:
            checks = dict((n, (ok, d)) for n, ok, d in
                          wp.run_probe(f"http://127.0.0.1:{srv.server_address[1]}", retries=0))
            self.assertTrue(checks["healthz"][0])
            self.assertFalse(checks["signature_gate"][0])
            self.assertIn("SECURITY", checks["signature_gate"][1])
        finally:
            srv.shutdown(); srv.server_close()

    def test_http_200_without_ok_true_is_not_healthy(self) -> None:
        srv = serve(DegradedHandler)
        try:
            checks = dict((n, ok) for n, ok, _ in
                          wp.run_probe(f"http://127.0.0.1:{srv.server_address[1]}", retries=0))
            self.assertEqual(checks, {"healthz": False, "signature_gate": True})
        finally:
            srv.shutdown(); srv.server_close()

    def test_unreachable_host_fails_without_leaking_the_url(self) -> None:
        srv = serve(OpenGateHandler)
        port = srv.server_address[1]
        srv.shutdown(); srv.server_close()
        checks = wp.run_probe(f"http://127.0.0.1:{port}", timeout=2, retries=0)
        self.assertFalse(any(ok for _, ok, _ in checks))
        for _, _, detail in checks:
            self.assertNotIn(str(port), detail)

    def test_cli_skips_cleanly_without_the_variable_and_rejects_tokens_in_url(self) -> None:
        with mock.patch.dict(os.environ, {"AI2027_WEBHOOK_PUBLIC_URL": ""}), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(wp.main(["probe"]), 0)
        self.assertIn("SKIP", out.getvalue())
        with mock.patch.dict(os.environ, {"AI2027_WEBHOOK_PUBLIC_URL": "https://h.example.org/webhook?t=x"}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wp.main(["probe"]), 1)


class AuditChainTests(unittest.TestCase):
    def test_summary_shows_row_numbers_never_hash_fragments(self) -> None:
        problems = ["row 2: self_hash is not 64-hex ('deadbeefcafe1234')",
                    "row 7: prev_hash != row 6 self_hash", "audit sheet has no data rows"]
        text = wp.summarise_chain_problems(problems)
        self.assertEqual(text, "2 broken row(s): 2, 7; 1 sheet-level problem(s)")
        self.assertNotIn("deadbeef", text)

    def test_skips_without_secrets(self) -> None:
        with mock.patch.dict(os.environ, {"PA_MEMORY_SHEET_ID": "", "GOOGLE_SERVICE_ACCOUNT_JSON": ""}), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(wp.main(["audit-chain", "--creds", ""]), 0)
        self.assertIn("SKIP", out.getvalue())

    def _run_with_fake_sheet(self, rows, env_json: bool = False):
        import sys
        import types
        from scripts import live_smoke_runner as lsr

        ws = mock.Mock(); ws.get_all_values.return_value = [["header"] * 25] + rows
        fake_gspread = types.ModuleType("gspread")
        fake_gspread.authorize = mock.Mock(return_value=mock.Mock(
            open_by_key=mock.Mock(return_value=mock.Mock(worksheet=mock.Mock(return_value=ws)))))
        sa = types.ModuleType("google.oauth2.service_account")
        sa.Credentials = mock.Mock()
        oauth2 = types.ModuleType("google.oauth2"); oauth2.service_account = sa
        google = types.ModuleType("google"); google.oauth2 = oauth2
        mods = {"gspread": fake_gspread, "google": google, "google.oauth2": oauth2,
                "google.oauth2.service_account": sa}
        env = {"PA_MEMORY_SHEET_ID": "sheet-id-for-test",
               "GOOGLE_SERVICE_ACCOUNT_JSON": '{"type": "service_account"}' if env_json else ""}
        with mock.patch.dict(sys.modules, mods), mock.patch.dict(os.environ, env), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            code = wp.main(["audit-chain"] if env_json else ["audit-chain", "--creds", "/tmp/unused.json"])
        factory = (sa.Credentials.from_service_account_info if env_json
                   else sa.Credentials.from_service_account_file)
        if env_json:
            self.assertEqual(factory.call_args.args[0], {"type": "service_account"})
            sa.Credentials.from_service_account_file.assert_not_called()
        scopes = factory.call_args.kwargs["scopes"]
        self.assertEqual(scopes, ["https://www.googleapis.com/auth/spreadsheets.readonly"])
        return code, out.getvalue(), lsr

    def test_real_chain_verifier_intact_and_broken(self) -> None:
        from scripts import live_smoke_runner as lsr

        def row(prev: str, self_hash: str):
            r = [""] * 25
            r[lsr.COL_PREV_HASH], r[lsr.COL_SELF_HASH] = prev, self_hash
            return r

        a, b, c = "a" * 64, "b" * 64, "c" * 64
        code, out, _ = self._run_with_fake_sheet([row("", a), row(a, b), row(b, c)])
        self.assertEqual(code, 0)
        self.assertIn("intact across 3 rows", out)

        code, out, _ = self._run_with_fake_sheet([row("", a), row("f" * 64, b), row(b, c)], env_json=True)
        self.assertEqual(code, 1)
        self.assertIn("1 broken row(s): 3", out)
        self.assertNotIn("ffffffff", out, "hash fragments must not reach public logs")
        self.assertNotIn("aaaaaaaa", out)

    def test_configured_but_unreadable_is_a_failure(self) -> None:
        with mock.patch.dict(os.environ, {"PA_MEMORY_SHEET_ID": "sheet-id-for-test",
                                          "GOOGLE_SERVICE_ACCOUNT_JSON": "not-json"}), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(wp.main(["audit-chain"]), 1)
        self.assertNotIn("not-json", out.getvalue())


class DecisionTests(unittest.TestCase):
    def test_failure_streak(self) -> None:
        self.assertEqual(wp.failure_streak([]), 0)
        self.assertEqual(wp.failure_streak(["success", "failure"]), 0)
        self.assertEqual(wp.failure_streak(["failure", "cancelled", "failure", "success", "failure"]), 2)

    def test_alert_only_on_state_change_plus_reminders(self) -> None:
        self.assertEqual(wp.decide("failure", 0, 36), "alert")
        self.assertIsNone(wp.decide("failure", 1, 36))
        self.assertEqual(wp.decide("failure", 35, 36), "reminder")  # 36th consecutive failure
        self.assertIsNone(wp.decide("success", 0, 36))
        self.assertEqual(wp.decide("success", 4, 36), "recovery")
        self.assertEqual(wp.decide("failure", None, 36), "alert")    # history unknown -> loud
        self.assertIsNone(wp.decide("success", None, 36))

    def test_a_day_long_outage_sends_at_most_five_messages(self) -> None:
        sent = sum(1 for streak in range(144) if wp.decide("failure", streak, 36))
        self.assertLessEqual(sent, 5)


class NotifyTests(unittest.TestCase):
    TOKEN = "123456:SECRET-BOT-TOKEN-never-printed"

    def setUp(self) -> None:
        FakeApis.conclusions, FakeApis.sent, FakeApis.paths = [], [], []
        self.srv = serve(FakeApis)
        base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.env = mock.patch.dict(os.environ, {
            "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "999", "GITHUB_TOKEN": "gh-token",
            "GITHUB_API_URL": base, "TELEGRAM_API_BASE": base, "BOT_TOKEN": self.TOKEN,
            "CHAT_ID": "722947046", "RUN_URL": "https://github.com/o/r/actions/runs/999",
        })
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.srv.shutdown(); self.srv.server_close()

    def run_notify(self, status: str) -> str:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(wp.main(["notify", "--status", status]), 0)
        self.assertNotIn(self.TOKEN, out.getvalue())
        return out.getvalue()

    def test_first_failure_alerts_then_stays_quiet_then_recovers(self) -> None:
        FakeApis.conclusions = ["success"]
        self.run_notify("failure")
        self.assertEqual(len(FakeApis.sent), 1)
        self.assertIn("Alert", str(FakeApis.sent[0]["text"]))
        self.assertEqual(FakeApis.sent[0]["chat_id"], "722947046")

        FakeApis.conclusions = ["failure", "success"]
        self.run_notify("failure")
        self.assertEqual(len(FakeApis.sent), 1, "a continuing outage must not re-alert every run")

        FakeApis.conclusions = ["failure", "failure", "success"]
        self.run_notify("success")
        self.assertEqual(len(FakeApis.sent), 2)
        self.assertIn("عاد", str(FakeApis.sent[1]["text"]))
        self.assertTrue(any("/actions/workflows/watchdog-24x7.yml/runs" in p for p in FakeApis.paths))

    def test_healthy_steady_state_sends_nothing(self) -> None:
        FakeApis.conclusions = ["success", "success"]
        self.assertIn("no notification", self.run_notify("success"))
        self.assertEqual(FakeApis.sent, [])

    def test_unreadable_history_still_alerts(self) -> None:
        with mock.patch.dict(os.environ, {"GITHUB_API_URL": "http://127.0.0.1:9"}):
            self.run_notify("failure")
        self.assertEqual(len(FakeApis.sent), 1)

    def test_missing_bot_credentials_warns_but_does_not_crash(self) -> None:
        FakeApis.conclusions = ["success"]
        with mock.patch.dict(os.environ, {"BOT_TOKEN": "", "CHAT_ID": ""}):
            out = self.run_notify("failure")
        self.assertIn("not set", out)
        self.assertEqual(FakeApis.sent, [])


if __name__ == "__main__":
    unittest.main()
