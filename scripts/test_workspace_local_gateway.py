#!/usr/bin/env python3
"""Real local regression: ember_up.sh -> Workspace Vite proxy -> Live Agent/HUD.

This is an integration test, not a simulated proxy or a public deployment test.
It starts isolated loopback services with disposable state and owner-only local
credentials, then exercises the same-origin browser-facing URLs over HTTP.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "workspace-ui"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request(base: str, path: str, *, method: str = "GET", payload: dict | None = None,
            headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = dict(headers or {})
    if payload is not None:
        request_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(
        base.rstrip("/") + path, data=data, headers=request_headers, method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()


def json_body(response: tuple[int, dict[str, str], bytes]) -> dict:
    try:
        value = json.loads(response[2])
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AssertionError(f"expected JSON response, got HTTP {response[0]}") from exc
    if not isinstance(value, dict):
        raise AssertionError("expected a JSON object response")
    return value


def stop_process_group(proc: subprocess.Popen[str] | None) -> str:
    if proc is None:
        return ""
    try:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=8)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
    finally:
        output = proc.stdout.read() if proc.stdout is not None else ""
        if proc.stdout is not None:
            proc.stdout.close()
    return output


class LocalWorkspaceGatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("npm") is None:
            raise unittest.SkipTest("npm is required for the Vite integration test")
        if not (WORKSPACE / "node_modules" / "vite").is_dir():
            raise unittest.SkipTest(
                "the dedicated Workspace gateway CI job installs npm dependencies before running this integration test"
            )

        cls.tmp = tempfile.TemporaryDirectory(prefix="ai2027-workspace-gateway-")
        cls.tmp_root = Path(cls.tmp.name)
        cls.state_dir = cls.tmp_root / "ember-state"
        cls.live_runtime = cls.tmp_root / "live-runtime"
        cls.hud_runtime = cls.tmp_root / "hud-runtime"
        cls.live_port, cls.hud_port, cls.vite_port = free_port(), free_port(), free_port()
        cls.base = f"http://127.0.0.1:{cls.vite_port}"
        cls.ember_env = os.environ.copy()
        for name in (
            "AI2027_WRITE_TOKEN", "AI2027_READ_TOKEN_ID", "AI2027_READ_TOKEN_SECRET",
            "AI2027_READ_TOKEN_HASHES", "AI2027_LOCAL_API_CREDENTIALS_FILE",
            "AI2027_LIVE_AGENT_URL", "AI2027_COMMAND_HUD_URL",
        ):
            cls.ember_env.pop(name, None)
        cls.ember_env.update({
            "AI2027_EMBER_STATE_DIR": str(cls.state_dir),
            "AI2027_LIVE_RUNTIME_DIR": str(cls.live_runtime),
            "AI2027_HUD_RUNTIME_DIR": str(cls.hud_runtime),
            "AI2027_READ_ONLY": "0",
        })
        cls.ember_proc = None
        cls.vite_proc = None
        try:
            cls._start_ember(cls.ember_env, cls.live_port, cls.hud_port)
            cls.credentials_path = cls.state_dir / "api-credentials.json"
            cls.credentials = json.loads(cls.credentials_path.read_text(encoding="utf-8"))
            cls.vite_env = cls.ember_env.copy()
            cls.vite_env.update({
                "AI2027_LOCAL_API_CREDENTIALS_FILE": str(cls.credentials_path),
                "AI2027_LIVE_AGENT_URL": f"http://127.0.0.1:{cls.live_port}",
                "AI2027_COMMAND_HUD_URL": f"http://127.0.0.1:{cls.hud_port}",
            })
            cls.vite_proc = cls._start_vite(cls.vite_env, cls.vite_port)
            cls._wait_for_vite(cls.base, cls.vite_proc)
        except Exception:
            stop_process_group(cls.vite_proc)
            cls._stop_ember(cls.ember_env)
            cls.tmp.cleanup()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        stop_process_group(getattr(cls, "vite_proc", None))
        cls._stop_ember(getattr(cls, "ember_env", None))
        if hasattr(cls, "tmp"):
            cls.tmp.cleanup()

    @classmethod
    def _start_ember(cls, env: dict[str, str], live_port: int, hud_port: int) -> None:
        result = subprocess.run(
            [str(ROOT / "scripts" / "ember_up.sh"), "--no-browser", "--no-smoke",
             "--json", "--live-port", str(live_port), "--hud-port", str(hud_port)],
            cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=60, check=False,
        )
        if result.returncode != 0:
            raise AssertionError(f"ember_up.sh failed with status {result.returncode}: {result.stdout[-3000:]}")
        if not (Path(env["AI2027_EMBER_STATE_DIR"]) / "api-credentials.json").is_file():
            raise AssertionError("ember_up.sh did not provision its local credential file")

    @classmethod
    def _stop_ember(cls, env: dict[str, str] | None) -> None:
        if not env:
            return
        subprocess.run(
            [str(ROOT / "scripts" / "ember_up.sh"), "--stop"],
            cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=20, check=False,
        )

    @staticmethod
    def _start_vite(env: dict[str, str], port: int,
                    host: str = "127.0.0.1") -> subprocess.Popen[str]:
        return subprocess.Popen(
            ["npm", "run", "dev", "--", "--host", host, "--port", str(port), "--strictPort"],
            cwd=WORKSPACE, env=env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, start_new_session=True,
        )

    @staticmethod
    def _wait_for_vite(base: str, proc: subprocess.Popen[str]) -> None:
        deadline = time.monotonic() + 40
        last_error = "not ready"
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                output = proc.stdout.read() if proc.stdout else ""
                raise AssertionError(f"Vite exited before startup:\n{output[-5000:]}")
            try:
                status, _, _ = request(base, "/")
                if status == 200:
                    return
                last_error = f"HTTP {status}"
            except (OSError, TimeoutError) as exc:
                last_error = str(exc)
            time.sleep(0.2)
        output = proc.stdout.read() if proc.stdout else ""
        raise AssertionError(f"Vite did not start: {last_error}\n{output[-5000:]}")

    def test_ember_workspace_reads_writes_and_files_stay_server_authenticated(self):
        file_mode = stat.S_IMODE(self.credentials_path.stat().st_mode)
        dir_mode = stat.S_IMODE(self.state_dir.stat().st_mode)
        self.assertEqual(file_mode & 0o077, 0, "credential file must be owner-only")
        self.assertEqual(dir_mode & 0o077, 0, "credential directory must be owner-only")

        health = request(self.base, "/api/health")
        self.assertEqual(health[0], 200)
        self.assertEqual(json_body(health), {"ok": True, "service": "ai-2027-live-agent"})

        # A browser-shaped request has no usable bearer credential. The proxy
        # injects its reader server-side and ignores a caller-supplied value.
        headers = {"Authorization": "Bearer browser-supplied-invalid-token"}
        capabilities = request(self.base, "/api/capabilities", headers=headers)
        self.assertEqual(capabilities[0], 200, json_body(capabilities))
        capability_body = json_body(capabilities)
        self.assertGreaterEqual(len(capability_body.get("supported_goals", [])), 1)
        self.assertIsInstance(capability_body.get("memory"), int)
        self.assertEqual(request(self.base, "/api/runs")[0], 200)

        page = request(self.base, "/")
        app_module = request(self.base, "/src/App.tsx")
        self.assertEqual(page[0], 200)
        self.assertEqual(app_module[0], 200)
        browser_source = (page[2] + app_module[2]).decode("utf-8", errors="replace")
        for secret in (self.credentials["read_secret"], self.credentials["write_token"]):
            self.assertNotIn(secret, browser_source)

        payload = {
            "goal_type": "create_file",
            "params": {"path": "gateway/regression-proof.txt", "content": "trusted local proxy"},
        }
        write_headers = {
            "Origin": self.base,
            "Authorization": "Bearer browser-supplied-invalid-token",
        }
        created = request(self.base, "/api/run", method="POST", payload=payload,
                          headers=write_headers)
        self.assertEqual(created[0], 200, json_body(created))
        record = json_body(created)
        self.assertEqual(record.get("status"), "VERIFIED")
        self.assertEqual(record.get("cleanup", {}).get("status"), "not_applicable")
        self.assertEqual(
            record.get("cleanup", {}).get("reason"), "direct_live_agent_mode")
        run_id = record["run_id"]

        fetched = request(self.base, f"/api/run/{run_id}", headers=headers)
        self.assertEqual(fetched[0], 200)
        fetched_cleanup = json_body(fetched).get("cleanup", {})
        self.assertEqual(fetched_cleanup.get("status"), "not_applicable")
        self.assertEqual(fetched_cleanup.get("reason"), "direct_live_agent_mode")
        artifact = request(self.base, "/api/file?run=" + run_id +
                           "&path=gateway%2Fregression-proof.txt", headers=headers)
        self.assertEqual(artifact[0], 200)
        self.assertEqual(artifact[2], b"trusted local proxy")
        summaries = json_body(request(self.base, "/api/runs"))["runs"]
        summary_cleanup = summaries[0].get("cleanup", {})
        self.assertEqual(summary_cleanup.get("status"), "not_applicable")
        self.assertEqual(summary_cleanup.get("reason"), "direct_live_agent_mode")

    def test_local_host_origin_and_write_method_guards_precede_the_proxy(self):
        cross_origin = request(
            self.base, "/api/run", method="POST",
            payload={"goal_type": "create_file", "params": {"path": "blocked.txt"}},
            headers={"Origin": "http://evil.example"},
        )
        self.assertEqual(cross_origin[0], 403)
        bad_host = request(
            self.base, "/api/runs", headers={"Host": "evil.example"},
        )
        self.assertEqual(bad_host[0], 403)
        disallowed_post = request(
            self.base, "/api/file", method="POST", payload={},
            headers={"Origin": self.base},
        )
        self.assertEqual(disallowed_post[0], 405)
        cross_site_read = request(
            self.base, "/api/runs", headers={"Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(cross_site_read[0], 403)
        studio_csrf = request(
            self.base, "/studio-api/v1/studio/sessions", method="POST", payload={},
        )
        self.assertEqual(studio_csrf[0], 403)
        studio_cross_origin = request(
            self.base, "/studio-api/v1/studio/sessions", method="POST", payload={},
            headers={"Origin": "http://evil.example"},
        )
        self.assertEqual(studio_cross_origin[0], 403)
        self.assertEqual(len(json_body(request(self.base, "/api/runs"))["runs"]), 1)

    def test_missing_local_credentials_fail_closed_without_affecting_health(self):
        unavailable = self.state_dir / "api-credentials.unavailable"
        self.credentials_path.replace(unavailable)
        try:
            self.assertEqual(request(self.base, "/api/health")[0], 200)
            protected = request(self.base, "/api/runs")
            self.assertEqual(protected[0], 503)
            self.assertEqual(json_body(protected).get("error"), "local reader credentials unavailable")
            write = request(
                self.base, "/api/run", method="POST",
                payload={"goal_type": "create_file", "params": {"path": "blocked-missing-token.txt"}},
                headers={"Origin": self.base},
            )
            self.assertEqual(write[0], 503)
        finally:
            unavailable.replace(self.credentials_path)

    def test_symlinked_local_credentials_fail_closed(self):
        if os.name != "posix":
            self.skipTest("owner-only file symlink behavior requires POSIX")
        original = self.credentials_path
        private_target = self.state_dir / "api-credentials.private"
        original.replace(private_target)
        try:
            original.symlink_to(private_target)
            self.assertEqual(request(self.base, "/api/health")[0], 200)
            protected = request(self.base, "/api/runs")
            self.assertEqual(protected[0], 503)
            self.assertEqual(
                json_body(protected).get("error"),
                "local reader credentials unavailable",
            )
            write = request(
                self.base, "/api/run", method="POST",
                payload={"goal_type": "create_file", "params": {"path": "blocked-symlink.txt"}},
                headers={"Origin": self.base},
            )
            self.assertEqual(write[0], 503)
        finally:
            if original.is_symlink():
                original.unlink()
            if private_target.exists():
                private_target.replace(original)

    def test_ops_proxy_rewrites_only_validated_local_origin_for_hud(self):
        hud_page = request(self.base, "/ops/")
        self.assertEqual(hud_page[0], 200)
        self.assertIn(b"Command HUD", hud_page[2])
        self.assertEqual(request(self.base, "/ops/app.js")[0], 200)
        health = json_body(request(self.base, "/ops/api/health"))
        self.assertEqual(health.get("service"), "command-hud")

        run = request(
            self.base, "/ops/api/run", method="POST",
            payload={"goal_type": "create_file", "params": {
                "path": "ops-proxy/proof.txt", "content": "same-origin"}},
            headers={"Origin": self.base},
        )
        self.assertEqual(run[0], 200, json_body(run))
        self.assertEqual(json_body(run).get("status"), "VERIFIED")

        rejected = request(
            self.base, "/ops/api/run", method="POST",
            payload={"goal_type": "create_file", "params": {"path": "ops-proxy/blocked.txt"}},
            headers={"Origin": "http://evil.example"},
        )
        self.assertEqual(rejected[0], 403)

    def test_vite_write_credentials_follow_shared_ascii_contract_and_stay_out_of_logs(self):
        if os.name != "posix":
            self.skipTest("the gateway credential fixture requires verifiable POSIX modes")
        credential_dir = self.tmp_root / "vite-token-policy"
        credential_dir.mkdir(mode=0o700)
        credential_dir.chmod(0o700)
        credential_path = credential_dir / "api-credentials.json"
        read_id = "vite-policy-reader"
        read_secret = "vite-policy-read-secret-" + "r" * 40
        credentials = {
            "read_id": read_id,
            "read_secret": read_secret,
            "read_hashes": {read_id: hashlib.sha256(read_secret.encode()).hexdigest()},
        }

        def write_credentials(token: str) -> None:
            credential_path.write_text(
                json.dumps({**credentials, "write_token": token}), encoding="utf-8",
            )
            credential_path.chmod(0o600)

        forwarded: list[str | None] = []

        class OperatorCaptureHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                forwarded.append(self.headers.get("Authorization"))
                body = b'{"ok":true,"service":"credential-policy-stub"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return None

        upstream = HTTPServer(("127.0.0.1", 0), OperatorCaptureHandler)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        vite_port = free_port()
        base = f"http://127.0.0.1:{vite_port}"
        vite_env = self.vite_env.copy()
        vite_env.update({
            "AI2027_LOCAL_API_CREDENTIALS_FILE": str(credential_path),
            "AI2027_LIVE_AGENT_URL": f"http://127.0.0.1:{upstream.server_port}",
        })
        vite_proc: subprocess.Popen[str] | None = None
        vite_log = ""
        valid = ("+/=" + "w" * 29, "+/=" + "w" * 509)
        invalid = (
            "+/=" + "w" * 28,
            "+/=" + "w" * 510,
            "w" * 16 + " " + "w" * 16,
            "w" * 32 + "\n",
            "w" * 31 + "é",
        )
        try:
            write_credentials(valid[0])
            vite_proc = self._start_vite(vite_env, vite_port)
            self._wait_for_vite(base, vite_proc)
            payload = {"goal_type": "create_file", "params": {"path": "policy.txt"}}
            for token in valid:
                write_credentials(token)
                response = request(
                    base, "/api/run", method="POST", payload=payload,
                    headers={"Origin": base},
                )
                self.assertEqual(response[0], 200, json_body(response))
                self.assertEqual(forwarded[-1], f"Bearer {token}")
            self.assertEqual(len(forwarded), len(valid))

            for token in invalid:
                write_credentials(token)
                response = request(
                    base, "/api/run", method="POST", payload=payload,
                    headers={"Origin": base},
                )
                self.assertEqual(response[0], 503, json_body(response))
                self.assertEqual(json_body(response).get("error"),
                                 "local operator credentials unavailable")
                self.assertNotIn(token.encode(), response[2])
            self.assertEqual(len(forwarded), len(valid),
                             "invalid local operator tokens must not reach the upstream")
        finally:
            vite_log = stop_process_group(vite_proc)
            upstream.shutdown()
            upstream.server_close()
            upstream_thread.join(timeout=5)
        for token in valid + invalid:
            self.assertNotIn(token, vite_log)

    def test_public_bind_is_rejected_instead_of_creating_a_preview_gateway(self):
        port = free_port()
        proc = self._start_vite(self.vite_env, port, host="0.0.0.0")
        try:
            deadline = time.monotonic() + 12
            while proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            if proc.poll() is None:
                self.fail("Vite remained running with a public bind address")
            output = proc.stdout.read() if proc.stdout else ""
            self.assertNotEqual(proc.returncode, 0, output)
            self.assertIn("local-only", output)
        finally:
            stop_process_group(proc)

    def test_read_only_live_agent_rejects_proxy_write_even_with_operator_file(self):
        readonly_root = self.tmp_root / "readonly"
        readonly_root.mkdir(mode=0o700)
        state_dir = readonly_root / "ember-state"
        live_runtime = readonly_root / "live-runtime"
        hud_runtime = readonly_root / "hud-runtime"
        live_port, hud_port, vite_port = free_port(), free_port(), free_port()
        base = f"http://127.0.0.1:{vite_port}"
        env = self.ember_env.copy()
        env.update({
            "AI2027_EMBER_STATE_DIR": str(state_dir),
            "AI2027_LIVE_RUNTIME_DIR": str(live_runtime),
            "AI2027_HUD_RUNTIME_DIR": str(hud_runtime),
            "AI2027_READ_ONLY": "1",
        })
        vite_proc: subprocess.Popen[str] | None = None
        try:
            self._start_ember(env, live_port, hud_port)
            credentials_path = state_dir / "api-credentials.json"
            vite_env = env.copy()
            vite_env.update({
                "AI2027_LOCAL_API_CREDENTIALS_FILE": str(credentials_path),
                "AI2027_LIVE_AGENT_URL": f"http://127.0.0.1:{live_port}",
                "AI2027_COMMAND_HUD_URL": f"http://127.0.0.1:{hud_port}",
            })
            vite_proc = self._start_vite(vite_env, vite_port)
            self._wait_for_vite(base, vite_proc)
            rejected = request(
                base, "/api/run", method="POST",
                payload={"goal_type": "create_file", "params": {"path": "readonly.txt"}},
                headers={"Origin": base},
            )
            self.assertEqual(rejected[0], 403, json_body(rejected))
            self.assertEqual(json_body(rejected).get("error"), "writes are disabled")
            self.assertEqual(json_body(request(base, "/api/runs"))["runs"], [])
        finally:
            stop_process_group(vite_proc)
            self._stop_ember(env)


if __name__ == "__main__":
    unittest.main(verbosity=2)
