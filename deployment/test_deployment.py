#!/usr/bin/env python3
"""Tests for the cloud deployment configs — because a config that does not
parse, or that starts the wrong command, fails at exactly the wrong moment.

Every test here is a regression for a specific mistake that was made in this
project, most of them in a chat message that *looked* correct:

  * `render.yaml` whose list indentation produced two half-services,
  * a start command naming a package that does not exist (`live_agent.server`),
  * a `Dockerfile` COPY of a file that is not in the repo
    (`projects/agi-system/__init__.py`),
  * a deployment with no disk cap that fills a free tier in ~1.4 minutes.

Run: python3 deployment/test_deployment.py -v
Stdlib only (the YAML test skips cleanly if PyYAML is absent — CI installs none).
"""
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deployment"
_LIVE_SPEC = importlib.util.spec_from_file_location(
    "ai2027_deployment_live_server_test", DEPLOY / "live_server.py"
)
if _LIVE_SPEC is None or _LIVE_SPEC.loader is None:
    raise RuntimeError("could not load deployment/live_server.py for prune tests")
deployment_live_server = importlib.util.module_from_spec(_LIVE_SPEC)
_LIVE_SPEC.loader.exec_module(deployment_live_server)
TEST_WRITE_TOKEN = "test-write-token-" + "x" * 40
TEST_READ_TOKEN_ID = "test-reader"
TEST_READ_TOKEN = "test-read-token-" + "r" * 40
TEST_READ_TOKEN_HASHES = json.dumps({
    TEST_READ_TOKEN_ID: hashlib.sha256(TEST_READ_TOKEN.encode("utf-8")).hexdigest()
})


def _read_headers(token: str | None = TEST_READ_TOKEN) -> dict[str, str]:
    if token is None:
        return {}
    return {"Authorization": f"Bearer {TEST_READ_TOKEN_ID}.{token}"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_http(port: int, path: str = "/api/health", timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2).read()
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    return False


def _get_http(port: int, path: str, *, token: str | None = TEST_READ_TOKEN):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", headers=_read_headers(token), method="GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


class RenderYamlTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "render.yaml").read_text(encoding="utf-8")

    def test_01_parses_to_exactly_one_complete_service(self):
        """The original blueprint parsed to TWO services: one `- type: web` with
        nothing else, and a second dict with every other key but no `type`."""
        try:
            import yaml  # noqa: F401  (optional; CI installs no packages)
        except ImportError:
            self.skipTest("PyYAML not installed — structural checks below still run")
        import yaml
        d = yaml.safe_load(self.text)
        services = d["services"]
        self.assertEqual(len(services), 1, f"expected 1 service, got {len(services)}")
        svc = services[0]
        for key in ("type", "name", "runtime", "startCommand", "plan"):
            self.assertIn(key, svc, f"service is missing {key!r}")
        self.assertEqual(svc["type"], "web")

    def test_02_start_command_targets_a_file_that_exists(self):
        """`python3 -m live_agent.server` fails: there is no `live_agent` package."""
        m = re.search(r'startCommand:\s*"([^"]+)"', self.text)
        self.assertIsNotNone(m, "no startCommand found")
        cmd = m.group(1)
        self.assertNotIn("live_agent", cmd,
                         "`-m live_agent.server` cannot work: no such package")
        parts = cmd.split()
        script = next((p for p in parts if p.endswith(".py")), None)
        self.assertIsNotNone(script, f"startCommand has no .py script: {cmd!r}")
        self.assertTrue((ROOT / script).is_file(), f"{script} does not exist")

    def test_03_declares_the_disk_cap(self):
        """Without a cap, ~7,500 requests fill a 512 MB tier in ~1.4 minutes."""
        self.assertIn("AI2027_MAX_DISK_MB", self.text)
        self.assertIn("AI2027_MAX_RUNS", self.text)

    def test_04_has_a_health_check(self):
        """A PaaS needs a health path, or a half-broken deploy looks healthy."""
        self.assertIn("healthCheckPath: /api/health", self.text)

    def test_05_render_blueprint_does_not_promise_a_persistent_disk(self):
        """A process restart is not an instance-replacement durability guarantee."""
        active_yaml = "\n".join(
            line for line in self.text.splitlines()
            if not line.lstrip().startswith("#"))
        self.assertFalse(re.search(r"^\s+disk\s*:", active_yaml, re.MULTILINE),
                         "this free blueprint must not imply it provisions a disk")
        doc = (DEPLOY / "README.md").read_text(encoding="utf-8").lower()
        self.assertIn("no persistent disk", doc)
        self.assertIn("instance replacement", doc)

    def test_06_public_blueprint_defaults_to_read_only(self):
        active_yaml = "\n".join(
            line for line in self.text.splitlines()
            if not line.lstrip().startswith("#"))
        self.assertRegex(
            active_yaml,
            r'(?m)^[ \t]*-[ \t]+key: AI2027_READ_ONLY[ \t]*\n[ \t]+value: "1"[ \t]*$',
        )


class RequirementsTests(unittest.TestCase):
    def test_10_no_third_party_requirements(self):
        """Zero dependencies is a measured claim, not a slogan."""
        lines = [ln.strip() for ln in
                 (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()]
        reqs = [ln for ln in lines if ln and not ln.startswith("#")]
        self.assertEqual(reqs, [], f"requirements.txt declares dependencies: {reqs}")

    def test_11_kernel_import_gate_still_passes(self):
        r = subprocess.run([sys.executable, "scripts/ci_guard.py", "imports"],
                           cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PASS", r.stdout + r.stderr)


class DockerfileTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.lines = [ln.strip() for ln in self.text.splitlines()
                      if ln.strip() and not ln.strip().startswith("#")]

    def test_20_every_copy_source_exists(self):
        """`COPY projects/agi-system/__init__.py` fails: that file is not in the repo."""
        missing = []
        for ln in self.lines:
            if not ln.upper().startswith("COPY "):
                continue
            parts = ln.split()[1:]
            for src in parts[:-1]:          # last token is the destination
                if "$" in src or "*" in src:
                    continue
                if not (ROOT / src.rstrip("/")).exists():
                    missing.append(src)
        self.assertEqual(missing, [], f"Dockerfile COPY sources that do not exist: {missing}")

    def test_21_cmd_uses_exec_form_and_a_real_script(self):
        cmds = [ln for ln in self.lines if ln.upper().startswith("CMD ")]
        self.assertEqual(len(cmds), 1, "expected exactly one CMD")
        self.assertTrue(cmds[0].startswith("CMD ["),
                        "use exec form so SIGTERM reaches Python")
        script = json.loads(cmds[0][4:])[-1]
        self.assertTrue((ROOT / script).is_file(), f"{script} does not exist")

    def test_22_never_runs_as_root(self):
        self.assertTrue(any(ln.upper().startswith("USER ") for ln in self.lines),
                        "container has no USER directive")

    def test_23_installs_nothing(self):
        """`pip install` anywhere in the image would break the zero-dep claim."""
        for ln in self.lines:
            self.assertNotIn("pip install", ln.lower(), f"image installs packages: {ln}")

    def test_24_container_defaults_to_read_only(self):
        self.assertIn("AI2027_READ_ONLY=1", self.text)


class WriteGuardUnitTests(unittest.TestCase):
    def test_sliding_window_expires_old_events_and_isolates_clients(self):
        limiter = deployment_live_server.SlidingWindowRateLimiter(2, 10)
        self.assertEqual(limiter.allow("client-a", now=100), (True, 0))
        self.assertEqual(limiter.allow("client-a", now=101), (True, 0))
        self.assertEqual(limiter.allow("client-a", now=102), (False, 8))
        self.assertEqual(limiter.allow("client-b", now=102), (True, 0))
        self.assertEqual(limiter.allow("client-a", now=111), (True, 0))

    def test_client_table_fails_closed_until_an_entry_expires(self):
        limiter = deployment_live_server.SlidingWindowRateLimiter(1, 10, max_clients=1)
        self.assertEqual(limiter.allow("client-a", now=5), (True, 0))
        self.assertEqual(limiter.allow("client-b", now=6), (False, 10))
        self.assertEqual(limiter.allow("client-b", now=15), (True, 0))

    def test_bearer_scheme_is_case_insensitive_and_token_is_exact(self):
        self.assertTrue(deployment_live_server._bearer_token_matches(
            "bearer " + TEST_WRITE_TOKEN, TEST_WRITE_TOKEN))
        self.assertFalse(deployment_live_server._bearer_token_matches(
            "Bearer " + TEST_WRITE_TOKEN + " ", TEST_WRITE_TOKEN))
        self.assertFalse(deployment_live_server._bearer_token_matches(
            "Basic " + TEST_WRITE_TOKEN, TEST_WRITE_TOKEN))


class PruneSafetyTests(unittest.TestCase):
    """Pin deletion scope, root containment, and partial-failure reporting."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ai2027-prune-safety-")
        self.runtime = Path(self.tmp.name) / "runtime"
        self.runs = self.runtime / "runs"
        self.sandboxes = self.runtime / "sandboxes"
        (self.runs / "ledgers").mkdir(parents=True)
        self.sandboxes.mkdir()
        self.server = SimpleNamespace(
            RUNS_DIR=self.runs,
            SANDBOXES_DIR=self.sandboxes,
            _run_index={},
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _add_run(self, run_id: str) -> tuple[Path, Path, Path]:
        sandbox = self.sandboxes / run_id
        sandbox.mkdir()
        (sandbox / "artifact.txt").write_text("evidence", encoding="utf-8")
        record_data = {
            "run_id": run_id,
            "sandbox": run_id,
            "goal": {"goal_type": "prune-test"},
            "status": "VERIFIED",
            "started_at": 1700000000.0,
            "finished_at": 1700000001.0,
        }
        record = self.runs / f"{run_id}.json"
        record.write_text(json.dumps(record_data), encoding="utf-8")
        ledger_record = {key: value for key, value in record_data.items() if key != "sandbox"}
        ledger_event = {
            "type": "DECISION_RECORDED",
            "run_id": run_id,
            "payload": {"record": ledger_record, "status": record_data["status"]},
        }
        ledger = self.runs / "ledgers" / f"{run_id}.jsonl"
        ledger.write_text(json.dumps(ledger_event) + "\n", encoding="utf-8")
        self.server._run_index[run_id] = {"status": "VERIFIED"}
        return sandbox, record, ledger

    def test_only_generated_run_ids_are_pruned(self):
        run_id = "run_1700000000_abcdef12"
        sandbox, record, ledger = self._add_run(run_id)
        unrelated = "run_notes"
        unrelated_sandbox = self.sandboxes / unrelated
        unrelated_sandbox.mkdir()
        (unrelated_sandbox / "keep.txt").write_text("keep", encoding="utf-8")
        unrelated_record = self.runs / f"{unrelated}.json"
        unrelated_record.write_text("keep", encoding="utf-8")
        unrelated_ledger = self.runs / "ledgers" / f"{unrelated}.jsonl"
        unrelated_ledger.write_text("keep", encoding="utf-8")

        result = deployment_live_server._prune(self.server, 0)

        self.assertEqual(result["removed"], 1)
        self.assertFalse(sandbox.exists())
        self.assertFalse(record.exists())
        self.assertFalse(ledger.exists())
        self.assertTrue(unrelated_sandbox.exists())
        self.assertTrue(unrelated_record.exists())
        self.assertTrue(unrelated_ledger.exists())
        self.assertNotIn(run_id, self.server._run_index)
        self.assertGreater(result["over_cap_bytes"], 0)

    def test_disk_usage_scan_failure_aborts_without_deleting(self):
        run_id = "run_1700000011_abcdef12"
        sandbox, record, ledger = self._add_run(run_id)

        with patch.object(deployment_live_server, "_dir_bytes", side_effect=PermissionError):
            result = deployment_live_server._prune(self.server, 0)

        self.assertTrue(sandbox.exists())
        self.assertTrue(record.exists())
        self.assertTrue(ledger.exists())
        self.assertEqual(result["removed"], 0)
        self.assertEqual(result["disk_bytes"], None)
        self.assertIn("disk-usage-scan-failed", result["failures"])

    def test_mismatched_record_blocks_its_whole_run_group(self):
        run_id = "run_1700000001_abcdef12"
        sandbox, record, ledger = self._add_run(run_id)
        record.write_text(json.dumps({"run_id": "another-run", "sandbox": run_id}),
                          encoding="utf-8")

        result = deployment_live_server._prune(self.server, 0)

        self.assertEqual(result["removed"], 0)
        self.assertTrue(sandbox.exists())
        self.assertTrue(record.exists())
        self.assertTrue(ledger.exists())
        self.assertIn(f"{run_id}:record:id-mismatch", result["failures"])

    def test_mismatched_ledger_blocks_its_whole_run_group(self):
        run_id = "run_1700000007_abcdef12"
        sandbox, record, ledger = self._add_run(run_id)
        ledger_record = {key: value for key, value in json.loads(record.read_text()).items()
                         if key != "sandbox"}
        ledger_record["run_id"] = "run_abcdef123456"
        ledger_record["status"] = "FAILED"
        event = {
            "type": "DECISION_RECORDED",
            "run_id": ledger_record["run_id"],
            "payload": {"record": ledger_record, "status": "FAILED"},
        }
        ledger.write_text(json.dumps(event) + "\n", encoding="utf-8")

        result = deployment_live_server._prune(self.server, 0)

        self.assertEqual(result["removed"], 0)
        self.assertTrue(sandbox.exists())
        self.assertTrue(record.exists())
        self.assertTrue(ledger.exists())
        self.assertIn(f"{run_id}:ledger:invalid-or-mismatched", result["failures"])

        # Malformed, unhashable event IDs must fail closed rather than crash pruning.
        ledger.write_text(json.dumps({"type": "DECISION_RECORDED", "run_id": [run_id]}) + "\n",
                          encoding="utf-8")
        malformed = deployment_live_server._prune(self.server, 0)
        self.assertEqual(malformed["removed"], 0)
        self.assertTrue(sandbox.exists())
        self.assertTrue(record.exists())
        self.assertTrue(ledger.exists())
        self.assertIn(f"{run_id}:ledger:invalid-or-mismatched", malformed["failures"])

    def test_legacy_ledger_requires_a_matching_final_record(self):
        run_id = "run_1700000010_abcdef12"
        sandbox, record, ledger = self._add_run(run_id)
        public_record = json.loads(record.read_text(encoding="utf-8"))
        kernel_run_id = "run_abcdef123456"
        ledger_record = {
            key: value for key, value in public_record.items() if key != "sandbox"
        }
        ledger_record["run_id"] = kernel_run_id
        event = {
            "type": "DECISION_RECORDED",
            "run_id": kernel_run_id,
            "payload": {"record": ledger_record, "status": public_record["status"]},
        }
        ledger.write_text(json.dumps(event) + "\n", encoding="utf-8")

        result = deployment_live_server._prune(self.server, 0)

        self.assertEqual(result["removed"], 1)
        self.assertFalse(sandbox.exists())
        self.assertFalse(record.exists())
        self.assertFalse(ledger.exists())

    def test_symlinked_storage_roots_are_never_pruned(self):
        run_id = "run_1700000002_abcdef12"
        for linked_dir in ("runs", "sandboxes", "ledgers"):
            with self.subTest(linked_dir=linked_dir):
                with tempfile.TemporaryDirectory(prefix="ai2027-prune-link-") as tmp:
                    runtime = Path(tmp) / "runtime"
                    outside = Path(tmp) / "outside"
                    runtime.mkdir()
                    outside.mkdir()
                    runs = runtime / "runs"
                    sandboxes = runtime / "sandboxes"
                    if linked_dir == "runs":
                        (outside / "ledgers").mkdir()
                        external = outside / f"{run_id}.json"
                        external.write_text(
                            json.dumps({"run_id": run_id, "sandbox": run_id}), encoding="utf-8"
                        )
                        runs.symlink_to(outside, target_is_directory=True)
                        sandboxes.mkdir()
                    elif linked_dir == "sandboxes":
                        runs.mkdir()
                        (runs / "ledgers").mkdir()
                        external = outside / run_id
                        external.mkdir()
                        (external / "artifact.txt").write_text("outside", encoding="utf-8")
                        sandboxes.symlink_to(outside, target_is_directory=True)
                    else:
                        runs.mkdir()
                        sandboxes.mkdir()
                        external = outside / f"{run_id}.jsonl"
                        external.write_text("outside", encoding="utf-8")
                        (runs / "ledgers").symlink_to(outside, target_is_directory=True)
                    server = SimpleNamespace(RUNS_DIR=runs, SANDBOXES_DIR=sandboxes, _run_index={})
                    try:
                        result = deployment_live_server._prune(server, 0)
                    except (OSError, NotImplementedError) as exc:
                        self.skipTest(f"directory symlinks unavailable: {exc}")

                    self.assertEqual(result["removed"], 0)
                    self.assertIn("unsafe-storage-layout", result["failures"])
                    self.assertTrue(external.exists())

    def test_symlinked_run_candidate_does_not_follow_target(self):
        run_id = "run_1700000005_abcdef12"
        sandbox, record, ledger = self._add_run(run_id)
        outside = Path(self.tmp.name) / "outside-sandbox"
        outside.mkdir()
        external_file = outside / "keep.txt"
        external_file.write_text("outside", encoding="utf-8")
        shutil.rmtree(sandbox)
        try:
            sandbox.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")

        result = deployment_live_server._prune(self.server, 0)

        self.assertEqual(result["removed"], 0)
        self.assertTrue(sandbox.is_symlink())
        self.assertTrue(external_file.exists())
        self.assertTrue(record.exists())
        self.assertTrue(ledger.exists())
        self.assertIn(f"{run_id}:sandbox:unexpected-file-type", result["failures"])

    def test_nested_symlink_inside_sandbox_does_not_delete_outside_target(self):
        if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
            self.skipTest("safe recursive deletion is unavailable on this platform")
        run_id = "run_1700000008_abcdef12"
        sandbox, record, ledger = self._add_run(run_id)
        outside = Path(self.tmp.name) / "outside-nested"
        outside.mkdir()
        external_file = outside / "keep.txt"
        external_file.write_text("outside", encoding="utf-8")
        try:
            (sandbox / "external").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")

        result = deployment_live_server._prune(self.server, 0)

        self.assertEqual(result["removed"], 1)
        self.assertFalse(sandbox.exists())
        self.assertFalse(record.exists())
        self.assertFalse(ledger.exists())
        self.assertTrue(external_file.exists())

    def test_unsafe_recursive_delete_platform_does_not_prune(self):
        run_id = "run_1700000006_abcdef12"
        sandbox, record, ledger = self._add_run(run_id)

        with patch.object(shutil.rmtree, "avoids_symlink_attacks", False, create=True):
            result = deployment_live_server._prune(self.server, 0)

        self.assertTrue(sandbox.exists())
        self.assertTrue(record.exists())
        self.assertTrue(ledger.exists())
        self.assertEqual(result["removed"], 0)
        self.assertEqual(result["partial_runs"], [])
        self.assertIn(
            f"{run_id}:sandbox:safe-recursive-delete-unavailable", result["failures"]
        )

    def test_record_delete_failure_leaves_the_group_untouched(self):
        run_id = "run_1700000003_abcdef12"
        sandbox, record, ledger = self._add_run(run_id)
        remove_artifact = deployment_live_server._remove_run_artifact

        def fail_on_record(path: Path, kind: str) -> int:
            if kind == "record":
                raise PermissionError("simulated removal failure")
            return remove_artifact(path, kind)

        with patch.object(deployment_live_server, "_remove_run_artifact", side_effect=fail_on_record):
            result = deployment_live_server._prune(self.server, 0)

        self.assertTrue(sandbox.exists())
        self.assertTrue(record.exists())
        self.assertTrue(ledger.exists())
        self.assertEqual(result["removed"], 0)
        self.assertEqual(result["partial_runs"], [])
        self.assertIn(f"{run_id}:record:delete-failed", result["failures"])
        self.assertIn(run_id, self.server._run_index)
        self.assertGreater(result["over_cap_bytes"], 0)

    def test_sandbox_delete_failure_removes_record_and_reports_partial_prune(self):
        run_id = "run_1700000009_abcdef12"
        sandbox, record, ledger = self._add_run(run_id)
        remove_artifact = deployment_live_server._remove_run_artifact

        def fail_on_sandbox(path: Path, kind: str) -> int:
            if kind == "sandbox":
                raise PermissionError("simulated sandbox deletion failure")
            return remove_artifact(path, kind)

        with patch.object(deployment_live_server, "_remove_run_artifact", side_effect=fail_on_sandbox):
            result = deployment_live_server._prune(self.server, 0)

        self.assertTrue(sandbox.exists())
        self.assertFalse(record.exists())
        self.assertTrue(ledger.exists())
        self.assertEqual(result["removed"], 0)
        self.assertEqual(result["partial_runs"], [run_id])
        self.assertIn(f"{run_id}:sandbox:delete-failed", result["failures"])
        self.assertNotIn(run_id, self.server._run_index)
        self.assertGreater(result["over_cap_bytes"], 0)

    def test_ledger_delete_failure_is_not_reported_as_a_complete_prune(self):
        run_id = "run_1700000004_abcdef12"
        sandbox, record, ledger = self._add_run(run_id)
        remove_artifact = deployment_live_server._remove_run_artifact

        def fail_on_ledger(path: Path, kind: str) -> int:
            if kind == "ledger":
                raise PermissionError("simulated removal failure")
            return remove_artifact(path, kind)

        with patch.object(deployment_live_server, "_remove_run_artifact", side_effect=fail_on_ledger):
            result = deployment_live_server._prune(self.server, 0)

        self.assertFalse(sandbox.exists())
        self.assertFalse(record.exists())
        self.assertTrue(ledger.exists())  # the audit artifact survives the failed cleanup
        self.assertEqual(result["removed"], 0)
        self.assertEqual(result["partial_runs"], [run_id])
        self.assertIn(f"{run_id}:ledger:delete-failed", result["failures"])
        self.assertNotIn(run_id, self.server._run_index)
        self.assertGreater(result["over_cap_bytes"], 0)


class CleanupReportTests(unittest.TestCase):
    def test_partial_and_failed_cleanup_are_path_free_and_never_complete(self):
        internal_run_id = "run_1700000000_abcdef01"
        partial = deployment_live_server._cleanup_report({
            "failures": [
                f"{internal_run_id}:record:delete-failed:/private/runtime/runs/record.json",
                "disk-cap-unmet:no-safe-candidates",
            ],
            "partial_runs": [internal_run_id],
            "removed": 1,
            "freed_bytes": 512,
        })
        self.assertEqual(partial["status"], "partial")
        self.assertTrue(partial["completed"])
        self.assertEqual(partial["partial_run_count"], 1)
        self.assertIn("RUN_RECORD_CLEANUP_FAILED", partial["warnings"])
        self.assertIn("DISK_CAP_UNMET", partial["warnings"])
        self.assertIn("CLEANUP_PARTIAL", partial["warnings"])
        self.assertNotIn(internal_run_id, json.dumps(partial))
        self.assertNotIn("/private/runtime", json.dumps(partial))

        failed = deployment_live_server._cleanup_report({
            "failures": ["disk-usage-scan-failed"], "partial_runs": [],
            "removed": 0, "freed_bytes": 0,
        })
        self.assertEqual(failed["status"], "failed")
        complete = deployment_live_server._cleanup_report({
            "failures": [], "partial_runs": [], "removed": 0, "freed_bytes": 0,
        })
        self.assertEqual(complete["status"], "complete")
        self.assertEqual(complete["warnings"], [])

    def test_partial_report_is_attached_to_caller_payload(self):
        run_id = "run_1700000001_abcdef02"
        persisted = []
        run_index = {run_id: {"status": "VERIFIED", "cleanup": {"status": "not_applicable"}}}
        fake_server = SimpleNamespace(
            _run_index=run_index,
            _persist_run_record=lambda payload: persisted.append(
                json.loads(json.dumps(payload))
            )
        )
        payload = {"run_id": run_id, "status": "VERIFIED"}
        report, persist_failed = deployment_live_server._attach_cleanup_result(
            fake_server, payload, {
                "failures": [f"{run_id}:ledger:delete-failed:/private/ledger"],
                "partial_runs": [run_id], "removed": 0, "freed_bytes": 0,
            }
        )
        self.assertFalse(persist_failed)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(payload["cleanup"], report)
        self.assertEqual(persisted[0]["cleanup"]["status"], "partial")
        self.assertEqual(run_index[run_id]["cleanup"], {"status": "partial"})
        self.assertNotIn("/private/ledger", json.dumps(payload))
        self.assertNotIn(run_id, json.dumps(payload["cleanup"]))

    def test_cleanup_report_persistence_failure_downgrades_complete_result(self):
        run_id = "run_1700000002_abcdef03"
        run_index = {run_id: {"status": "VERIFIED"}}
        fake_server = SimpleNamespace(
            _run_index=run_index,
            _persist_run_record=lambda _payload: (_ for _ in ()).throw(OSError("synthetic"))
        )
        payload = {"run_id": run_id, "status": "VERIFIED"}
        report, persist_failed = deployment_live_server._attach_cleanup_result(
            fake_server, payload, {"failures": [], "partial_runs": [],
                              "removed": 0, "freed_bytes": 0}
        )
        self.assertTrue(persist_failed)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(run_index[run_id]["cleanup"], {"status": "partial"})
        self.assertIn("CLEANUP_RESULT_PERSIST_FAILED", report["warnings"])


class LauncherTests(unittest.TestCase):
    """Boot the real launcher and talk to it over HTTP."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ai2027-deploy-")
        cls.runtime_dir = Path(cls.tmp.name) / "live-agent-runtime"
        cls.port = _free_port()
        cls.env = {**os.environ, "PORT": str(cls.port), "HOST": "127.0.0.1",
                   "AI2027_LIVE_RUNTIME_DIR": str(cls.runtime_dir),
                   "AI2027_MAX_DISK_MB": "1", "AI2027_MAX_RUNS": "40",
                   "AI2027_WRITE_TOKEN": TEST_WRITE_TOKEN, "AI2027_READ_ONLY": "0",
                   "AI2027_WRITE_RATE_LIMIT": "100",
                   "AI2027_READ_TOKEN_HASHES": TEST_READ_TOKEN_HASHES}
        cls.proc = subprocess.Popen(
            [sys.executable, "deployment/live_server.py"], cwd=ROOT, env=cls.env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            start_new_session=True)
        if not _wait_http(cls.port):
            cls.proc.kill()
            out = cls.proc.stdout.read() if cls.proc.stdout else ""
            cls.tmp.cleanup()
            raise AssertionError(f"launcher did not start:\n{out}")

    @classmethod
    def tearDownClass(cls):
        if cls.proc.poll() is None:
            cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait(timeout=5)
        if cls.proc.stdout:
            cls.proc.stdout.close()
        cls.tmp.cleanup()

    def _post(self, payload: dict, token: str | None = TEST_WRITE_TOKEN,
              extra_headers: dict[str, str] | None = None) -> tuple[int, dict]:
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        headers.update(extra_headers or {})
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/run",
                                     data=body, headers=headers)
        try:
            return 200, json.loads(urllib.request.urlopen(req, timeout=30).read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_30_health_endpoint_answers(self):
        health_status, _, health_raw = _get_http(self.port, "/api/health", token=None)
        self.assertEqual(health_status, 200)
        d = json.loads(health_raw)
        self.assertEqual(d, {"ok": True, "service": "ai-2027-live-agent"})
        page = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=5).read().decode()
        self.assertIn("private api", page.lower())
        self.assertIn("واجهة API خاصة", page)

    def test_30a_missing_or_wrong_bearer_token_cannot_run_a_goal(self):
        before_status, _, before_raw = _get_http(self.port, "/api/runs")
        self.assertEqual(before_status, 200)
        before = json.loads(before_raw)
        code, body = self._post({"goal_type": "create_file",
                                 "params": {"path": "unauthorized.txt", "content": "x"}},
                                token=None)
        self.assertEqual(code, 401)
        self.assertIn("bearer", body["error"])

        wrong_code, wrong_body = self._post(
            {"goal_type": "create_file", "params": {"path": "wrong.txt", "content": "x"}},
            token="wrong-" + "x" * 40)
        self.assertEqual(wrong_code, 401)
        self.assertIn("bearer", wrong_body["error"])
        self.assertNotIn("wrong-", json.dumps(wrong_body))

        after_status, _, after_raw = _get_http(self.port, "/api/runs")
        self.assertEqual(after_status, 200)
        after = json.loads(after_raw)
        self.assertEqual(before["runs"], after["runs"], "unauthorized requests executed goals")

    def test_30b_every_private_data_route_rejects_missing_and_wrong_reader_tokens(self):
        marker = "synthetic-query-secret-never-log"
        paths = (
            "/api/agent-card",
            "/api/metrics",
            "/api/memory",
            "/api/memory/search?q=" + marker,
            "/api/capabilities",
            "/api/runs",
            "/api/run/run_1700000000_abcdef01",
            "/api/file?run=run_1700000000_abcdef01&path=" + marker,
            "/api/unregistered-private-route?value=" + marker,
        )
        wrong_secret = "wrong-reader-secret-" + "w" * 40
        for path in paths:
            with self.subTest(path=path.split("?", 1)[0]):
                missing_status, missing_headers, missing_body = _get_http(
                    self.port, path, token=None)
                self.assertEqual(missing_status, 401)
                self.assertIn("Bearer", missing_headers.get("WWW-Authenticate", ""))
                self.assertNotIn(marker, missing_body.decode("utf-8", errors="replace"))

                wrong_status, wrong_headers, wrong_body = _get_http(
                    self.port, path, token=wrong_secret)
                self.assertEqual(wrong_status, 401)
                self.assertIn("Bearer", wrong_headers.get("WWW-Authenticate", ""))
                self.assertNotIn(wrong_secret, wrong_body.decode("utf-8", errors="replace"))

        valid_status, _, valid_body = _get_http(self.port, "/api/memory")
        self.assertEqual(valid_status, 200)
        self.assertIn("memories", json.loads(valid_body))

    def test_31_disk_cap_is_enforced_under_load(self):
        """The whole point: 60 runs against a 1 MB cap must not exceed it."""
        ok = 0
        for i in range(60):
            code, body = self._post({"goal_type": "create_file",
                                     "params": {"path": f"l{i}.txt", "content": "A" * 4000}})
            if code == 200 and body.get("status") in ("VERIFIED", "DENIED", "FAILED"):
                ok += 1
        self.assertGreater(ok, 0, "no run succeeded")
        total = 0
        for d in (self.runtime_dir / "runs", self.runtime_dir / "sandboxes"):
            for p in d.rglob("*"):
                if p.is_file():
                    total += p.stat().st_size
        self.assertLessEqual(total, 1024 * 1024,
                             f"disk cap breached: {total / 1024:.0f} KB > 1024 KB")
        run_records = {p.stem for p in (self.runtime_dir / "runs").glob("run_*.json")}
        sandbox_ids = {p.name for p in (self.runtime_dir / "sandboxes").glob("run_*")}
        ledger_ids = {
            p.name[:-6]
            for p in (self.runtime_dir / "runs" / "ledgers").glob("run_*.jsonl")
        }
        self.assertEqual(run_records, sandbox_ids, "pruning split records from their sandboxes")
        self.assertEqual(run_records, ledger_ids, "pruning left run ledgers orphaned")
        listed_status, _, listed_raw = _get_http(self.port, "/api/runs")
        self.assertEqual(listed_status, 200)
        listed = json.loads(listed_raw)
        reported_ids = {run["run_id"] for run in listed["runs"]}
        self.assertEqual(run_records, reported_ids, "the API listed pruned run records")

    def test_32_run_budget_is_enforced(self):
        """Past AI2027_MAX_RUNS the launcher refuses instead of growing forever."""
        code, body = None, {}
        for _ in range(5):
            code, body = self._post({"goal_type": "create_file",
                                     "params": {"path": "b.txt", "content": "x"}})
            if code == 400 and "budget" in str(body.get("error", "")).lower():
                break
        self.assertEqual(code, 400, "run budget never engaged")
        self.assertIn("budget", body["error"].lower())

    def test_30c_runtime_storage_uses_owner_only_modes(self):
        result_code, payload = self._post({"goal_type": "create_file", "params": {
            "path": "permissions/proof.txt", "content": "private-runtime-mode-check"}})
        self.assertEqual(result_code, 200)
        self.assertEqual(payload["status"], "VERIFIED")
        run_id = payload["run_id"]
        private_directories = (
            self.runtime_dir, self.runtime_dir / "runs", self.runtime_dir / "runs" / "ledgers",
            self.runtime_dir / "sandboxes", self.runtime_dir / "state",
        )
        for path in private_directories:
            with self.subTest(directory=path.relative_to(self.runtime_dir.parent).as_posix()):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        private_files = (
            self.runtime_dir / "runs" / f"{run_id}.json",
            self.runtime_dir / "runs" / "ledgers" / f"{run_id}.jsonl",
            self.runtime_dir / "sandboxes" / run_id / "permissions" / "proof.txt",
            self.runtime_dir / "state" / "memory.db",
        )
        for path in private_files:
            with self.subTest(file=path.relative_to(self.runtime_dir).as_posix()):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


class SecureLoggingLauncherTests(unittest.TestCase):
    def test_http_logs_exclude_query_authorization_body_paths_and_error_details(self):
        port = _free_port()
        query_marker = "synthetic-search-secret-never-log"
        body_marker = "synthetic-artifact-content-never-log"
        error_marker = "synthetic-invalid-goal-never-log"
        with tempfile.TemporaryDirectory(prefix="ai2027-secure-logs-") as tmp:
            runtime = Path(tmp) / "private-runtime-path-marker"
            log_path = Path(tmp) / "service.log"
            env = {
                **os.environ,
                "PORT": str(port), "HOST": "127.0.0.1",
                "AI2027_LIVE_RUNTIME_DIR": str(runtime),
                "AI2027_WRITE_TOKEN": TEST_WRITE_TOKEN,
                "AI2027_READ_TOKEN_HASHES": TEST_READ_TOKEN_HASHES,
                "AI2027_READ_ONLY": "0",
                "AI2027_READ_RATE_LIMIT": "100",
                "AI2027_WRITE_RATE_LIMIT": "100",
            }
            with log_path.open("w", encoding="utf-8") as log:
                proc = subprocess.Popen(
                    [sys.executable, "deployment/live_server.py"], cwd=ROOT, env=env,
                    stdout=log, stderr=subprocess.STDOUT, text=True,
                )
                try:
                    self.assertTrue(_wait_http(port), "secure-log launcher did not start")
                    status, _, _ = _get_http(
                        port, "/api/memory/search?q=" + query_marker)
                    self.assertEqual(status, 200)

                    payload = {"goal_type": "create_file", "params": {
                        "path": "log-audit.txt", "content": body_marker,
                    }}
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/run",
                        data=json.dumps(payload).encode(),
                        headers={"Content-Type": "application/json",
                                 "Authorization": f"Bearer {TEST_WRITE_TOKEN}"},
                    )
                    with urllib.request.urlopen(request, timeout=15) as response:
                        result = json.loads(response.read())
                        self.assertEqual(response.status, 200)
                    self.assertEqual(result["cleanup"]["status"], "complete")
                    run_id = result["run_id"]
                    record_status, _, record_raw = _get_http(
                        port, f"/api/run/{run_id}")
                    self.assertEqual(record_status, 200)
                    self.assertEqual(
                        json.loads(record_raw)["cleanup"]["status"], "complete")
                    runs_status, _, runs_raw = _get_http(port, "/api/runs")
                    self.assertEqual(runs_status, 200)
                    listed = json.loads(runs_raw)["runs"]
                    self.assertEqual(
                        next(row for row in listed if row["run_id"] == run_id)
                        ["cleanup"]["status"], "complete")

                    bad_payload = {"goal_type": error_marker, "params": {}}
                    bad_request = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/run",
                        data=json.dumps(bad_payload).encode(),
                        headers={"Content-Type": "application/json",
                                 "Authorization": f"Bearer {TEST_WRITE_TOKEN}"},
                    )
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(bad_request, timeout=5)
                    self.assertEqual(error.exception.code, 400)
                    error_body = error.exception.read().decode("utf-8", errors="replace")
                    self.assertNotIn(error_marker, error_body)
                finally:
                    if proc.poll() is None:
                        proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)

            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            for marker in (query_marker, body_marker, error_marker, TEST_WRITE_TOKEN,
                           TEST_READ_TOKEN, str(runtime)):
                self.assertNotIn(marker, log_text)
            access_records = [
                json.loads(line) for line in log_text.splitlines()
                if line.startswith("{\"request_id\"")
            ]
            self.assertGreaterEqual(len(access_records), 3)
            for record in access_records:
                self.assertEqual(set(record), {
                    "request_id", "method", "route", "status", "duration_ms"
                })
                self.assertNotIn("?", record["route"])


class RateLimitedLauncherTests(unittest.TestCase):
    def test_rate_limit_returns_retry_after_and_ignores_untrusted_forwarded_ip(self):
        port = _free_port()
        with tempfile.TemporaryDirectory(prefix="ai2027-rate-limit-") as tmp:
            env = {**os.environ, "PORT": str(port), "HOST": "127.0.0.1",
                   "AI2027_LIVE_RUNTIME_DIR": str(Path(tmp) / "runtime"),
                   "AI2027_WRITE_TOKEN": TEST_WRITE_TOKEN, "AI2027_READ_ONLY": "0",
                   "AI2027_WRITE_RATE_LIMIT": "1",
                   "AI2027_WRITE_RATE_WINDOW_SECONDS": "60"}
            proc = subprocess.Popen(
                [sys.executable, "deployment/live_server.py"], cwd=ROOT, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                start_new_session=True)
            try:
                self.assertTrue(_wait_http(port), "rate-limited launcher did not start")
                body = json.dumps({"goal_type": "create_file",
                                   "params": {"path": "one.txt", "content": "ok"}}).encode()
                headers = {"Content-Type": "application/json",
                           "Authorization": f"Bearer {TEST_WRITE_TOKEN}"}
                first = urllib.request.Request(f"http://127.0.0.1:{port}/api/run",
                                              data=body, headers=headers)
                with urllib.request.urlopen(first, timeout=10) as response:
                    self.assertEqual(response.status, 200)
                    response.read()

                headers["X-Forwarded-For"] = "203.0.113.99"
                second = urllib.request.Request(f"http://127.0.0.1:{port}/api/run",
                                               data=body, headers=headers)
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(second, timeout=10).read()
                self.assertEqual(ctx.exception.code, 429)
                self.assertGreaterEqual(int(ctx.exception.headers["Retry-After"]), 1)
                self.assertIn("rate limit", ctx.exception.read().decode().lower())
            finally:
                if proc.poll() is None:
                    proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                if proc.stdout:
                    proc.stdout.close()


class WriteTokenConfigurationTests(unittest.TestCase):
    def test_weak_write_token_fails_startup_without_echoing_secret(self):
        secret = "too-short"
        env = {**os.environ, "AI2027_WRITE_TOKEN": secret}
        result = subprocess.run(
            [sys.executable, "deployment/live_server.py"], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        output = result.stdout + result.stderr
        self.assertIn("32-512 visible ASCII", output)
        self.assertNotIn(secret, output)

    def test_shared_write_token_contract_includes_symbols_and_exact_boundaries(self):
        valid = ("+/=" + "A" * 29, "+/=" + "A" * 509)
        self.assertEqual(tuple(map(len, valid)), (32, 512))
        for token in valid:
            with self.subTest(valid_length=len(token)):
                self.assertTrue(deployment_live_server._valid_write_token(token))
        invalid = (
            "A" * 31,
            "A" * 513,
            "A" * 16 + " " + "A" * 16,
            "A" * 16 + "\t" + "A" * 16,
            "A" * 32 + "\n",
            "A" * 31 + "é",
        )
        for token in invalid:
            with self.subTest(invalid_length=len(token)):
                self.assertFalse(deployment_live_server._valid_write_token(token))


class FreshResetTests(unittest.TestCase):
    def test_40_fresh_option_clears_only_the_selected_runtime(self):
        """The wrapper reset is explicit, scoped, and never the normal boot path."""
        port = _free_port()
        with tempfile.TemporaryDirectory(prefix="ai2027-fresh-reset-") as tmp:
            root = Path(tmp)
            runtime = root / "isolated-runtime"
            stale_run = runtime / "runs" / "stale.json"
            stale_artifact = runtime / "sandboxes" / "old-run" / "artifact.txt"
            stale_db = runtime / "state" / "memory.db"
            for path in (stale_run, stale_artifact, stale_db):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"stale")
            outside = root / "outside-runtime.txt"
            outside.write_text("must survive", encoding="utf-8")

            env = {**os.environ, "PORT": str(port), "HOST": "127.0.0.1",
                   "AI2027_LIVE_RUNTIME_DIR": str(runtime),
                   "AI2027_READ_TOKEN_HASHES": TEST_READ_TOKEN_HASHES}
            log_path = root / "launcher.log"
            with log_path.open("w", encoding="utf-8") as log:
                proc = subprocess.Popen(
                    [sys.executable, "deployment/live_server.py", "--fresh"],
                    cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
                try:
                    self.assertTrue(
                        _wait_http(port),
                        "fresh launcher did not start:\n" +
                        log_path.read_text(encoding="utf-8", errors="replace"))
                    self.assertFalse(stale_run.exists())
                    self.assertFalse(stale_artifact.exists())
                    # Minimal public health must not initialize private stores;
                    # an authorized memory read creates the new empty database.
                    self.assertFalse(stale_db.exists())
                    self.assertTrue(outside.is_file())
                    runs_status, _, runs_raw = _get_http(port, "/api/runs")
                    self.assertEqual(runs_status, 200)
                    self.assertEqual(json.loads(runs_raw)["runs"], [])
                    memory_status, _, memory_raw = _get_http(port, "/api/memory")
                    self.assertEqual(memory_status, 200)
                    self.assertEqual(json.loads(memory_raw)["memories"], 0)
                    self.assertNotEqual(stale_db.read_bytes(), b"stale")
                finally:
                    if proc.poll() is None:
                        proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)


class ReadOnlyTests(unittest.TestCase):
    def test_40_read_only_serves_but_refuses_writes(self):
        port = _free_port()
        with tempfile.TemporaryDirectory(prefix="ai2027-read-only-") as runtime:
            env = {**os.environ, "PORT": str(port), "HOST": "127.0.0.1",
                   "AI2027_LIVE_RUNTIME_DIR": str(Path(runtime) / "live-agent-runtime"),
                   "AI2027_READ_ONLY": "1"}
            proc = subprocess.Popen([sys.executable, "deployment/live_server.py"], cwd=ROOT,
                                    env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, start_new_session=True)
            try:
                self.assertTrue(_wait_http(port), "read-only launcher did not start")
                body = json.dumps({"goal_type": "create_file",
                                   "params": {"path": "x.txt", "content": "y"}}).encode()
                req = urllib.request.Request(f"http://127.0.0.1:{port}/api/run", data=body,
                                             headers={"Content-Type": "application/json"})
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req, timeout=10).read()
                self.assertEqual(ctx.exception.code, 403)
                self.assertIn("READ-ONLY", ctx.exception.read().decode().upper())
            finally:
                if proc.poll() is None:
                    proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                if proc.stdout:
                    proc.stdout.close()

    def test_41_missing_token_keeps_writes_disabled_even_when_read_only_flag_is_zero(self):
        port = _free_port()
        with tempfile.TemporaryDirectory(prefix="ai2027-no-write-token-") as runtime:
            env = {k: v for k, v in os.environ.items() if k != "AI2027_WRITE_TOKEN"}
            env.update({"PORT": str(port), "HOST": "127.0.0.1",
                        "AI2027_LIVE_RUNTIME_DIR": str(Path(runtime) / "live-agent-runtime"),
                        "AI2027_READ_ONLY": "0"})
            proc = subprocess.Popen([sys.executable, "deployment/live_server.py"], cwd=ROOT,
                                    env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, start_new_session=True)
            try:
                self.assertTrue(_wait_http(port), "fail-closed launcher did not start")
                body = json.dumps({"goal_type": "create_file",
                                   "params": {"path": "x.txt", "content": "y"}}).encode()
                req = urllib.request.Request(f"http://127.0.0.1:{port}/api/run", data=body,
                                             headers={"Content-Type": "application/json"})
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req, timeout=10).read()
                self.assertEqual(ctx.exception.code, 503)
                self.assertIn("AI2027_WRITE_TOKEN", ctx.exception.read().decode())
            finally:
                if proc.poll() is None:
                    proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                if proc.stdout:
                    proc.stdout.close()


class DocumentationHonestyTests(unittest.TestCase):
    def test_50_deployment_doc_states_the_write_controls(self):
        doc = (DEPLOY / "README.md").read_text(encoding="utf-8").lower()
        for phrase in ("bearer", "rate limit", "ai2027_read_only=1",
                       "never commit", "embed them in public html",
                       "multi-user", "terminate tls", "ai2027_read_token_hashes"):
            self.assertIn(phrase, doc, f"deployment README omits {phrase!r}")

    def test_51_deployment_doc_quotes_the_measured_fill_rate(self):
        doc = (DEPLOY / "README.md").read_text(encoding="utf-8")
        self.assertIn("69.5 KB", doc, "the measured per-run disk growth is missing")
        self.assertIn("1.4 min", doc, "the measured time-to-fill is missing")

    def test_52_live_ui_never_embeds_or_persists_api_credentials(self):
        ui = (ROOT / "live-agent" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("trusted gateway", ui.lower())
        self.assertIn("HTTPS", ui)
        self.assertIn("لا تضمّن رموزًا مشتركة", ui)
        self.assertIn("autocomplete=\"off\"", ui)
        self.assertIn("Authorization", ui)
        self.assertIn("Bearer credentials require HTTPS", ui)
        self.assertIn("isLoopbackHostname(target.hostname)", ui)
        self.assertGreaterEqual(ui.count("assertCredentialTransport(url);"), 2)
        self.assertIn("redirect:'error'", ui)
        self.assertIn("localStorage", ui)
        self.assertIn("full success not confirmed", ui)
        self.assertNotIn("localStorage.setItem", ui)
        self.assertNotIn("innerHTML=", ui)


if __name__ == "__main__":
    unittest.main(verbosity=2)
