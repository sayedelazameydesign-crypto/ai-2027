#!/usr/bin/env python3
"""Tests for the gated Render deploy — a deploy must never be claimed, only proven.

No network: the Render API is a routing fake, and the reachability probe is
injected. Run: python3 scripts/test_render_deploy.py -v
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "render_control_adapter"))

import adapter  # noqa: E402
import render_deploy as rd  # noqa: E402

KEY = "rnd_" + "T" * 24
SERVICE = {"id": "srv-1", "name": "ai-2027-live-agent",
           "suspended": "not_suspended",
           "serviceDetails": {"url": "https://ai-2027-live-agent.onrender.com"}}


class Reach:
    def __init__(self, reachable: bool, error: str | None = None):
        self.reachable = reachable
        self.error = error
        self.status = 401 if reachable else None


def routing_transport(*, deploy_statuses=("build_in_progress", "live"),
                      services=(SERVICE,), service=SERVICE, deployed=True):
    """A fake Render API that routes by (method, path) and records every call."""
    calls: list[dict] = []
    statuses = list(deploy_statuses)

    def transport(method, url, headers, body, timeout):
        path = url.split("api.render.com/v1", 1)[-1].split("?")[0]
        calls.append({"method": method, "path": path, "headers": dict(headers)})
        if path == "/owners":
            return 200, {}, json.dumps([{"owner": {"id": "own-1"}}]).encode()
        if path.startswith("/services/") and path.endswith("/deploys") and method == "POST":
            if not deployed:
                return 400, {}, json.dumps({"message": "deploy refused"}).encode()
            return 201, {}, json.dumps({"deploy": {"id": "dep-1",
                                                   "status": "created"}}).encode()
        if "/deploys/" in path and method == "GET":
            status = statuses.pop(0) if len(statuses) > 1 else statuses[0]
            return 200, {}, json.dumps({"deploy": {"id": "dep-1",
                                                   "status": status}}).encode()
        if path.startswith("/services/srv-1") and method == "GET":
            return 200, {}, json.dumps({"service": service}).encode()
        if path.startswith("/services") and method == "GET":
            return 200, {}, json.dumps([{"service": s} for s in services]).encode()
        return 404, {}, b'{"message": "not found"}'

    transport.calls = calls  # type: ignore[attr-defined]
    return transport


def client_for(transport):
    return adapter.RenderClient(api_key=KEY, transport=transport,
                                audit=adapter.AuditLog(path=None))


def run_main(argv, transport, reachable=True, env=None):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = rd.main(argv, env=env if env is not None else {"RENDER_API_KEY": KEY},
                       client=client_for(transport),
                       probe_fn=lambda url: Reach(reachable), sleep=lambda _s: None)
    return code, buf.getvalue()


class GateTests(unittest.TestCase):
    def test_01_no_key_is_skipped_and_nothing_is_sent(self):
        transport = routing_transport()
        code, out = run_main(["--json"], transport, env={})
        self.assertEqual(code, rd.NO_KEY)
        self.assertIn("skipped_no_key", out)
        self.assertEqual(transport.calls, [])

    def test_02_an_unreachable_api_is_blocked_not_failed(self):
        """Measured in earlier sessions: api.render.com resets TLS here."""
        transport = routing_transport()
        code, out = run_main(["--json"], transport, reachable=False)
        self.assertEqual(code, rd.BLOCKED)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "blocked_unreachable")
        self.assertEqual(payload["mutations_sent"], 0)
        self.assertEqual(transport.calls, [], "a blocked run must not call the API")

    def test_03_without_confirmation_nothing_is_deployed(self):
        transport = routing_transport()
        code, out = run_main(["--json"], transport)
        payload = json.loads(out)
        self.assertEqual(code, rd.OK)
        self.assertEqual(payload["status"], "dry_run")
        self.assertFalse(payload["plan"]["allowed"])
        self.assertEqual(payload["mutations_sent"], 0)
        self.assertFalse([c for c in transport.calls if c["method"] == "POST"])

    def test_04_a_wrong_confirmation_is_refused_before_anything_is_sent(self):
        transport = routing_transport()
        code, out = run_main(["--json", "--confirm", "some-other-service"], transport)
        self.assertEqual(code, rd.REFUSED)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "refused_confirmation_mismatch")
        self.assertEqual(payload["mutations_sent"], 0)
        self.assertEqual([c for c in transport.calls if c["method"] == "POST"], [])

    def test_05_an_unknown_service_is_reported_not_guessed(self):
        transport = routing_transport(services=())
        code, out = run_main(["--json"], transport)
        self.assertEqual(code, rd.FAILED)
        self.assertEqual(json.loads(out)["status"], "service_not_found")


class DeployTests(unittest.TestCase):
    def test_10_a_verified_deploy_requires_a_terminal_state_and_liveness(self):
        transport = routing_transport(deploy_statuses=["build_in_progress", "live"])
        code, out = run_main(["--json", "--confirm", SERVICE["name"]], transport)
        payload = json.loads(out)
        self.assertEqual(code, rd.OK)
        self.assertTrue(payload["verified"])
        self.assertEqual(payload["status"], "verified_live")
        self.assertEqual(payload["deploy_id"], "dep-1")
        self.assertEqual(payload["mutations_sent"], 1)
        posts = [c for c in transport.calls if c["method"] == "POST"]
        self.assertEqual(len(posts), 1, "exactly one mutation, ever")

    def test_11_a_failed_build_is_a_failure_not_a_success(self):
        transport = routing_transport(deploy_statuses=["build_failed"])
        code, out = run_main(["--json", "--confirm", SERVICE["name"]], transport)
        payload = json.loads(out)
        self.assertEqual(code, rd.FAILED)
        self.assertFalse(payload["verified"])
        self.assertEqual(payload["status"], "deploy_not_verified")

    def test_12_a_suspended_service_fails_the_liveness_check(self):
        suspended = dict(SERVICE, suspended="suspended")
        transport = routing_transport(deploy_statuses=["live"], service=suspended)
        code, out = run_main(["--json", "--confirm", SERVICE["name"]], transport)
        payload = json.loads(out)
        self.assertEqual(code, rd.FAILED)
        self.assertFalse(payload["verified"])
        self.assertEqual(payload["status"], "live_check_failed")

    def test_13_no_wait_admits_it_did_not_verify(self):
        transport = routing_transport()
        code, out = run_main(["--json", "--no-wait", "--confirm", SERVICE["name"]],
                             transport)
        payload = json.loads(out)
        self.assertEqual(code, rd.FAILED)
        self.assertFalse(payload["verified"])
        self.assertEqual(payload["status"], "accepted")
        self.assertEqual(len([c for c in transport.calls if c["method"] == "POST"]), 1)

    def test_14_the_deploy_call_carries_an_approval_token(self):
        """The adapter refuses mutations without one; prove we actually mint it."""
        transport = routing_transport(deploy_statuses=["live"])
        code, _ = run_main(["--json", "--confirm", SERVICE["name"]], transport)
        self.assertEqual(code, rd.OK)
        client = client_for(routing_transport())
        with self.assertRaises(adapter.PolicyError):
            client.deploy("srv-1")          # no token ⇒ refused by the adapter itself


class DisclosureTests(unittest.TestCase):
    def test_20_the_key_never_appears_in_output(self):
        transport = routing_transport(deploy_statuses=["live"])
        code, out = run_main(["--confirm", SERVICE["name"]], transport)
        self.assertEqual(code, rd.OK)
        self.assertNotIn(KEY, out)
        self.assertNotIn(KEY[:4], out)
        self.assertIn("sha256:", out)

    def test_21_the_key_never_appears_in_the_json_report(self):
        transport = routing_transport(deploy_statuses=["live"])
        code, out = run_main(["--json", "--confirm", SERVICE["name"]], transport)
        self.assertNotIn(KEY, out)
        self.assertTrue(json.loads(out)["key_present"])

    def test_22_a_provider_error_that_quotes_the_token_is_scrubbed(self):
        self.assertEqual(rd.scrub(f"bad token {KEY}", KEY), "bad token [redacted-key]")
        self.assertEqual(rd.scrub(None, KEY), None)

    def test_23_the_report_file_contains_no_secret(self):
        transport = routing_transport(deploy_statuses=["live"])
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            code, _ = run_main(["--report", str(path), "--confirm", SERVICE["name"]],
                               transport)
            self.assertEqual(code, rd.OK)
            self.assertNotIn(KEY, path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
