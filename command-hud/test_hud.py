#!/usr/bin/env python3
"""End-to-end tests for the Unified Command HUD against the REAL kernel.

Starts the actual HTTP server in a background thread on an ephemeral port and
calls the real endpoints with urllib. No mocks of the kernel, no simulation.

Run: python3 command-hud/test_hud.py -v
Stdlib only.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import audit as audit_mod        # noqa: E402
import kernel_bridge            # noqa: E402
import redteam as redteam_mod   # noqa: E402
import server as hud            # noqa: E402


class HudTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ai2027-hud-test-"))
        hud.SRV = kernel_bridge.load(runtime=cls.tmp, fresh=True)
        hud.STATE.update({"frozen": False, "history": [], "redteam": None,
                          "audit": audit_mod.build(fast=True), "boot_errors": []})
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), hud.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        hud.STATE["frozen"] = False

    # ---------------------------- helpers -------------------------------- #
    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=30) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()

    def post(self, path, payload=None):
        body = json.dumps(payload or {}).encode()
        req = urllib.request.Request(self.base + path, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())

    # ------------------------------ tests -------------------------------- #
    def test_01_health_declares_no_llm(self):
        code, _, raw = self.get("/api/health")
        d = json.loads(raw)
        self.assertEqual(code, 200)
        self.assertTrue(d["ok"])
        self.assertTrue(d["no_llm"])
        self.assertIn("REAL", d["kernel"])

    def test_02_state_contains_audit_and_honesty(self):
        _, _, raw = self.get("/api/state")
        d = json.loads(raw)
        self.assertEqual(len(d["kernel"]["supported_goals"]), 6)
        self.assertEqual(d["audit"]["summary"]["projects"], 48)
        self.assertIn("kill_switch", d["honesty"])
        self.assertIn("NOT a simulation", d["honesty"]["kernel"])

    def test_03_verified_run_has_independent_sha256(self):
        _, d = self.post("/api/run", {"goal_type": "create_file",
                                      "params": {"path": "proof/a.txt", "content": "hello"}})
        self.assertEqual(d["status"], "VERIFIED")
        self.assertTrue(d["evidence_checks"].get("hash_matches"))
        self.assertEqual(len(d["sha256"]["sha256"]), 64)
        self.assertEqual(d["sha256"]["file"], "proof/a.txt")
        self.assertEqual(d["sha256"]["sha256"],
                         __import__("hashlib").sha256(b"hello").hexdigest())

    def test_04_escape_test_is_denied_with_recorded_reason(self):
        _, d = self.post("/api/escape")
        self.assertEqual(d["status"], "DENIED")
        self.assertIn("escapes the sandbox", d["reason"])
        self.assertEqual(d["sha256"], None)
        self.assertEqual(d["sandbox_files"], [])

    def test_05_pulse_is_a_real_verified_run(self):
        _, d = self.post("/api/pulse")
        self.assertEqual(d["status"], "VERIFIED")
        self.assertEqual(d["source"], "live-pulse")

    def test_06_kill_switch_freezes_dispatch_but_not_verification(self):
        _, k = self.post("/api/kill", {"enabled": True})
        self.assertTrue(k["frozen"])
        _, d = self.post("/api/run", {"goal_type": "create_file",
                                      "params": {"path": "blocked.txt", "content": "x"}})
        self.assertEqual(d["status"], "FROZEN")
        self.assertIn("kernel was NOT called", d["reason"])
        self.assertEqual(d["sha256"], None)
        # verification endpoints deliberately keep working
        _, rt = self.post("/api/redteam")
        self.assertTrue(rt["all_gates_pass"])
        _, k2 = self.post("/api/kill", {"enabled": False})
        self.assertFalse(k2["frozen"])
        _, ok = self.post("/api/pulse")
        self.assertEqual(ok["status"], "VERIFIED")

    def test_07_redteam_100_real_attacks_zero_breaches(self):
        _, r = self.post("/api/redteam")
        self.assertEqual(r["attacks"], 100)
        self.assertEqual(r["expected_met"], 100)
        self.assertEqual(r["breaches"], 0)
        self.assertEqual(r["controls"]["verified"], 10)
        self.assertEqual(r["denied"], 91)
        self.assertEqual(r["contained"], 9)
        self.assertTrue(r["all_gates_pass"])
        self.assertIn("CLOSED 2026-11-17", r["portability_warning"])

    def test_08_audit_is_read_only_and_classifies_honestly(self):
        _, _, raw = self.get("/api/audit")
        a = json.loads(raw)
        self.assertEqual(len(a["projects"]), 48)
        self.assertEqual(a["summary"]["actual_files"], a["summary"]["declared_files"])
        self.assertIn("read-only", a["method"])
        statuses = {p["status"] for p in a["projects"]}
        self.assertTrue(statuses <= {"PROVEN", "DECLARED", "SIMULATED", "NOT VERIFIED", "FAILED"})
        # full-mode probes are the ONLY thing that can earn PROVEN
        self.assertLessEqual(a["summary"]["status_counts"].get("PROVEN", 0), 1)
        self.assertGreater(a["summary"]["critical_high"], 0)

    def test_09_static_files_and_traversal_safety(self):
        code, ctype, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"COMMAND HUD", body)
        code, _, js = self.get("/app.js")
        self.assertEqual(code, 200)
        self.assertIn(b"api/state", js)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/../server.py")
        self.assertEqual(ctx.exception.code, 404)

    def test_10_decision_record_is_downloadable(self):
        _, d = self.post("/api/run", {"goal_type": "create_file",
                                      "params": {"path": "rec.txt", "content": "evidence"}})
        _, _, raw = self.get("/api/record/" + d["run_id"])
        rec = json.loads(raw)
        self.assertEqual(rec["status"], "VERIFIED")
        self.assertTrue(rec["evidence"])

    def test_11_audit_module_and_redteam_case_count(self):
        cases = redteam_mod.build_cases()
        self.assertEqual(len(cases), 100)
        self.assertEqual(len({c["path"] for c in cases}), 100, "attack paths must be distinct")
        a = audit_mod.build(fast=True)
        self.assertEqual(a["summary"]["projects"], 48)
        self.assertEqual(a["mode"], "fast")

    def test_12_audit_refresh_endpoint(self):
        _, d = self.post("/api/audit/refresh")
        self.assertTrue(d["ok"])
        self.assertIn("probes", d["note"])

    def test_13_control_events_do_not_pollute_latency(self):
        """Regression: kill toggles have elapsed_ms=0 and once dragged p50 to 0."""
        hud.STATE["history"] = []
        self.post("/api/kill", {"enabled": True})
        self.post("/api/kill", {"enabled": False})
        hud.STATE["frozen"] = False
        self.post("/api/pulse")
        _, _, raw = self.get("/api/state")
        d = json.loads(raw)
        self.assertEqual(d["counters"]["CONTROL"], 2)
        self.assertEqual(d["timings"]["n"], 1, "only real kernel calls may be timed")
        self.assertGreater(d["timings"]["p50_ms"], 0)

    def test_14_refused_runs_are_logged_in_memory(self):
        """Regression: only VERIFIED runs were offered to memory, so the
        admission log recorded zero refusals while DENIED runs happened."""
        from pathlib import Path as _P
        import tempfile as _tf
        import store as _store
        tmp = _P(_tf.mkdtemp(prefix="hud-mem-"))
        prev = hud.MEM
        hud.MEM = _store.EvidenceMemoryStore(tmp / "m.sqlite",
                                            sandbox_root=hud.SRV.SANDBOXES_DIR)
        try:
            before = hud.MEM.stats()["refused_events"]
            _, d = self.post("/api/run", {"goal_type": "create_file",
                                          "params": {"path": "../../escape.txt",
                                                     "content": "x"}})
            self.assertEqual(d["status"], "DENIED")
            self.assertIn("memory", d)
            self.assertFalse(d["memory"]["admitted"])
            after = hud.MEM.stats()["refused_events"]
            self.assertEqual(after, before + 1, "a DENIED run must be refused AND logged")
            _, ok = self.post("/api/run", {"goal_type": "create_file",
                                           "params": {"path": "mem/ok.txt",
                                                      "content": "fine"}})
            self.assertTrue(ok["memory"]["admitted"])
            st = hud.MEM.stats()
            self.assertEqual(st["memories"], 1)
            self.assertEqual(st["refused_events"], before + 1)
        finally:
            hud.MEM = prev

if __name__ == "__main__":
    unittest.main(verbosity=2)
