#!/usr/bin/env python3
"""Process-level persistence test for the real Live Agent launcher and HUD.

Boots both services against isolated temporary runtimes, executes real kernel
work over HTTP, terminates both OS processes, starts fresh processes on the same
runtimes, then retrieves and verifies the saved records and artifacts.

Run: python3 scripts/test_runtime_restart.py -v
Stdlib only. Never touches the repository's default runtime directories.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TEST_WRITE_TOKEN = "runtime-restart-test-" + "x" * 40


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _http(base: str, path: str, payload: dict | None = None,
          headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    req = urllib.request.Request(
        base + path, data=data,
        headers=request_headers,
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _json_http(base: str, path: str, payload: dict | None = None,
               headers: dict[str, str] | None = None) -> tuple[int, dict]:
    status, raw = _http(base, path, payload, headers=headers)
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise AssertionError(f"{path} returned non-JSON HTTP {status}: {raw[:120]!r}") from exc
    if not isinstance(value, dict):
        raise AssertionError(f"{path} returned a non-object JSON value")
    return status, value


class RuntimeRestartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ai2027-process-restart-")
        self.root = Path(self.temp.name)
        self.live_runtime = self.root / "live-agent"
        self.hud_runtime = self.root / "command-hud"
        self.logs = self.root / "logs"
        self.logs.mkdir()
        self.live_port = _free_port()
        self.hud_port = _free_port()
        while self.hud_port == self.live_port:
            self.hud_port = _free_port()
        self.processes: list[tuple[str, subprocess.Popen, object]] = []
        self.current: dict[str, subprocess.Popen] = {}
        self.start_number = 0
        self.live_base = f"http://127.0.0.1:{self.live_port}"
        self.hud_base = f"http://127.0.0.1:{self.hud_port}"

    def tearDown(self):
        self._stop_services()
        self.temp.cleanup()

    def _diagnostic(self, label: str) -> str:
        paths = sorted(self.logs.glob(f"{label}-*.log"))
        chunks = []
        for path in paths:
            try:
                chunks.append(f"--- {path.name} ---\n{path.read_text(encoding='utf-8', errors='replace')}")
            except OSError:
                pass
        return "\n".join(chunks)[-12000:]

    def _spawn(self, label: str, command: list[str], env: dict[str, str], base: str) -> subprocess.Popen:
        log_path = self.logs / f"{label}-{self.start_number}.log"
        log = log_path.open("w", encoding="utf-8", buffering=1)
        try:
            proc = subprocess.Popen(
                command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        except Exception:
            log.close()
            raise
        self.processes.append((label, proc, log))
        self.current[label] = proc
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                self.fail(f"{label} exited during startup ({proc.returncode}):\n{self._diagnostic(label)}")
            try:
                status, health = _json_http(base, "/api/health")
                if status == 200 and health.get("ok") is True:
                    return proc
            except (urllib.error.URLError, OSError, TimeoutError, AssertionError):
                pass
            time.sleep(0.1)
        self.fail(f"{label} did not become healthy within 25s:\n{self._diagnostic(label)}")
        raise AssertionError("unreachable")

    def _start_services(self) -> None:
        self.start_number += 1
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        live_env = {
            **env,
            "PORT": str(self.live_port),
            "HOST": "127.0.0.1",
            "AI2027_LIVE_RUNTIME_DIR": str(self.live_runtime),
            "AI2027_MAX_DISK_MB": "32",
            "AI2027_MAX_RUNS": "100",
            "AI2027_READ_ONLY": "0",
            "AI2027_WRITE_TOKEN": _TEST_WRITE_TOKEN,
        }
        self._spawn(
            "live", [sys.executable, "deployment/live_server.py"],
            live_env, self.live_base)
        hud_command = [
            sys.executable, "command-hud/server.py",
            "--port", str(self.hud_port), "--host", "127.0.0.1",
            "--fast-audit", "--runtime-dir", str(self.hud_runtime),
        ]
        self._spawn("hud", hud_command, env, self.hud_base)

    def _stop_services(self) -> None:
        for label, proc, log in reversed(self.processes):
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            try:
                log.flush()
                log.close()
            except (OSError, ValueError):
                pass
        self.processes.clear()

    def test_records_and_artifacts_survive_real_process_restart(self):
        self._start_services()
        nonce = os.urandom(12).hex()
        live_path = f"restart/live-{nonce}.txt"
        live_content = f"live-agent-restart-proof:{nonce}"
        hud_path = f"restart/hud-{nonce}.txt"
        hud_content = f"command-hud-restart-proof:{nonce}"
        live_expected_hash = hashlib.sha256(live_content.encode("utf-8")).hexdigest()
        hud_expected_hash = hashlib.sha256(hud_content.encode("utf-8")).hexdigest()

        status, live_before = _json_http(self.live_base, "/api/run", {
            "goal_type": "create_file",
            "params": {"path": live_path, "content": live_content},
        }, headers={"Authorization": f"Bearer {_TEST_WRITE_TOKEN}"})
        self.assertEqual(status, 200)
        self.assertEqual(live_before.get("status"), "VERIFIED")
        live_id = live_before["run_id"]
        live_memory_id = (live_before.get("memory") or {}).get("memory_id")
        self.assertTrue(live_memory_id, "verified Live Agent run was not admitted to memory")

        status, hud_before = _json_http(self.hud_base, "/api/run", {
            "goal_type": "create_file",
            "params": {"path": hud_path, "content": hud_content},
        })
        self.assertEqual(status, 200)
        self.assertEqual(hud_before.get("status"), "VERIFIED")
        self.assertGreaterEqual(hud_before.get("plan_steps", 0), 1)
        self.assertTrue(hud_before.get("history_persisted"))
        self.assertEqual(hud_before.get("sha256"), {
            "file": hud_path,
            "bytes": len(hud_content.encode("utf-8")),
            "sha256": hud_expected_hash,
        })
        hud_id = hud_before["run_id"]
        hud_memory_id = (hud_before.get("memory") or {}).get("memory_id")
        self.assertTrue(hud_memory_id, "verified HUD run was not admitted to memory")

        old_live = self.current["live"]
        old_hud = self.current["hud"]
        self._stop_services()
        self.assertIsNotNone(old_live.poll(), "Live Agent process did not terminate")
        self.assertIsNotNone(old_hud.poll(), "HUD process did not terminate")
        self.assertTrue((self.live_runtime / "runs" / f"{live_id}.json").is_file())
        self.assertTrue((self.hud_runtime / "runs" / f"{hud_id}.json").is_file())
        self.assertTrue((self.hud_runtime / "state" / "hud-history.jsonl").is_file())

        # New OS processes, same disposable storage directories.
        self._start_services()
        self.assertIsNone(self.current["live"].poll(), "restarted Live Agent process exited")
        self.assertIsNone(self.current["hud"].poll(), "restarted HUD process exited")

        status, live_after = _json_http(self.live_base, f"/api/run/{live_id}")
        self.assertEqual(status, 200)
        self.assertEqual(live_after.get("status"), "VERIFIED")
        self.assertEqual(live_after.get("goal", {}).get("params"), {
            "path": live_path, "content": live_content,
        })
        live_checks = (live_after.get("evidence") or [{}])[0].get("checks", {})
        self.assertTrue(all(live_checks.get(key) is True for key in (
            "claimed_ok", "file_exists", "correct_path", "correct_content", "hash_matches")))
        status, live_runs = _json_http(self.live_base, "/api/runs")
        self.assertEqual(status, 200)
        self.assertTrue(any(run.get("run_id") == live_id for run in live_runs.get("runs", [])))
        status, live_bytes = _http(self.live_base, "/api/file?" + urllib.parse.urlencode({
            "run": live_id, "path": live_path,
        }))
        self.assertEqual(status, 200)
        self.assertEqual(live_bytes, live_content.encode("utf-8"))
        self.assertEqual(hashlib.sha256(live_bytes).hexdigest(), live_expected_hash)
        live_artifact = self.live_runtime / "sandboxes" / live_id / live_path
        self.assertEqual(live_artifact.read_bytes(), live_content.encode("utf-8"))
        self.assertEqual(hashlib.sha256(live_artifact.read_bytes()).hexdigest(), live_expected_hash)
        status, live_memory = _json_http(self.live_base, "/api/memory")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(live_memory.get("memories", 0), 1)
        status, live_search = _json_http(self.live_base,
                                        "/api/memory/search?" + urllib.parse.urlencode({
                                            "q": "restart proof", "k": 3,
                                        }))
        self.assertEqual(status, 200)
        self.assertTrue(any(row.get("memory_id") == live_memory_id
                            for row in live_search.get("results", [])))

        status, hud_after = _json_http(self.hud_base, f"/api/record/{hud_id}")
        self.assertEqual(status, 200)
        self.assertEqual(hud_after.get("status"), "VERIFIED")
        self.assertEqual(hud_after.get("goal", {}).get("params"), {
            "path": hud_path, "content": hud_content,
        })
        hud_checks = (hud_after.get("evidence") or [{}])[0].get("checks", {})
        self.assertTrue(all(hud_checks.get(key) is True for key in (
            "claimed_ok", "file_exists", "correct_path", "correct_content", "hash_matches")))
        status, hud_state = _json_http(self.hud_base, "/api/state")
        self.assertEqual(status, 200)
        hud_history = next((event for event in hud_state.get("history", [])
                            if event.get("run_id") == hud_id), None)
        self.assertIsNotNone(hud_history)
        self.assertEqual(hud_history.get("status"), "VERIFIED")
        self.assertTrue(hud_history.get("history_persisted"))
        self.assertEqual(hud_history.get("sha256"), {
            "file": hud_path,
            "bytes": len(hud_content.encode("utf-8")),
            "sha256": hud_expected_hash,
        })

        hud_artifact = self.hud_runtime / "sandboxes" / hud_id / hud_path
        self.assertEqual(hud_artifact.read_bytes(), hud_content.encode("utf-8"))
        self.assertEqual(hashlib.sha256(hud_artifact.read_bytes()).hexdigest(), hud_expected_hash)
        status, hud_memory = _json_http(self.hud_base, "/api/memory")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(hud_memory.get("stats", {}).get("memories", 0), 1)
        self.assertTrue(any(row.get("memory_id") == hud_memory_id
                            for row in hud_memory.get("recent", [])))
        integrity = hud_memory.get("verify", {})
        self.assertTrue(integrity.get("intact"), integrity)
        self.assertEqual(integrity.get("hash_mismatches"), 0)
        self.assertEqual(integrity.get("artifacts_missing"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
