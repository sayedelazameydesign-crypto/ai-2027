#!/usr/bin/env python3
"""CI gate: a public claim that loses its evidence must break the build.

    python3 launch/test_launch_claims.py -v

Stdlib only.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCH = ROOT / "launch"
PUBLISHABLE = ("case-study-deterministic-core.md", "show-hn.md",
               "build-in-public.md", "producthunt.md", "founder-posts.md")

REQUIRED_EVIDENCE_ASSETS = [
    "scripts/generate_launch_evidence.py", "scripts/ci_guard.py",
    "scripts/test_ci_guard.py", "live-agent/bench.py", "live-agent/test_server.py",
    "command-hud/redteam.py", "command-hud/audit.py", "command-hud/test_hud.py",
    "docs/BENCHMARKS.md", "docs/REPOSITORY-AUDIT.md",
    "docs/PHASE-0-READINESS.md", "website/demo.html", "website/kernel-demo.js",
    "packaging/agi-kernel/pyproject.toml", "packaging/agi-kernel/build_wheel.sh",
    ".github/workflows/ci.yml", ".github/workflows/pages.yml",
    "launch/PROOF-LEDGER.md", "launch/hn-comments-playbook.md",
    "launch/REAL-WORLD-CASE-STUDY.md", "launch/production_evidence.py",
]


def local_links(text: str):
    for raw in re.findall(r"\]\(([^)]+)\)", text):
        target = raw.split()[0].strip("<>").split("#")[0]
        if not target or target.startswith(("http://", "https://", "mailto:", "data:")):
            continue
        yield target


class ClaimTests(unittest.TestCase):
    def test_01_all_local_links_resolve(self):
        broken = []
        for md in sorted(LAUNCH.glob("*.md")):
            for link in local_links(md.read_text(encoding="utf-8")):
                if not (md.parent / link).resolve().exists():
                    broken.append(f"{md.name}: {link}")
        self.assertEqual(broken, [], "broken local links: " + "; ".join(broken))

    def test_02_every_evidence_asset_exists(self):
        missing = [p for p in REQUIRED_EVIDENCE_ASSETS if not (ROOT / p).exists()]
        self.assertEqual(missing, [], "missing evidence assets: " + ", ".join(missing))

    def test_03_case_study_declares_its_measurement(self):
        s = (LAUNCH / "case-study-deterministic-core.md").read_text(encoding="utf-8")
        self.assertIn("2026-11-17", s)
        self.assertIn("9ad27fc", s)
        self.assertIn("generate_launch_evidence.py --assert", s)
        self.assertIn("spread", s.lower())
        self.assertIn("What I got wrong", s)

    def test_04_no_unqualified_marketing_claims(self):
        banned = ["guaranteed", "blazing fast", "revolutionary", "game-changing",
                  "100% error-free", "unbreakable", "zero latency"]
        for name in PUBLISHABLE:
            low = (LAUNCH / name).read_text(encoding="utf-8").lower()
            for phrase in banned:
                self.assertNotIn(phrase, low, f"{name} contains banned claim {phrase!r}")
            for line in low.splitlines():
                if "100/100 denied" in line:
                    self.assertTrue(any(w in line for w in ("not", "refuse", "never")),
                                    f"{name} asserts a fake badge: {line[:100]}")

    def test_05_proof_ledger_covers_all_three_strategies(self):
        s = (LAUNCH / "PROOF-LEDGER.md").read_text(encoding="utf-8")
        for token in ("Measured claims", "Exact claims", "Structural claims",
                      "Known defects", "Claims we REFUSE", "Mechanical enforcement"):
            self.assertIn(token, s)
        self.assertIn("generate_launch_evidence.py", s)

    def test_06_real_world_case_study_is_still_unpublishable(self):
        s = (LAUNCH / "REAL-WORLD-CASE-STUDY.md").read_text(encoding="utf-8")
        self.assertIn("NOT PUBLISHABLE", s)
        self.assertIn("UNFILLED", s)
        self.assertIn("لن أختلق", s)

    def test_07_comment_playbook_is_grounded(self):
        s = (LAUNCH / "hn-comments-playbook.md").read_text(encoding="utf-8")
        self.assertGreaterEqual(s.count("### "), 12)
        for token in ("core/verifier.py", "core/policy.py", "redteam.py",
                      "docs/REPOSITORY-AUDIT.md", "NOT VERIFIED"):
            self.assertIn(token, s)

    def test_08_build_in_public_series_has_evidence_gates(self):
        s = (LAUNCH / "build-in-public.md").read_text(encoding="utf-8")
        self.assertIn("PROOF-LEDGER.md", s)
        self.assertGreaterEqual(s.count("| "), 12)
        self.assertIn("⏳", s)   # some articles are explicitly gated
        self.assertIn("🔴", s)   # one is explicitly blocked

    def test_09_production_harness_refuses_empty_data(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "empty.json"
            tpl = subprocess.run([sys.executable, str(LAUNCH / "production_evidence.py"),
                                  "--template"], cwd=ROOT, capture_output=True,
                                 text=True, timeout=30)
            p.write_text(tpl.stdout)
            r = subprocess.run([sys.executable, str(LAUNCH / "production_evidence.py"),
                                "--input", str(p)], cwd=ROOT, capture_output=True,
                               text=True, timeout=30)
            self.assertEqual(r.returncode, 1, "empty template must be REFUSED")
            self.assertIn("NO EVIDENCE", r.stderr)

    def test_10_production_harness_verifies_real_shaped_data(self):
        with tempfile.TemporaryDirectory() as d:
            p, out = Path(d) / "jobs.json", Path(d) / "evidence.json"
            row = {"job_id": "TEST-1", "measured_at": "2026-11-17",
                   "measurement_method": "test fixture",
                   "source": "synthetic — NOT publishable",
                   "screens": 4, "separation_minutes": 10,
                   "ink_ml": 100, "reprint_count": 1, "notes": None}
            p.write_text(json.dumps({"jobs": [dict(row, stage="before"),
                                              dict(row, stage="after",
                                                   separation_minutes=5)]}))
            r = subprocess.run([sys.executable, str(LAUNCH / "production_evidence.py"),
                                "--input", str(p), "--out", str(out)],
                               cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            d2 = json.loads(out.read_text())
            self.assertEqual(d2["status"], "VERIFIED")
            self.assertEqual(len(d2["input_sha256"]), 64)
            self.assertIn("n < 20", d2["sample_warning"])
            v = subprocess.run([sys.executable, str(LAUNCH / "production_evidence.py"),
                                "--verify", "--input", str(p), "--out", str(out)],
                               cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(v.returncode, 0)
            self.assertIn("VERIFIED", v.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
