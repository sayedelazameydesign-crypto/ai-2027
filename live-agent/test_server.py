#!/usr/bin/env python3
"""Stdlib tests for the Live Agent Experience server (Phase 1)."""
import hashlib
import io
import json
import logging
import os
import shutil
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import server  # noqa: E402


class TestRunGoal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        server.RUNS_DIR = Path(self.tmp) / "runs"
        server.SANDBOXES_DIR = Path(self.tmp) / "sandboxes"
        # Isolate all durable files because this test explicitly starts fresh.
        # Never let the destructive test reset touch a developer's live runtime.
        server.MEMORY_DB = Path(self.tmp) / "memory.db"
        server._prepare_state(fresh=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_file_is_verified_with_evidence(self):
        rec = server.run_goal("create_file", {"path": "a/b.txt", "content": "hi"})
        self.assertEqual(rec["status"], "VERIFIED")
        self.assertTrue(rec["evidence"])
        for e in rec["evidence"]:
            self.assertTrue(e["checks"])
            self.assertTrue(all(e["checks"].values()))
        self.assertIn("a/b.txt", rec["sandbox_files"])
        # the file really exists in that run's sandbox
        self.assertTrue(
            (server.SANDBOXES_DIR / rec["run_id"] / "a" / "b.txt").is_file()
        )

    def test_traversal_is_denied_by_kernel_policy(self):
        rec = server.run_goal("create_file",
                              {"path": "../../escape.txt", "content": "x"})
        self.assertEqual(rec["status"], "DENIED")
        self.assertTrue(rec["denial_reasons"])
        # nothing escaped
        self.assertFalse(Path(self.tmp).parent.joinpath("escape.txt").exists())

    def test_unknown_goal_fails_loudly(self):
        with self.assertRaises(ValueError):
            server.run_goal("launch_missiles", {"target": "nowhere"})

    def test_param_limits(self):
        with self.assertRaises(ValueError):
            server.run_goal("create_file",
                            {"path": "x.txt", "content": "y" * 5000})
        with self.assertRaises(ValueError):
            server.run_goal("create_file", "not-a-dict")

    def test_record_persisted_and_readable(self):
        rec = server.run_goal("ensure_dir", {"path": "d1"})
        loaded = server.read_run(rec["run_id"])
        self.assertEqual(loaded["status"], "VERIFIED")
        self.assertIsNone(server.read_run("../outside"))
        self.assertIsNone(server.read_run(f"{rec['run_id']}/../../outside"))
        ledger = server.RUNS_DIR / "ledgers" / f"{rec['run_id']}.jsonl"
        events = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(events)
        self.assertTrue(all(event.get("run_id") == rec["run_id"] for event in events))

    def test_sandbox_file_serving_is_traversal_safe(self):
        rec = server.run_goal("create_file", {"path": "pub.txt", "content": "42"})
        got = server.sandbox_file(rec["run_id"], "pub.txt")
        self.assertEqual(got[0], b"42")
        self.assertIsNone(server.sandbox_file(rec["run_id"], "../pub.txt"))
        self.assertIsNone(server.sandbox_file(rec["run_id"], "/pub.txt"))
        self.assertIsNone(server.sandbox_file(rec["run_id"], "..\\pub.txt"))
        self.assertIsNone(server.sandbox_file(rec["run_id"], "ledger.jsonl"))
        self.assertIsNone(server.sandbox_file("bad id", "pub.txt"))
        self.assertIsNone(server.sandbox_file("run_0_00000000", "pub.txt"))

    def test_runtime_configuration_refuses_symlink_paths_without_touching_targets(self):
        root = Path(self.tmp) / "symlink-audit"
        outside = root / "outside"
        outside.mkdir(parents=True)
        marker = outside / "keep.txt"
        marker.write_text("outside-data", encoding="utf-8")
        linked_root = root / "linked-runtime"
        linked_state_runtime = root / "runtime-with-linked-state"
        linked_state_runtime.mkdir()
        try:
            linked_root.symlink_to(outside, target_is_directory=True)
            (linked_state_runtime / "state").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")

        with self.assertRaises(ValueError):
            server._configure_runtime(linked_root)
        with self.assertRaises(ValueError):
            server._configure_runtime(linked_state_runtime)
        self.assertEqual(marker.read_text(encoding="utf-8"), "outside-data")

    def test_runtime_is_restored_by_default_and_reset_only_when_explicit(self):
        rec = server.run_goal("create_file", {
            "path": "restore/proof.txt", "content": "durable-run"})
        run_id = rec["run_id"]
        run_file = server.RUNS_DIR / f"{run_id}.json"
        artifact = server.SANDBOXES_DIR / run_id / "restore" / "proof.txt"
        self.assertTrue(run_file.is_file())
        self.assertEqual(artifact.read_bytes(), b"durable-run")

        # Model a new process: its module-level index and memory handle are empty,
        # but the run record, artifact, and SQLite data must remain on disk.
        server._run_index.clear()
        server._memory = None
        warnings = server._prepare_state()
        self.assertEqual(warnings, [])
        self.assertEqual(server.read_run(run_id)["status"], "VERIFIED")
        self.assertEqual(server._run_index[run_id]["status"], "VERIFIED")
        self.assertEqual(artifact.read_bytes(), b"durable-run")
        self.assertGreaterEqual(server.memory().stats()["memories"], 1)

        # Wiping is available for disposable demos/tests, but never implicit.
        server._prepare_state(fresh=True)
        self.assertFalse(run_file.exists())
        self.assertFalse(artifact.exists())
        self.assertFalse(server._run_index)

    def test_runtime_directories_and_files_are_owner_only_under_umask_022(self):
        if os.name != "posix":
            self.skipTest("POSIX mode bits are not portable to this platform")
        old_sandbox = server.SANDBOXES_DIR / "legacy-open-runtime"
        old_sandbox.mkdir()
        old_artifact = old_sandbox / "artifact.txt"
        old_artifact.write_text("synthetic-old-artifact", encoding="utf-8")
        os.chmod(server.RUNS_DIR, 0o750)
        os.chmod(server.SANDBOXES_DIR, 0o750)
        os.chmod(old_sandbox, 0o750)
        os.chmod(old_artifact, 0o640)

        previous_umask = os.umask(0o022)
        try:
            server._restrict_runtime_permissions()
            self.assertEqual(stat.S_IMODE(server.RUNS_DIR.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(server.SANDBOXES_DIR.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(old_sandbox.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(old_artifact.stat().st_mode), 0o600)

            server._prepare_state(fresh=True)
            record = server.run_goal("create_file", {
                "path": "private/artifact.txt", "content": "synthetic-private-artifact"})
            run_id = record["run_id"]
            private_files = (
                server.RUNS_DIR / f"{run_id}.json",
                server.RUNS_DIR / "ledgers" / f"{run_id}.jsonl",
                server.SANDBOXES_DIR / run_id / "private" / "artifact.txt",
                server.MEMORY_DB,
            )
            for path in private_files:
                with self.subTest(path=path.relative_to(Path(self.tmp)).as_posix()):
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            for path in (server.RUNS_DIR, server.RUNS_DIR / "ledgers",
                         server.SANDBOXES_DIR, server.SANDBOXES_DIR / run_id,
                         server.SANDBOXES_DIR / run_id / "private"):
                with self.subTest(directory=path.relative_to(Path(self.tmp)).as_posix()):
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        finally:
            os.umask(previous_umask)
            server._memory = None


class TestAPIProtectionAndLogging(unittest.TestCase):
    READ_ID = "unit-reader"
    READ_SECRET = "unit-read-secret-" + "r" * 40
    WRITE_SECRET = "unit-write-secret-" + "w" * 40

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ai2027-api-security-")
        self.saved_paths = (server.RUNS_DIR, server.SANDBOXES_DIR, server.MEMORY_DB)
        self.saved_index = dict(server._run_index)
        self.saved_memory = server._memory
        self.saved_access = (
            server._READ_TOKEN_HASHES, server._WRITE_TOKEN, server._READ_ONLY,
            server._API_READ_RATE_LIMITER, server._API_WRITE_RATE_LIMITER,
        )
        self.logger = logging.getLogger("agi.kernel")
        self.saved_logger = (list(self.logger.handlers), self.logger.propagate,
                             self.logger.level, self.logger.disabled)
        self.env_patch = patch.dict(os.environ, {
            "AI2027_READ_TOKEN_HASHES": json.dumps({
                self.READ_ID: hashlib.sha256(self.READ_SECRET.encode()).hexdigest()
            }),
            "AI2027_WRITE_TOKEN": self.WRITE_SECRET,
            "AI2027_READ_ONLY": "0",
            "AI2027_READ_RATE_LIMIT": "100",
            "AI2027_WRITE_RATE_LIMIT": "100",
        })
        self.env_patch.start()
        server.configure_api_access_from_env()
        server._configure_runtime(self.tmp.name)
        server._prepare_state(fresh=True)
        self.httpd = server.SafeThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        self.env_patch.stop()
        server.RUNS_DIR, server.SANDBOXES_DIR, server.MEMORY_DB = self.saved_paths
        server._run_index.clear()
        server._run_index.update(self.saved_index)
        server._memory = self.saved_memory
        (server._READ_TOKEN_HASHES, server._WRITE_TOKEN, server._READ_ONLY,
         server._API_READ_RATE_LIMITER, server._API_WRITE_RATE_LIMITER) = self.saved_access
        self.logger.handlers[:] = self.saved_logger[0]
        self.logger.propagate = self.saved_logger[1]
        self.logger.setLevel(self.saved_logger[2])
        self.logger.disabled = self.saved_logger[3]
        self.tmp.cleanup()

    def request(self, method: str, path: str, *, credential: str | None = None,
                payload: dict | None = None):
        headers = {}
        if credential is not None:
            headers["Authorization"] = f"Bearer {credential}"
        if payload is not None:
            headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=body,
            headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def test_all_private_read_routes_require_a_reader_and_health_is_minimal(self):
        health_status, _, health_raw = self.request("GET", "/api/health")
        self.assertEqual(health_status, 200)
        self.assertEqual(json.loads(health_raw), {
            "ok": True, "service": "ai-2027-live-agent"
        })
        paths = (
            "/api/agent-card", "/api/metrics", "/api/memory",
            "/api/memory/search?q=synthetic-query-marker",
            "/api/runs", "/api/run/run_1700000000_abcdef01",
            "/api/file?run=run_1700000000_abcdef01&path=synthetic-file-marker",
            "/api/unregistered-private-route",
        )
        wrong = "unit-read-secret-" + "z" * 40
        valid = f"{self.READ_ID}.{self.READ_SECRET}"
        for path in paths:
            with self.subTest(path=path.split("?", 1)[0]):
                missing_status, _, missing_body = self.request("GET", path)
                self.assertEqual(missing_status, 401)
                self.assertNotIn("synthetic", missing_body.decode("utf-8", errors="replace"))
                wrong_status, _, wrong_body = self.request("GET", path, credential=f"{self.READ_ID}.{wrong}")
                self.assertEqual(wrong_status, 401)
                self.assertNotIn(wrong, wrong_body.decode("utf-8", errors="replace"))
        self.assertEqual(self.request("GET", "/api/runs", credential=valid)[0], 200)

    def test_file_download_filename_is_encoded_before_header_use(self):
        read_credential = f"{self.READ_ID}.{self.READ_SECRET}"
        malicious_name = "bad\r\nX-Injected: yes.bin"
        with patch.object(server, "sandbox_file", return_value=(b"proof", malicious_name)):
            status, headers, body = self.request(
                "GET",
                "/api/file?run=run_1700000000_abcdef01&path=artifact.bin",
                credential=read_credential,
            )
        self.assertEqual(status, 200)
        self.assertEqual(body, b"proof")
        disposition = headers.get("Content-Disposition", "")
        self.assertIn("filename*=UTF-8''bad%0D%0AX-Injected%3A%20yes.bin", disposition)
        self.assertIsNone(headers.get("X-Injected"))

    def test_read_rate_limit_includes_health_and_returns_retry_after(self):
        limiter = server.RequestRateLimiter(2, 60)
        with patch.object(server, "_API_READ_RATE_LIMITER", limiter):
            first = self.request("GET", "/api/health")
            second = self.request("GET", "/api/health")
            third = self.request("GET", "/api/health")
        self.assertEqual((first[0], second[0], third[0]), (200, 200, 429))
        self.assertEqual(third[1].get("Retry-After"), "60")

    def test_write_rate_limit_returns_retry_after(self):
        limiter = server.RequestRateLimiter(1, 60)
        payload = {"goal_type": "create_file", "params": {"path": "limited.txt"}}
        with patch.object(server, "_API_WRITE_RATE_LIMITER", limiter):
            first = self.request("POST", "/api/run", credential=self.WRITE_SECRET,
                                 payload=payload)
            second = self.request("POST", "/api/run", credential=self.WRITE_SECRET,
                                  payload=payload)
        self.assertEqual((first[0], second[0]), (200, 429))
        self.assertEqual(second[1].get("Retry-After"), "60")

    def test_unsupported_http_methods_never_expose_api_data(self):
        marker = "method-query-do-not-reflect"
        for method in ("HEAD", "OPTIONS", "PUT", "PATCH", "DELETE", "TRACE", "CONNECT"):
            with self.subTest(method=method):
                status, _, body = self.request(method, "/api/runs?" + marker)
                self.assertEqual(status, 501)
                self.assertNotIn(marker.encode(), body)
                if method == "HEAD":
                    self.assertEqual(body, b"")
                else:
                    self.assertEqual(json.loads(body)["error"], "request rejected")

    def test_read_only_switch_blocks_a_valid_operator_token(self):
        with patch.object(server, "_READ_ONLY", True):
            status, _, body = self.request(
                "POST", "/api/run", credential=self.WRITE_SECRET,
                payload={"goal_type": "create_file", "params": {"path": "blocked.txt"}},
            )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"], "writes are disabled")

    def test_write_auth_and_request_logs_do_not_capture_inputs_or_errors(self):
        query_marker = "synthetic-query-do-not-log"
        body_marker = "synthetic-body-do-not-log"
        auth_marker = "synthetic-wrong-auth-do-not-log"
        kernel_marker = "synthetic-kernel-error-do-not-log"
        read_credential = f"{self.READ_ID}.{self.READ_SECRET}"
        wrong_writer = auth_marker + "-" + "x" * 40
        body = {"goal_type": "create_file", "params": {
            "path": "safe.txt", "content": body_marker,
        }}
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(self.request("POST", "/api/run", payload=body)[0], 401)
            self.assertEqual(self.request("POST", "/api/run", credential=wrong_writer,
                                          payload=body)[0], 401)
            method_status, _, method_body = self.request(
                "DELETE", "/api/run?" + query_marker)
            self.assertEqual(method_status, 501)
            self.assertEqual(json.loads(method_body)["error"], "request rejected")
            self.assertNotIn(query_marker, method_body.decode("utf-8", errors="replace"))
            search_status, _, _ = self.request(
                "GET", "/api/memory/search?q=" + query_marker,
                credential=read_credential)
            self.assertEqual(search_status, 200)
            run_status, _, run_raw = self.request(
                "POST", "/api/run", credential=self.WRITE_SECRET, payload=body)
            self.assertEqual(run_status, 200)
            self.assertEqual(json.loads(run_raw)["status"], "VERIFIED")

            def raise_for_error(*_args, **_kwargs):
                raise RuntimeError(kernel_marker)

            with patch.object(server, "memory", side_effect=raise_for_error):
                error_status, _, error_raw = self.request(
                    "GET", "/api/memory", credential=read_credential)
                self.assertEqual(error_status, 500)
                self.assertNotIn(kernel_marker, error_raw.decode("utf-8", errors="replace"))

            class ExplodingKernel:
                def __init__(self, *_args, **_kwargs):
                    pass

                def run(self, *_args, **_kwargs):
                    try:
                        raise RuntimeError(kernel_marker)
                    except RuntimeError:
                        logging.getLogger("agi.kernel").exception("kernel execution failed")
                        raise

            with patch.object(server, "Kernel", ExplodingKernel):
                error_status, _, error_raw = self.request(
                    "POST", "/api/run", credential=self.WRITE_SECRET,
                    payload={"goal_type": "create_file", "params": {"path": "error.txt"}},
                )
                self.assertEqual(error_status, 500)
                self.assertNotIn(kernel_marker, error_raw.decode("utf-8", errors="replace"))

            # Handler access logs are emitted as each worker closes its response;
            # wait for the final worker while stderr is still redirected.
            deadline = time.monotonic() + 2
            while errors.getvalue().count("\n") < 7 and time.monotonic() < deadline:
                time.sleep(0.01)

        captured = output.getvalue() + errors.getvalue()
        for marker in (query_marker, body_marker, auth_marker, kernel_marker,
                       self.READ_SECRET, self.WRITE_SECRET):
            self.assertNotIn(marker, captured)
        records = [json.loads(line) for line in errors.getvalue().splitlines() if line.strip()]
        self.assertGreaterEqual(len(records), 7)
        for record in records:
            self.assertEqual(set(record), {
                "request_id", "method", "route", "status", "duration_ms"
            })
            self.assertNotIn("?", record["route"])
            self.assertNotIn(query_marker, record["route"])
            self.assertIsInstance(record["request_id"], str)
            self.assertIsInstance(record["duration_ms"], (int, float))


class TestLocalBindDefault(unittest.TestCase):
    def test_main_binds_loopback_by_default_without_opening_a_socket(self):
        addresses = []

        class StubHTTPServer:
            def __init__(self, address, handler):
                addresses.append(address)

            def serve_forever(self):
                return None

        with (
            patch.object(sys, "argv", ["server.py"]),
            patch.object(server, "_configure_runtime"),
            patch.object(server, "_restrict_runtime_permissions"),
            patch.object(server, "_prepare_state", return_value=[]),
            patch.object(server, "SafeThreadingHTTPServer", StubHTTPServer),
        ):
            self.assertEqual(server.main(), 0)
        self.assertEqual(addresses, [("127.0.0.1", 8090)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
