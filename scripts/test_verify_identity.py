#!/usr/bin/env python3
"""Tests for archive identity — drift, documented deviation, and blindness.

No network: the GitHub API, git and the preflight are all injected, so this
suite proves the classification logic itself. Run:
    python3 scripts/test_verify_identity.py -v
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import network_preflight as npf  # noqa: E402
import verify_archive_identity as vi  # noqa: E402

REG = {"deviations": {"agi-system": ["core/policy.py"]},
       "generated": {"easy-ai": ["data/manifest.json"]}}
REG_GEN = {"deviations": {}, "generated": {"gen": ["m.json"]}}


class RegisterTests(unittest.TestCase):
    def test_01_the_real_register_is_readable_and_complete(self):
        reg, source = vi.load_register()
        self.assertIn("agi-system", reg["deviations"])
        self.assertIn("ai-store2", reg["deviations"])       # D-2
        self.assertIn("easy-ai", reg["generated"])
        self.assertTrue(source.endswith("deviations.json"))

    def test_02_a_missing_register_falls_back_instead_of_crashing(self):
        reg, source = vi.load_register(Path("/nonexistent/deviations.json"))
        self.assertEqual(reg, vi.DEFAULT_REGISTER)
        self.assertIn("built-in defaults", source)

    def test_03_a_corrupt_register_falls_back_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "deviations.json"
            p.write_text("{not json", encoding="utf-8")
            reg, source = vi.load_register(p)
        self.assertEqual(reg, vi.DEFAULT_REGISTER)
        self.assertIn("built-in defaults", source)


class ClassifyTests(unittest.TestCase):
    def test_10_no_change_is_identical(self):
        r = vi.classify("cela2027", [], [], [], REG)
        self.assertEqual(r["status"], vi.IDENTICAL)

    def test_11_a_documented_change_is_a_deviation_not_drift(self):
        r = vi.classify("agi-system", ["core/policy.py"], [], [], REG)
        self.assertEqual(r["status"], vi.DEVIATION)
        self.assertEqual(r["unexpected"], [])

    def test_12_an_upstream_generated_file_is_volatile_not_drift(self):
        """The real easy-ai case: same bytes we shipped, regenerated upstream."""
        r = vi.classify("easy-ai", ["data/manifest.json"], [], [], REG)
        self.assertEqual(r["status"], vi.GENERATED)
        self.assertEqual(r["generated"], ["data/manifest.json"])

    def test_13_an_undocumented_change_is_drift(self):
        r = vi.classify("cela2027", ["src/app.py"], [], [], REG)
        self.assertEqual(r["status"], vi.DRIFTED)
        self.assertEqual(r["unexpected"], ["src/app.py"])

    def test_14_a_generated_file_that_also_disappeared_is_drift(self):
        """Volatility excuses a changed blob, never a missing or extra one."""
        r = vi.classify("easy-ai", [], ["data/manifest.json"], [], REG)
        self.assertEqual(r["status"], vi.DRIFTED)
        r2 = vi.classify("easy-ai", ["data/manifest.json"], [], ["new.txt"], REG)
        self.assertEqual(r2["status"], vi.DRIFTED)

    def test_15_mixed_documented_and_generated_is_still_a_deviation(self):
        reg = {"deviations": {"p": ["a.py"]}, "generated": {"p": ["m.json"]}}
        r = vi.classify("p", ["a.py", "m.json"], [], [], reg)
        self.assertEqual(r["status"], vi.DEVIATION)
        self.assertTrue(r["mixed"])
        self.assertEqual(r["generated"], ["m.json"])

    def test_16_one_undocumented_file_poisons_an_otherwise_documented_change(self):
        r = vi.classify("agi-system", ["core/policy.py", "core/ledger.py"], [], [], REG)
        self.assertEqual(r["status"], vi.DRIFTED)
        self.assertEqual(r["unexpected"], ["core/ledger.py"])


class FailureKindTests(unittest.TestCase):
    def _with_report(self, egress, exc):
        original = npf.cached_preflight
        npf.cached_preflight = lambda *a, **k: npf.Report(egress, ())
        try:
            return vi.failure_kind(exc)
        finally:
            npf.cached_preflight = original

    def test_20_total_blindness_is_blocked_not_drift(self):
        kind, why = self._with_report(npf.CLOSED, urllib.error.URLError("no route"))
        self.assertEqual(kind, "blocked")
        self.assertIn("no egress", why)

    def test_21_an_http_5xx_is_an_upstream_failure_not_blindness(self):
        exc = urllib.error.HTTPError("https://api.github.com", 502, "bad gateway", {}, None)
        kind, why = self._with_report(npf.OPEN, exc)
        self.assertEqual(kind, "failed")
        self.assertIn("502", why)

    def test_22_a_transport_error_with_working_api_is_a_real_failure(self):
        kind, _ = self._with_report(npf.ALLOWLISTED_ONLY, urllib.error.URLError("reset"))
        self.assertEqual(kind, "failed")


class StrictModeTests(unittest.TestCase):
    """--strict must fire on drift only; --strict-all also on blindness."""

    def _run(self, projects, *, upstream, local, argv):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "deviations.json").write_text(
                json.dumps({"deviations": {}, "generated": {"gen": ["m.json"]}}),
                encoding="utf-8")
            (root / "catalog.json").write_text(json.dumps({
                "projects": [{"name": n, "source": f"https://github.com/o/{n}"}
                             for n in projects]}), encoding="utf-8")
            saved = (vi.ROOT, vi.REGISTER, vi.token, vi.api, vi.local_blobs)
            vi.ROOT = root
            vi.REGISTER = root / "docs" / "deviations.json"
            vi.token = lambda: "t"
            vi.api = lambda url, tok: upstream(url)
            vi.local_blobs = lambda name: local[name]
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    code = vi.main(argv)
                return code, buf.getvalue()
            finally:
                vi.ROOT, vi.REGISTER, vi.token, vi.api, vi.local_blobs = saved

    @staticmethod
    def _upstream_factory(blobs_by_repo):
        """Map both API shapes main() asks for: repo metadata and the git tree."""
        def repo_of(url: str) -> str:
            return url.split("/repos/")[1].split("/")[1]

        def upstream(url):
            if "/git/trees/" in url:
                repo = repo_of(url)
                return {"tree": [{"path": p, "sha": s, "type": "blob"}
                                 for p, s in blobs_by_repo[repo].items()]}
            return {"default_branch": "main"}
        return upstream

    def test_30_generated_drift_does_not_fail_strict(self):
        projects = ["same", "gen"]
        local = {"same": {"a.py": "sha1"}, "gen": {"m.json": "old"}}
        up = self._upstream_factory({"same": {"a.py": "sha1"},
                                     "gen": {"m.json": "new"}})
        code, out = self._run(projects, upstream=up, local=local, argv=["--strict"])
        self.assertEqual(code, 0, out)
        self.assertIn("generated (volatile): 1", out)
        self.assertIn("2/2 accounted for", out)

    def test_31_a_real_drift_fails_strict(self):
        projects = ["same", "bad"]
        local = {"same": {"a.py": "sha1"}, "bad": {"a.py": "edited"}}
        up = self._upstream_factory({"same": {"a.py": "sha1"}, "bad": {"a.py": "orig"}})
        code, out = self._run(projects, upstream=up, local=local, argv=["--strict"])
        self.assertEqual(code, 1, out)
        self.assertIn("! bad", out)

    def test_32_blindness_fails_strict_all_but_not_strict(self):
        projects = ["same", "blind"]
        local = {"same": {"a.py": "sha1"}, "blind": {"a.py": "sha1"}}
        good = self._upstream_factory({"same": {"a.py": "sha1"}})

        def upstream(url):
            if "/blind/" in url:
                raise urllib.error.URLError("no route")
            return good(url)

        original = npf.cached_preflight
        npf.cached_preflight = lambda *a, **k: npf.Report(npf.CLOSED, ())
        try:
            code, out = self._run(projects, upstream=upstream, local=local,
                                  argv=["--strict"])
            self.assertEqual(code, 0, out)
            self.assertIn("BLOCKED", out)
            code2, out2 = self._run(projects, upstream=upstream, local=local,
                                    argv=["--strict-all"])
            self.assertEqual(code2, 1, out2)
        finally:
            npf.cached_preflight = original

    def test_33_json_output_is_machine_readable(self):
        projects = ["same"]
        local = {"same": {"a.py": "sha1"}}
        up = self._upstream_factory({"same": {"a.py": "sha1"}})
        code, out = self._run(projects, upstream=up, local=local, argv=["--json"])
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(payload["counts"]["identical"], 1)
        self.assertEqual(payload["total"], 1)
        self.assertIn("register", payload)


class GeneratedSchemaTests(unittest.TestCase):
    """Volatility excuses a changed blob — never a broken one."""

    SPEC = {"required": ["schema", "run_id", "counts"],
            "types": {"schema": "int", "run_id": "string", "counts": "object"}}

    def test_40_the_real_register_declares_a_schema_for_every_generated_file(self):
        reg, _ = vi.load_register()
        self.assertTrue(reg["generated_schema"], "no generated_schema in the register")
        for project, files in reg["generated"].items():
            for path in files:
                self.assertIn(path, reg["generated_schema"].get(project, {}),
                              f"{project}/{path} is generated but has no schema")

    def test_41_the_real_manifest_passes_its_schema(self):
        """The file that actually drifted upstream must still be structurally valid."""
        reg, _ = vi.load_register()
        spec = reg["generated_schema"]["easy-ai"]["data/manifest.json"]
        raw = (vi.ROOT / "projects/easy-ai/data/manifest.json").read_bytes()
        result = vi.validate_generated("easy-ai", "data/manifest.json", spec, raw)
        self.assertTrue(result["checked"])
        self.assertTrue(result["ok"], result["reason"])
        self.assertEqual(result["reason"], "schema ok")

    def test_42_a_missing_key_is_not_volatility(self):
        raw = json.dumps({"schema": 1, "counts": {}}).encode()
        res = vi.validate_generated("p", "m.json", self.SPEC, raw)
        self.assertFalse(res["ok"])
        self.assertIn("run_id", res["reason"])

    def test_43_a_wrong_type_is_not_volatility(self):
        raw = json.dumps({"schema": 1, "run_id": 123, "counts": {}}).encode()
        res = vi.validate_generated("p", "m.json", self.SPEC, raw)
        self.assertFalse(res["ok"])
        self.assertIn("run_id", res["reason"])

    def test_44_true_is_not_an_int(self):
        raw = json.dumps({"schema": True, "run_id": "r", "counts": {}}).encode()
        self.assertFalse(vi.validate_generated("p", "m.json", self.SPEC, raw)["ok"])

    def test_45_truncated_json_is_reported_as_invalid(self):
        res = vi.validate_generated("p", "m.json", self.SPEC, b'{"schema": 1, "run_')
        self.assertFalse(res["ok"])
        self.assertIn("not valid JSON", res["reason"])

    def test_46_a_non_object_top_level_is_refused(self):
        res = vi.validate_generated("p", "m.json", self.SPEC, b"[1, 2, 3]")
        self.assertFalse(res["ok"])
        self.assertIn("not an object", res["reason"])

    def test_47_an_undeclared_schema_is_reported_not_silently_passed(self):
        res = vi.validate_generated("p", "m.json", None, b"{}")
        self.assertFalse(res["checked"])
        self.assertTrue(res["ok"])
        self.assertIn("no schema declared", res["reason"])

    def test_48_enforce_turns_a_broken_generated_file_into_drift(self):
        results = [vi.classify("gen", ["m.json"], [], [], REG_GEN)]
        self.assertEqual(results[0]["status"], vi.GENERATED)
        reg = dict(REG_GEN, generated_schema={"gen": {"m.json": self.SPEC}})
        out = vi.enforce_schemas(results, reg,
                                 reader=lambda path: b'{"schema": 1}')
        self.assertEqual(out[0]["status"], vi.DRIFTED)
        self.assertIn("run_id", out[0]["schema_failures"]["m.json"])

    def test_49_enforce_keeps_a_valid_generated_file_volatile(self):
        results = [vi.classify("gen", ["m.json"], [], [], REG_GEN)]
        reg = dict(REG_GEN, generated_schema={"gen": {"m.json": self.SPEC}})
        good = json.dumps({"schema": 1, "run_id": "r", "counts": {}}).encode()
        out = vi.enforce_schemas(results, reg, reader=lambda path: good)
        self.assertEqual(out[0]["status"], vi.GENERATED)
        self.assertTrue(out[0]["generated_checks"]["m.json"]["ok"])

    def test_50_enforce_flags_an_undeclared_schema_without_failing(self):
        results = [vi.classify("gen", ["m.json"], [], [], REG_GEN)]
        out = vi.enforce_schemas(results, dict(REG_GEN, generated_schema={}),
                                 reader=lambda path: b"{}")
        self.assertEqual(out[0]["status"], vi.GENERATED)
        self.assertEqual(out[0]["schema_unchecked"], ["m.json"])

    def test_51_a_real_drift_is_left_alone_by_the_schema_pass(self):
        results = [vi.classify("other", ["x.py"], [], [], REG)]
        out = vi.enforce_schemas(results, dict(REG, generated_schema={}),
                                 reader=lambda path: b"{}")
        self.assertEqual(out[0]["status"], vi.DRIFTED)
        self.assertNotIn("generated_checks", out[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
