#!/usr/bin/env python3
"""Regression tests for credential-safe reuse in scripts/ember_up.sh.

Run with ``python3 scripts/test_ember_reuse_auth.py -v``. The tests start only
isolated loopback Live Agents and use disposable state; no write goal is sent.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EMBER_UP = ROOT / "scripts" / "ember_up.sh"
LIVE_AGENT = ROOT / "live-agent" / "server.py"
CREDENTIAL_ENV = (
    "AI2027_WRITE_TOKEN",
    "AI2027_READ_TOKEN_ID",
    "AI2027_READ_TOKEN_SECRET",
    "AI2027_READ_TOKEN_HASHES",
    "AI2027_READ_CREDENTIAL",
    "AI2027_LOCAL_API_CREDENTIALS_FILE",
    "AI2027_LIVE_AGENT_URL",
    "AI2027_COMMAND_HUD_URL",
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def private_reader_hashes(reader: tuple[str, str] | None) -> str:
    if reader is None:
        return ""
    token_id, secret = reader
    return json.dumps({token_id: hashlib.sha256(secret.encode("utf-8")).hexdigest()})


class EmberReuseCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="ai2027-ember-reuse-auth-")
        self.root = Path(self.tmp.name)
        self.agents: list[subprocess.Popen[str]] = []

    def tearDown(self) -> None:
        for agent in self.agents:
            self._stop_agent(agent)
        self.tmp.cleanup()

    def _start_agent(
        self, *, reader: tuple[str, str] | None, writer: str | None,
    ) -> tuple[subprocess.Popen[str], int, Path]:
        port = free_port()
        runtime = self.root / f"live-runtime-{len(self.agents)}"
        env = os.environ.copy()
        for name in CREDENTIAL_ENV + (
            "AI2027_READ_ONLY", "AI2027_LIVE_RUNTIME_DIR", "AI2027_RUNTIME_DIR",
        ):
            env.pop(name, None)
        env["AI2027_LIVE_RUNTIME_DIR"] = str(runtime)
        env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost"
        env["AI2027_READ_TOKEN_HASHES"] = private_reader_hashes(reader)
        if writer is not None:
            env["AI2027_WRITE_TOKEN"] = writer
        proc = subprocess.Popen(
            [sys.executable, str(LIVE_AGENT), "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
        self.agents.append(proc)
        deadline = time.monotonic() + 15
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                output = proc.stdout.read() if proc.stdout else ""
                self.fail(f"isolated Live Agent exited before health: {output[-3000:]}")
            try:
                with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
                    if response.status == 200:
                        return proc, port, runtime
            except (OSError, urllib.error.URLError):
                time.sleep(0.05)
        self.fail("isolated Live Agent did not become healthy")
        raise AssertionError("unreachable")

    @staticmethod
    def _stop_agent(proc: subprocess.Popen[str]) -> str:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            output, _ = proc.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            output, _ = proc.communicate(timeout=5)
        return output or ""

    def _run_ember(
        self, live_port: int, *, credentials: tuple[str, str, str] | None,
        label: str, no_smoke: bool = True,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, str], int]:
        state = self.root / f"ember-state-{label}"
        hud_port = free_port()
        env = os.environ.copy()
        for name in CREDENTIAL_ENV + (
            "AI2027_READ_ONLY", "AI2027_EMBER_STATE_DIR", "AI2027_LIVE_RUNTIME_DIR",
            "AI2027_HUD_RUNTIME_DIR",
        ):
            env.pop(name, None)
        env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost"
        env.update({
            "AI2027_EMBER_STATE_DIR": str(state),
            "AI2027_LIVE_RUNTIME_DIR": str(state / "live-runtime"),
            "AI2027_HUD_RUNTIME_DIR": str(state / "hud-runtime"),
        })
        if credentials is not None:
            reader_id, reader_secret, writer = credentials
            env.update({
                "AI2027_READ_TOKEN_ID": reader_id,
                "AI2027_READ_TOKEN_SECRET": reader_secret,
                "AI2027_WRITE_TOKEN": writer,
            })
        args = [
            str(EMBER_UP), "--no-browser", "--json", "--live-port", str(live_port),
            "--hud-port", str(hud_port),
        ]
        if no_smoke:
            args.append("--no-smoke")
        result = subprocess.run(
            args, cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=45, check=False,
        )
        return result, env, hud_port

    def _stop_ember_owned_services(self, env: dict[str, str]) -> None:
        subprocess.run(
            [str(EMBER_UP), "--stop"], cwd=ROOT, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15, check=False,
        )

    def _assert_no_secret_output(
        self, output: str, secrets: tuple[str, ...], runtime: Path | None = None,
    ) -> None:
        for secret in secrets:
            if secret:
                self.assertNotIn(secret, output)
        if runtime is not None:
            for path in runtime.rglob("*.log"):
                content = path.read_text(encoding="utf-8", errors="replace")
                for secret in secrets:
                    if secret:
                        self.assertNotIn(secret, content, str(path))

    def test_healthy_service_without_reader_is_not_reused_under_no_smoke(self):
        read_id = "ember-reader"
        read_secret = "ember-read-secret-" + "r" * 40
        writer = "+/=" + "w" * 29
        agent, port, runtime = self._start_agent(reader=None, writer=writer)
        result, _, _ = self._run_ember(
            port, credentials=(read_id, read_secret, writer), label="missing-reader",
            no_smoke=True,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("could not prove both local reader and operator credentials", result.stdout)
        self.assertIsNone(agent.poll(), "the unverified service must not be stopped")
        self.assertEqual(list((runtime / "runs").glob("*.json")), [])
        self._assert_no_secret_output(result.stdout, (read_secret, writer), runtime)
        service_log = self._stop_agent(agent)
        self._assert_no_secret_output(service_log, (read_secret, writer))

    def test_different_reader_or_operator_is_not_reused_without_smoking(self):
        service_reader = ("service-reader", "service-read-secret-" + "s" * 40)
        requested_reader = ("ember-reader", "ember-read-secret-" + "r" * 40)
        service_writer = "+/=" + "s" * 29
        requested_writer = "+/=" + "e" * 29
        scenarios = (
            ("different-reader", service_reader, service_writer,
             requested_reader, service_writer),
            ("different-operator", service_reader, service_writer,
             service_reader, requested_writer),
        )
        for label, old_reader, old_writer, new_reader, new_writer in scenarios:
            with self.subTest(case=label):
                agent, port, runtime = self._start_agent(reader=old_reader, writer=old_writer)
                result, _, _ = self._run_ember(
                    port, credentials=(new_reader[0], new_reader[1], new_writer),
                    label=label, no_smoke=True,
                )
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn(
                    "could not prove both local reader and operator credentials", result.stdout,
                )
                self.assertIsNone(agent.poll(), "the unverified service must not be stopped")
                self.assertEqual(list((runtime / "runs").glob("*.json")), [])
                self._assert_no_secret_output(
                    result.stdout,
                    (old_reader[1], old_writer, new_reader[1], new_writer), runtime,
                )
                service_log = self._stop_agent(agent)
                self._assert_no_secret_output(
                    service_log,
                    (old_reader[1], old_writer, new_reader[1], new_writer),
                )

    def test_matching_credentials_reuse_without_a_write_even_at_token_boundaries(self):
        reader = ("ember-reader", "ember-read-secret-" + "r" * 40)
        for label, writer in (
            ("minimum", "+/=" + "w" * 29),
            ("maximum", "+/=" + "w" * 509),
        ):
            with self.subTest(token_length=len(writer)):
                agent, port, runtime = self._start_agent(reader=reader, writer=writer)
                result, ember_env, _ = self._run_ember(
                    port, credentials=(reader[0], reader[1], writer), label=label,
                    no_smoke=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout)
                state = json.loads(result.stdout)
                self.assertEqual(state["reused"], "live-agent")
                self.assertEqual(state["smoke"], "skipped")
                self.assertIsNone(agent.poll(), "the pre-existing service stays alive")
                self.assertEqual(list((runtime / "runs").glob("*.json")), [])
                self._assert_no_secret_output(result.stdout, (reader[1], writer), runtime)
                self._stop_ember_owned_services(ember_env)
                self.assertIsNone(agent.poll(), "--stop must not stop the reused service")
                service_log = self._stop_agent(agent)
                self._assert_no_secret_output(service_log, (reader[1], writer))

    def test_no_local_credential_file_does_not_mint_an_identity_for_a_healthy_service(self):
        reader = ("service-reader", "service-read-secret-" + "s" * 40)
        writer = "service-write-secret-" + "w" * 40
        agent, port, runtime = self._start_agent(reader=reader, writer=writer)
        result, env, _ = self._run_ember(
            port, credentials=None, label="no-local-creds", no_smoke=True,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("no local credential file is available", result.stdout)
        self.assertFalse(
            (Path(env["AI2027_EMBER_STATE_DIR"]) / "api-credentials.json").exists(),
            "do not mint credentials when a healthy but unverified service is present",
        )
        self.assertIsNone(agent.poll())
        self.assertEqual(list((runtime / "runs").glob("*.json")), [])
        self._assert_no_secret_output(result.stdout, (reader[1], writer), runtime)
        service_log = self._stop_agent(agent)
        self._assert_no_secret_output(service_log, (reader[1], writer))

    def test_invalid_environment_write_tokens_fail_closed_without_logging_values(self):
        reader_id = "ember-reader"
        reader_secret = "ember-read-secret-" + "r" * 40
        invalid_tokens = (
            "+/=" + "w" * 28,             # 31 characters
            "+/=" + "w" * 510,            # 513 characters
            "w" * 16 + " " + "w" * 16,    # embedded whitespace
            "w" * 31 + "é",                # non-ASCII
        )
        for index, token in enumerate(invalid_tokens):
            with self.subTest(length=len(token), index=index):
                result, _, _ = self._run_ember(
                    free_port(), credentials=(reader_id, reader_secret, token),
                    label=f"invalid-token-{index}",
                )
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("local workspace credentials are invalid", result.stdout)
                self._assert_no_secret_output(result.stdout, (reader_secret, token))


if __name__ == "__main__":
    unittest.main(verbosity=2)
