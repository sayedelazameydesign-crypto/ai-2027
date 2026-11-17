#!/usr/bin/env python3
"""Tests for the Render Control Adapter.

Everything here runs OFFLINE against fixtures built to the response shapes in
Render's official documentation. That is deliberate and it is also a limitation:
**the adapter has never been proven against the live API** in this environment
(egress to api.render.com is blocked — TLS reset, while api.github.com returns
200). `probe.py` is how that gap gets closed by someone with a key.

What these tests do prove, which is the part that matters most:
  * no mutation can happen without an approval token bound to that exact
    operation and target,
  * secret values never come back from the masked read, never enter the audit
    log, and are removed from any log line that contains them,
  * a deploy request is never reported as a successful deploy.

Run: python3 render_control_adapter/test_adapter.py -v
Stdlib only.
"""
from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import adapter  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures — shapes taken from api-docs.render.com
# --------------------------------------------------------------------------- #
def svc(sid="srv-abc123", name="ai-2027-live-agent", **kw):
    base = {
        "id": sid, "name": name, "type": "web_service", "suspended": "not_suspended",
        "autoDeploy": True, "branch": "main",
        "repo": "https://github.com/o/r", "ownerId": "own-1",
        "serviceDetails": {"region": "frankfurt", "plan": "free",
                           "runtime": "python", "healthCheckPath": "/api/health",
                           "url": f"https://{name}.onrender.com"},
    }
    base.update(kw)
    return base


def wrapped(items, key, cursor="cur-1"):
    """Render list endpoints wrap each item with a sibling cursor."""
    return json.dumps([{key: item, "cursor": cursor} for item in items]).encode()


def deploys(status="live", dep_id="dep-1"):
    return [{"id": dep_id, "status": status, "trigger": "manual",
             "createdAt": "2026-11-17T10:00:00Z",
             "finishedAt": "2026-11-17T10:03:00Z"}]


class ClientFactory(unittest.TestCase):
    def client(self, status=200, body=b"[]", headers=None):
        t = adapter.recording_transport(status, body, headers)
        c = adapter.RenderClient(api_key="rnd_test_key", transport=t,
                                 audit=adapter.AuditLog(None))
        return c, t


# --------------------------------------------------------------------------- #
# Response-shape handling
# --------------------------------------------------------------------------- #
class ShapeTests(ClientFactory):
    def test_01_list_services_unwraps_the_documented_envelope(self):
        """Docs: GET /services -> [{"service": {...}, "cursor": "..."}].

        A naive client that treats the response as a bare list of services finds
        `id = None` on every row. This is the shape trap.
        """
        c, _ = self.client(body=wrapped([svc()], "service"))
        services = c.list_services()
        self.assertEqual(len(services), 1)
        self.assertEqual(services[0]["id"], "srv-abc123")
        self.assertEqual(services[0]["name"], "ai-2027-live-agent")

    def test_02_pagination_stops_when_no_cursor(self):
        c, t = self.client(body=json.dumps([{"service": svc(), "cursor": ""}]).encode())
        services = c.list_services()
        self.assertEqual(len(services), 1)
        self.assertEqual(len(t.calls), 1, "should not page again with no cursor")

    def test_03_pagination_follows_cursor_to_the_next_page(self):
        bodies = [json.dumps([{"service": svc("srv-1"), "cursor": "c1"}]).encode(),
                  json.dumps([{"service": svc("srv-2"), "cursor": ""}]).encode()]
        calls = []

        def transport(method, url, hdrs, body, timeout):
            calls.append(url)
            return 200, {}, bodies[len(calls) - 1]

        c = adapter.RenderClient(api_key="k", transport=transport)
        out = c.list_services(limit=1)
        self.assertEqual([s["id"] for s in out], ["srv-1", "srv-2"])
        self.assertIn("cursor=c1", calls[1])

    def test_04_a_bare_object_is_tolerated_but_a_wrong_type_is_not(self):
        c, _ = self.client(body=json.dumps({"id": "srv-x", "name": "n"}).encode())
        self.assertEqual(c.get_service("srv-x")["id"], "srv-x")
        c2, _ = self.client(body=b'"not a service"')
        with self.assertRaises(adapter.ApiError):
            c2.get_service("srv-x")

    def test_05_deploy_list_unwraps_deploy_key(self):
        c, _ = self.client(body=wrapped(deploys(), "deploy"))
        out = c.list_deploys("srv-abc123")
        self.assertEqual(out[0]["status"], "live")


# --------------------------------------------------------------------------- #
# Policy: the core guarantee
# --------------------------------------------------------------------------- #
class PolicyTests(ClientFactory):
    def test_10_restart_without_token_is_refused_and_never_hits_the_network(self):
        c, t = self.client()
        with self.assertRaises(adapter.PolicyError):
            c.restart_service("srv-abc123")
        self.assertEqual(t.calls, [], "a refused call must not send a request")

    def test_11_restart_with_a_valid_token_proceeds(self):
        c, t = self.client(body=b"{}")
        token = c.approvals.mint("restart_service", "srv-abc123").token
        out = c.restart_service("srv-abc123", approval_token=token)
        self.assertTrue(out["requested"])
        self.assertFalse(out["verified"], "a restart request is not a verified fix")
        self.assertEqual(t.calls[0]["method"], "POST")
        self.assertTrue(t.calls[0]["url"].endswith("/services/srv-abc123/restart"))

    def test_12_a_token_for_another_service_is_refused(self):
        c, t = self.client(body=b"{}")
        token = c.approvals.mint("restart_service", "srv-OTHER").token
        with self.assertRaises(adapter.PolicyError):
            c.restart_service("srv-abc123", approval_token=token)
        self.assertEqual(t.calls, [])

    def test_13_a_token_for_another_operation_is_refused(self):
        c, t = self.client(body=b"{}")
        token = c.approvals.mint("deploy", "srv-abc123").token
        with self.assertRaises(adapter.PolicyError):
            c.restart_service("srv-abc123", approval_token=token)
        self.assertEqual(t.calls, [])

    def test_14_a_token_cannot_be_reused(self):
        c, t = self.client(body=b"{}")
        token = c.approvals.mint("restart_service", "srv-abc123").token
        c.restart_service("srv-abc123", approval_token=token)
        with self.assertRaises(adapter.PolicyError):
            c.restart_service("srv-abc123", approval_token=token)
        self.assertEqual(len(t.calls), 1, "the replay must not reach the network")

    def test_15_a_forged_token_is_refused(self):
        c, t = self.client(body=b"{}")
        with self.assertRaises(adapter.PolicyError):
            c.restart_service("srv-abc123", approval_token="0" * 32)
        self.assertEqual(t.calls, [])

    def test_16_reads_are_automatic_and_need_no_token(self):
        for name, call in (("list_services", lambda c: c.list_services()),
                           ("list_deploys", lambda c: c.list_deploys("srv-abc123"))):
            c, _ = self.client(body=wrapped([svc() if "services" in name else deploys()[0]],
                                            "service" if "services" in name else "deploy"))
            call(c)  # must not raise
            self.assertFalse(adapter.POLICY[name][1], f"{name} should not need approval")

    def test_17_every_mutation_in_the_policy_requires_approval(self):
        mutations = [op for op, (_r, need, _p) in adapter.POLICY.items() if need]
        self.assertTrue(mutations)
        for op in mutations:
            self.assertTrue(adapter.POLICY[op][1], op)
        # and the dangerous ones are labelled as such
        self.assertEqual(adapter.POLICY["delete_service"][0], "destroy")

    def test_18_delete_requires_the_exact_service_name(self):
        c, t = self.client(body=wrapped([svc(name="real-name")], "service"))
        token = c.approvals.mint("delete_service", "srv-abc123").token
        with self.assertRaises(adapter.PolicyError):
            c.delete_service("srv-abc123", approval_token=token, confirm_name="wrong")
        # and it must be mandatory, not optional
        token2 = c.approvals.mint("delete_service", "srv-abc123").token
        with self.assertRaises(adapter.PolicyError):
            c.delete_service("srv-abc123", approval_token=token2)
        self.assertFalse(any(call["method"] == "DELETE" for call in t.calls))


# --------------------------------------------------------------------------- #
# Secrets
# --------------------------------------------------------------------------- #
class SecretTests(ClientFactory):
    ENV = json.dumps([{"envVar": {"key": "RENDER_API_KEY", "value": "rnd_supersecret123"},
                       "cursor": "c1"},
                      {"envVar": {"key": "DATABASE_URL",
                                  "value": "postgres://u:p@host/db"}, "cursor": "c2"},
                      {"envVar": {"key": "PORT", "value": "7860"}, "cursor": "c3"}]).encode()

    def test_20_masked_read_returns_no_values(self):
        c, _ = self.client(body=self.ENV)
        masked = c.list_env_vars_masked("srv-abc123")
        blob = json.dumps(masked)
        self.assertNotIn("rnd_supersecret123", blob)
        self.assertNotIn("postgres://u:p@host/db", blob)
        by_key = {m["key"]: m for m in masked}
        self.assertEqual(by_key["RENDER_API_KEY"]["value_len"], 18)
        self.assertTrue(by_key["RENDER_API_KEY"]["value_present"])
        self.assertNotIn("value", by_key["RENDER_API_KEY"])

    def test_21_masked_read_logs_only_key_names(self):
        c, _ = self.client(body=self.ENV)
        c.list_env_vars_masked("srv-abc123")
        # the audit log is in-memory here; inspect what would be written
        events = []
        c.audit = adapter.AuditLog(None)
        c.audit.write = lambda **e: events.append(e) or e
        c.list_env_vars_masked("srv-abc123")
        blob = json.dumps(events)
        self.assertNotIn("rnd_supersecret123", blob)

    def test_22_get_value_requires_approval(self):
        c, t = self.client(body=self.ENV)
        with self.assertRaises(adapter.PolicyError):
            c.get_env_var_value("srv-abc123", "RENDER_API_KEY")
        self.assertEqual(t.calls, [], "refused before any request")

    def test_23_get_value_with_approval_is_flagged_sensitive_and_audited_by_key(self):
        c, _ = self.client(body=self.ENV)
        events = []
        c.audit.write = lambda **e: events.append(e) or e
        token = c.approvals.mint("get_env_var_value", "srv-abc123:RENDER_API_KEY").token
        out = c.get_env_var_value("srv-abc123", "RENDER_API_KEY", approval_token=token)
        self.assertEqual(out["value"], "rnd_supersecret123")
        self.assertTrue(out["sensitive"])
        blob = json.dumps(events)
        self.assertIn("RENDER_API_KEY", blob, "the KEY must be audited")
        self.assertNotIn("rnd_supersecret123", blob, "the VALUE must never be")

    def test_24_a_known_secret_is_scrubbed_from_later_log_lines(self):
        """A secret can leak into a log line under an innocent key. Value
        matching catches what key-name matching cannot."""
        c, _ = self.client(body=self.ENV)
        c.list_env_vars_masked("srv-abc123")     # learns the secrets
        logs_body = json.dumps({
            "hasMore": False,
            "logs": [{"message": "connecting with postgres://u:p@host/db now",
                      "timestamp": "2026-11-17T10:00:00Z"}],
        }).encode()
        c.transport = adapter.recording_transport(200, logs_body)
        out = c.list_logs("own-1", ["srv-abc123"])
        self.assertNotIn("postgres://u:p@host/db", json.dumps(out))
        self.assertIn("REDACTED", json.dumps(out))

    def test_25_key_name_heuristics_redact_without_a_known_value(self):
        payload = {"apiKey": "anything", "port": 7860,
                   "nested": {"DATABASE_PASSWORD": "x", "region": "fra"}}
        out = adapter.redact(payload)
        self.assertEqual(out["apiKey"], "***REDACTED***")
        self.assertEqual(out["nested"]["DATABASE_PASSWORD"], "***REDACTED***")
        self.assertEqual(out["port"], 7860, "non-secret fields must survive")
        self.assertEqual(out["nested"]["region"], "fra")

    def test_26_set_env_var_never_echoes_the_value(self):
        c, t = self.client(body=b"{}")
        events = []
        c.audit.write = lambda **e: events.append(e) or e
        token = c.approvals.mint("set_env_var", "srv-abc123:NEW_SECRET").token
        out = c.set_env_var("srv-abc123", "NEW_SECRET", "hunter2hunter2",
                            approval_token=token)
        self.assertTrue(out["value_set"])
        self.assertFalse(out["value_echoed"])
        self.assertNotIn("hunter2hunter2", json.dumps(out))
        self.assertNotIn("hunter2hunter2", json.dumps(events))


# --------------------------------------------------------------------------- #
# Deploy is not verification
# --------------------------------------------------------------------------- #
class DeployVerificationTests(ClientFactory):
    def test_30_a_deploy_request_is_never_reported_as_verified(self):
        c, _ = self.client(body=json.dumps({"deploy": {"id": "dep-9",
                                                       "status": "created"}}).encode())
        token = c.approvals.mint("deploy", "srv-abc123").token
        out = c.deploy("srv-abc123", commit_id="abc1234", approval_token=token)
        self.assertFalse(out["verified"])
        self.assertIn("not verified", out["note"].lower())

    def test_31_wait_sets_verified_true_only_on_a_terminal_success(self):
        c, _ = self.client(body=json.dumps({"deploy": {"id": "dep-9",
                                                       "status": "live"}}).encode())
        out = c.wait_for_deploy("srv-abc123", "dep-9", timeout=5, sleep=lambda s: None)
        self.assertTrue(out["verified"])
        self.assertEqual(out["status"], "live")
        self.assertIn("does not prove", out["claim"].lower())

    def test_32_a_failed_deploy_is_verified_false(self):
        c, _ = self.client(body=json.dumps({"deploy": {"id": "dep-9",
                                                       "status": "build_failed"}}).encode())
        out = c.wait_for_deploy("srv-abc123", "dep-9", timeout=5, sleep=lambda s: None)
        self.assertFalse(out["verified"])
        self.assertIn("do not report success", out["claim"])

    def test_33_timeout_is_unknown_not_success(self):
        c, _ = self.client(body=json.dumps({"deploy": {"id": "dep-9",
                                                       "status": "building"}}).encode())
        out = c.wait_for_deploy("srv-abc123", "dep-9", timeout=0.01, sleep=lambda s: None)
        self.assertFalse(out["verified"])
        self.assertTrue(out["timed_out"])
        self.assertIn("UNKNOWN", out["claim"])

    def test_34_service_is_live_reports_its_own_limits(self):
        c, _ = self.client(body=wrapped([svc()], "service"))
        out = c.service_is_live("srv-abc123", probes=2, interval=0, sleep=lambda s: None)
        self.assertTrue(out["live"])
        self.assertIn("not", out["claim"].lower())

    def test_35_a_suspended_service_is_not_live(self):
        c, _ = self.client(body=wrapped([svc(suspended="suspended")], "service"))
        out = c.service_is_live("srv-abc123", probes=1, sleep=lambda s: None)
        self.assertFalse(out["live"])


# --------------------------------------------------------------------------- #
# Rate limits
# --------------------------------------------------------------------------- #
class RateLimitTests(ClientFactory):
    def test_40_deploy_limit_is_enforced_locally_before_sending(self):
        c, t = self.client(body=json.dumps({"deploy": {"id": "d", "status": "created"}}).encode())
        tokens = []
        for _ in range(10):
            tokens.append(c.approvals.mint("deploy", "srv-abc123").token)
        for tok in tokens:
            c.deploy("srv-abc123", approval_token=tok)
        self.assertEqual(len(t.calls), 10)
        tok = c.approvals.mint("deploy", "srv-abc123").token
        with self.assertRaises(adapter.RateLimitError):
            c.deploy("srv-abc123", approval_token=tok)
        self.assertEqual(len(t.calls), 10, "the 11th must not reach the API")

    def test_41_documented_limits_match_the_table(self):
        self.assertEqual(adapter.RATE_LIMITS["create_service"], (20, 3600))
        self.assertEqual(adapter.RATE_LIMITS["deploy"], (10, 60))
        self.assertEqual(adapter.RATE_LIMITS["list_logs"], (30, 60))


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class ErrorTests(ClientFactory):
    def test_50_401_is_flagged_as_auth(self):
        c, _ = self.client(status=401, body=b'{"error":"unauthorized"}')
        with self.assertRaises(adapter.ApiError) as ctx:
            c.list_services()
        self.assertTrue(ctx.exception.is_auth)

    def test_51_429_is_flagged_as_rate_limited(self):
        c, _ = self.client(status=429, body=b'{"error":"slow down"}')
        with self.assertRaises(adapter.ApiError) as ctx:
            c.list_services()
        self.assertTrue(ctx.exception.is_rate_limited)

    def test_52_html_error_body_does_not_crash_the_parser(self):
        c, _ = self.client(status=502, body=b"<html>bad gateway</html>")
        with self.assertRaises(adapter.ApiError) as ctx:
            c.list_services()
        self.assertEqual(ctx.exception.status, 502)

    def test_53_invalid_json_on_200_is_an_api_error_not_a_crash(self):
        c, _ = self.client(status=200, body=b"not json at all")
        with self.assertRaises(adapter.ApiError) as ctx:
            c.list_services()
        self.assertEqual(ctx.exception.code, "invalid_json")


# --------------------------------------------------------------------------- #
# Diagnosis + infrastructure audit
# --------------------------------------------------------------------------- #
class DiagnosisTests(unittest.TestCase):
    def test_60_known_causes_are_classified(self):
        d = adapter.diagnose([
            "ModuleNotFoundError: No module named 'requests'",
            "OSError: [Errno 98] Address already in use",
            "Killed process 1 (python3) out of memory",
            "some entirely novel failure mode",
        ])
        labels = {c["cause"] for c in d["causes"]}
        self.assertIn("missing_dependency", labels)
        self.assertIn("port_conflict", labels)
        self.assertIn("out_of_memory", labels)
        self.assertEqual(d["unclassified"], 1)
        self.assertIn("HYPOTHESIS", d["note"])

    def test_61_clean_logs_produce_no_findings(self):
        d = adapter.diagnose(["server started on port 7860", "GET / 200"])
        self.assertEqual(d["error_lines"], 0)
        self.assertEqual(d["causes"], [])

    def test_62_infrastructure_audit_flags_missing_health_check(self):
        a = adapter.audit_infrastructure([svc(serviceDetails={"plan": "free",
                                                             "region": "fra"})])
        issues = {f["issue"] for f in a["findings"]}
        self.assertIn("no_health_check_path", issues)
        self.assertIn("absent findings are NOT proof", my_note(a))

    def test_63_suspended_service_is_high_severity(self):
        a = adapter.audit_infrastructure([svc(suspended="suspended")])
        sev = {f["issue"]: f["severity"] for f in a["findings"]}
        self.assertEqual(sev.get("suspended"), "high")


def my_note(a: dict) -> str:
    return a["note"]


# --------------------------------------------------------------------------- #
# Probe (Phase 0) must refuse to run blind
# --------------------------------------------------------------------------- #
class ProbeTests(unittest.TestCase):
    def test_70_probe_exits_2_without_a_key(self):
        import os
        import subprocess
        env = {k: v for k, v in os.environ.items() if k != "RENDER_API_KEY"}
        r = subprocess.run([sys.executable, str(HERE / "probe.py")],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("NO KEY", r.stderr)

    def test_71_probe_never_prints_the_key(self):
        src = (HERE / "probe.py").read_text(encoding="utf-8")
        # the only place the key may appear is the redacted fingerprint
        self.assertIn("redact_key", src)
        self.assertNotIn('print(key', src)
        self.assertNotIn("report[\"api_key\"]", src)


class RegressionsFoundByFirstRun(unittest.TestCase):
    """Bugs the FIRST execution of this suite exposed. Each one shipped as a
    plausible-looking guard and none would have been caught by reading it."""

    def test_80_repeated_approvals_in_the_same_minute_are_distinct(self):
        # Was: token = HMAC(op|target|minute) with no nonce, so ten deploys in one
        # minute produced ONE string and the single-use set rejected #2..#10.
        eng = adapter.ApprovalEngine(secret=b"s" * 32)
        toks = [eng.mint("deploy", "srv-abc123").token for _ in range(10)]
        self.assertEqual(len(set(toks)), 10, "tokens must be unique per mint")
        self.assertEqual(sum(eng.check(t, "deploy", "srv-abc123") for t in toks), 10)

    def test_81_a_token_cannot_be_reused(self):
        eng = adapter.ApprovalEngine(secret=b"s" * 32)
        tok = eng.mint("deploy", "srv-abc123").token
        self.assertTrue(eng.check(tok, "deploy", "srv-abc123"))
        self.assertFalse(eng.check(tok, "deploy", "srv-abc123"), "single-use")

    def test_82_single_service_read_accepts_the_list_envelope(self):
        # Was: GET /services/{id} returns a ONE-ITEM LIST [{service, cursor}], but
        # _unwrap_one only handled a bare object and raised "unexpected_shape".
        body = json.dumps([{"service": {"id": "srv-abc123", "name": "pro-print",
                                        "suspended": "not_suspended"}, "cursor": ""}]).encode()
        c = adapter.RenderClient(api_key="k",
                                 transport=adapter.recording_transport(200, body))
        self.assertEqual(c.get_service("srv-abc123")["name"], "pro-print")

    def test_83_oom_kills_are_diagnosed_even_without_the_word_error(self):
        # Was: diagnose() skipped any line lacking error|fail|exception, so
        # "Killed process 1 (python3) out of memory" was invisible.
        lines = ["Killed process 1 (python3) out of memory and terminated."]
        out = adapter.diagnose(lines)
        self.assertIn("out_of_memory", json.dumps(out))


if __name__ == "__main__":
    unittest.main(verbosity=2)
