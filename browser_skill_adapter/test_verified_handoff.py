#!/usr/bin/env python3
"""Offline handoff tests: real loopback HTTP, a simulated pinned bsk transport."""
from __future__ import annotations

import contextlib
import copy
import http.client
import io
import json
import multiprocessing
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from pathlib import Path, PureWindowsPath
from unittest import mock
from urllib.parse import urlsplit

# Make this file runnable from any working directory and on any OS. It imports
# `adapter` flat, and `adapter` builds on the shared kernel in ../kernel-2027;
# neither resolves from sys.path alone when the runner's cwd differs from the
# repository root (which is exactly what a Windows job with `shell: python`
# does). Resolving from __file__ rather than from cwd keeps it deterministic.
_HERE = Path(__file__).resolve().parent
for _entry in (str(_HERE), str(_HERE.parent / "kernel-2027")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)


from adapter import BskCommandError
from handoff_contract import (CHECKS, MAX_JSON_BYTES, SIGN_NAMESPACE, SIGN_PRINCIPAL, HandoffError, Request,
                              canonical, read_document, sha256, verify_receipt, verify_signature)
import verified_handoff as handoff
import windows_acl


_PROCESS_TIMEOUT = 20
_CRASH_EXIT = 73


def _set_windows_test_acl(path: Path, *, grant_everyone: bool = False) -> None:
    # Real Windows fixtures only. PowerShell/.NET writes a known ACL; the
    # production checker independently reads the descriptor through Win32.
    script = r"""
    $ErrorActionPreference = 'Stop'
    $item = Get-Item -LiteralPath $env:HANDOFF_TEST_ACL_PATH
    $owner = [Security.Principal.WindowsIdentity]::GetCurrent().User
    if ($item.PSIsContainer) {
        $acl = [Security.AccessControl.DirectorySecurity]::new()
        $inherit = 'ContainerInherit, ObjectInherit'
    } else {
        $acl = [Security.AccessControl.FileSecurity]::new()
        $inherit = 'None'
    }
    $acl.SetOwner($owner)
    $acl.SetAccessRuleProtection($true, $false)
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        $owner, 'FullControl', $inherit, 'None', 'Allow'))
    if ($env:HANDOFF_TEST_ACL_BROAD -eq '1') {
        $everyone = [Security.Principal.SecurityIdentifier]::new('S-1-1-0')
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            $everyone, 'ReadAndExecute', $inherit, 'None', 'Allow'))
    }
    Set-Acl -LiteralPath $item.FullName -AclObject $acl
    """
    exe = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    subprocess.run([str(exe), "-NoProfile", "-NonInteractive", "-Command", script],
                   env=os.environ | {"HANDOFF_TEST_ACL_PATH": str(path),
                                     "HANDOFF_TEST_ACL_BROAD": "1" if grant_everyone else "0"},
                   timeout=_PROCESS_TIMEOUT, stdin=subprocess.DEVNULL, capture_output=True, check=True)


class FixtureTransport:
    """Narrow v0.5.0 --json shapes, not the legacy adapter's fixtures.

    For a nominal navigation, fetch the fixture by real HTTP. Only the
    BrowserSkill IPC and Chrome observation are simulated in these tests.
    """

    browser_id = "7eb467ac"
    session_id = "local987"
    window_id = 1024
    tab_id = 801

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.faults: dict[str, object] = {}
        self.url: str | None = None
        self.observed: str = ""
        self.active = False
        self.lease: str | None = None
        self.status = {
            "daemon_version": "0.5.0", "protocol_version": "2.0", "pid": 1001,
            "local_auth": {"mode": "required", "paired_devices": 1},
            "sessions": [],
            "browsers": [{"instance_id": self.browser_id, "browser_name": "chrome",
                          "label": handoff.LAB_LABEL, "extension_version": "0.5.0",
                          "extension_protocol_version": "2.0", "connected_at_ms": 1760000000123,
                          "session_count": 0}],
        }

    def call(self, args: list[str], *, timeout: float, expect_json: bool = True):
        self.calls.append(args.copy())
        op = args[0]
        if op in ("session", "tab"):
            op = " ".join(args[:2])
        if op == "session request":
            op = args[-1]
        if op in self.faults:
            override = self.faults[op]
            if isinstance(override, BaseException):
                raise override
            if callable(override):
                return override(args)
            return copy.deepcopy(override)
        if op == "--version":
            assert expect_json is False
            return "bsk 0.5.0"
        if op == "status":
            return copy.deepcopy(self.status)
        if args == ["daemon", "devices"]:
            return [{"kind": "local", "status": "active", "browser_id": self.browser_id,
                     "device_id": "lab-device-not-for-export"}]
        if op == "--prepare":
            self.lease = args[2]
            assert re.fullmatch(r"[0-9]+:[0-9a-f-]{36}", self.lease)
            return {"request_id": self.lease, "state": "prepared", "session": None, "cleanup_error": None}
        if op == "session start":
            assert self.lease and args == ["session", "start", "--request-id", self.lease, "--browser", self.browser_id,
                                           "--incognito", "--no-focus", "--name", "AI-2027 local handoff"]
            self.active = True
            self.status["sessions"] = [{"session_id": self.session_id, "browser_instance_id": self.browser_id,
                                        "agent_window_id": self.window_id}]
            self.status["browsers"][0]["session_count"] = 1
            return {"session_id": self.session_id, "browser_instance_id": self.browser_id, "agent_window_id": self.window_id}
        if op == "tab list":
            assert self.active and args == ["tab", "list", "--session", self.session_id, "--scope", "agent"]
            return {"tabs": [{"tab_id": self.tab_id, "window_id": self.window_id, "scope": "agent",
                              "url": self.url or "about:blank"}]}
        if op == "navigate":
            assert self.active and args[2:] == ["--session", self.session_id, "--tab-id", str(self.tab_id),
                                                  "--wait-until", "load", "--timeout", "10s"]
            assert self.url is None, "navigation cannot be replayed"
            self.url = args[1]
            target = urlsplit(self.url)
            assert target.scheme == "http" and target.hostname == handoff.LOOPBACK and target.port
            conn = http.client.HTTPConnection(target.hostname, target.port, timeout=3)
            try:
                conn.request("GET", target.path)
                res = conn.getresponse()
                assert res.status == 200 and res.headers["Cache-Control"] == "no-store"
                text = res.read().decode()
            finally:
                conn.close()
            self.observed = re.search(r"<h1>([A-Z0-9a-f-]+)</h1>", text).group(1)
            return {"tab_id": self.tab_id, "url": self.url, "final_url": self.url, "reached": "load"}
        if op == "observe":
            assert self.active and args == ["observe", "--session", self.session_id, "--tab-id", str(self.tab_id),
                                            "--max-tokens", "256"]
            return {
                "tab_id": self.tab_id, "ref_count": 1,
                "text": f'@e1 heading "{self.observed}"\nCookie: synthetic-cookie-do-not-export',
                "session_token": "synthetic-session-do-not-export",
            }
        if op == "--cancel":
            assert args[2] == self.lease
            self.active = False
            self.status["sessions"] = []
            self.status["browsers"][0]["session_count"] = 0
            return {"request_id": self.lease, "state": "closed", "session": None, "cleanup_error": None}
        raise AssertionError(f"unrecognized bsk operation: {op}")


def _process_execute(document, digest, state, out, now, hold, resume, crash, channel):
    """Spawn-only worker: independent snapshot, runner and process memory."""
    transport = FixtureTransport()
    call = transport.call

    def simulate(args, **kwargs):
        if crash and args[:2] == ["session", "start"]:
            call(args, **kwargs)
            os._exit(_CRASH_EXIT)  # no finally blocks run; the window is only simulated
        if hold is not None and args[0] == "navigate":
            hold.set()
            if not resume.wait(2 * _PROCESS_TIMEOUT):
                raise AssertionError("parent did not release the simulated browser")
        return call(args, **kwargs)

    transport.call = simulate
    try:
        handoff.execute(Request.parse(document), out, state=state, runner=transport,
                        expected=transport.browser_id, expected_request_sha256=digest,
                        approve=lambda *_: None, clock=lambda: now)
        reason = "verified"
    except HandoffError as exc:
        reason = exc.reason
    with channel:
        channel.send((os.getpid(), reason, len(transport.calls),
                      sum(args[:2] == ["session", "start"] for args in transport.calls)))


def _process_claim(lock, gate, channel):
    gate.wait(timeout=_PROCESS_TIMEOUT)
    try:
        handoff._write_new(lock, b"")
        outcome = "claimed"
    except FileExistsError:
        outcome = "already_exists"
    with channel:
        channel.send((os.getpid(), outcome))


class HandoffTest(unittest.TestCase):
    def setUp(self) -> None:
        work = tempfile.TemporaryDirectory(dir=Path.home() if os.name == "nt" else None)
        self.addCleanup(work.cleanup)
        self.path = Path(work.name)
        self.state = self.path / handoff.LOCAL_STATE
        if os.name == "nt":
            _set_windows_test_acl(self.path)
            self.state.mkdir()
            _set_windows_test_acl(self.state)
        self.out = self.path / "receipt.json"
        self.now = int(time.time())
        self.request = Request.create(300, now=self.now)
        self.transport = FixtureTransport()

    def assert_state_untouched(self):
        if os.name == "nt":
            self.assertEqual(list(self.state.iterdir()), [])
        else:
            self.assertFalse(self.state.exists())

    def start_worker(self, context, target, *args):
        reader, writer = context.Pipe(duplex=False)
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        process = context.Process(target=target, args=(*args, writer))
        process.start()
        writer.close()
        self.addCleanup(self.stop_worker, process)
        return process, reader

    @staticmethod
    def stop_worker(process):
        if process.is_alive():
            process.kill()
        process.join(timeout=_PROCESS_TIMEOUT)
        process.close()

    def start_attempt(self, context, out, *, hold=None, resume=None, crash=False):
        return self.start_worker(context, _process_execute, self.request.document(), self.request.digest,
                                 self.state, out, self.now, hold, resume, crash)

    def worker_result(self, worker):
        process, reader = worker
        process.join(timeout=_PROCESS_TIMEOUT)
        self.assertEqual(process.exitcode, 0, "spawned test worker did not exit successfully")
        self.assertTrue(reader.poll(1), "worker did not report a result")
        return reader.recv()

    def execute(self, *, out=None, clock=None, approve=None, expected=None):
        return handoff.execute(self.request, out or self.out, state=self.state, runner=self.transport,
                               clock=clock or (lambda: self.now), approve=approve or (lambda request, browser: None),
                               expected=self.transport.browser_id if expected is None else expected,
                               expected_request_sha256=self.request.digest)

    def reject(self, reason: str, **kwargs) -> HandoffError:
        with self.assertRaises(HandoffError) as ctx:
            self.execute(**kwargs)
        self.assertEqual(ctx.exception.reason, reason)
        self.assertFalse((kwargs.get("out") or self.out).exists())
        return ctx.exception

    def test_roundtrip_fetch_observe_cleanup_and_safe_receipt(self):
        receipt = self.execute()
        self.assertEqual(verify_receipt(self.request, receipt), receipt["receipt_sha256"])
        self.assertEqual(set(receipt["checks"]), CHECKS)
        self.assertEqual(sha256(self.transport.observed.encode()), receipt["observed_marker_sha256"])
        self.assertFalse(self.transport.active)
        self.assertEqual(self.transport.calls[-2][-1], "--cancel")
        self.assertEqual(self.transport.calls[-1], ["status"])
        raw, persisted = read_document(self.out)
        self.assertEqual(persisted, receipt)
        for item in (self.transport.url, self.transport.lease, self.transport.session_id, self.transport.browser_id,
                     "lab-device-not-for-export", "about:blank", "heading", str(self.path),
                     "Cookie", "synthetic-cookie-do-not-export", "session_token", "synthetic-session-do-not-export"):
            self.assertNotIn(item.encode(), raw)

    def test_consent_precedes_any_browser_call_or_state_files(self):
        def declined(request, browser_id):
            self.assertEqual(request, self.request)
            self.assertEqual(browser_id, self.transport.browser_id)
            self.assertEqual(self.transport.calls, [])
            raise HandoffError("operator_declined")
        self.reject("operator_declined", approve=declined)
        self.assertEqual(self.transport.calls, [])
        self.assert_state_untouched()

    def test_non_interactive_execution_is_blocked(self):
        inp = io.StringIO(f"APPROVE {self.request.digest}\n")
        with mock.patch.object(handoff.sys, "stdin", inp):
            err = self.reject("interactive_local_approval_required", approve=handoff.prompt_approval)
        self.assertEqual(err.code, 3)
        self.assertEqual(self.transport.calls, [])

    def test_real_prompt_requires_exact_digest(self):
        for match, text in [(False, "APPROVE another\n"), (True, f"APPROVE {self.request.digest}\n")]:
            with self.subTest(match=match):
                fake_in, fake_err = io.StringIO(text), io.StringIO()
                fake_in.isatty = fake_err.isatty = lambda: True
                with mock.patch.object(handoff.sys, "stdin", fake_in), mock.patch.object(handoff.sys, "stderr", fake_err):
                    if match:
                        handoff.prompt_approval(self.request, self.transport.browser_id)
                    else:
                        with self.assertRaisesRegex(HandoffError, "operator_declined"):
                            handoff.prompt_approval(self.request, self.transport.browser_id)
                self.assertIn(self.request.digest, fake_err.getvalue())
                self.assertIn(handoff.LAB_LABEL, fake_err.getvalue())
                self.assertIn(self.transport.browser_id, fake_err.getvalue())

    def test_valid_but_modified_request_is_rejected_before_prompt_or_browser(self):
        original_digest = self.request.digest
        modified = {**self.request.document(), "expires_at": self.request.expires_at + 1}
        self.assertNotEqual(Request.parse(modified).digest, original_digest)
        req_path = self.path / "request.json"
        req_path.write_text(json.dumps(modified), encoding="utf-8")
        stdout, terminal = io.StringIO(), mock.Mock()
        with contextlib.redirect_stdout(stdout), mock.patch.object(handoff.sys, "stdin", terminal), \
                mock.patch.object(handoff, "_lab_runner", return_value=self.transport):
            self.assertEqual(handoff.main([
                "execute", "--request", str(req_path), "--expected-request-sha256", original_digest,
                "--browser-instance", self.transport.browser_id, "--out", str(self.out),
            ]), 1)
        self.assertEqual(json.loads(stdout.getvalue()), {"status": "REJECTED", "reason": "request_digest_mismatch"})
        terminal.isatty.assert_not_called()
        self.assertEqual(self.transport.calls, [])
        self.assertFalse(self.out.exists())

    def test_post_approval_rehash_rejects_each_changed_snapshot_field(self):
        changes = {
            "expires_at": self.request.expires_at + 1,
            "issued_at": self.now - 1,
            "request_id": "0" * 32,
        }
        self.assertEqual(set(changes), {field.name for field in fields(Request)})
        for field, value in changes.items():
            with self.subTest(field=field):
                self.request = Request.create(300, now=self.now)
                approved_sha = self.request.digest
                modified = Request.parse({**self.request.document(), field: value})
                modified.require_current(self.now)  # rejection must be due to the hash, not expiry or schema
                self.assertNotEqual(modified.digest, approved_sha)

                def approve_then_tamper(request, _browser):
                    self.assertIs(request, self.request)
                    self.assertEqual(request.digest, approved_sha)
                    # Fault injection only: deliberately bypass frozen=True to exercise the second gate.
                    object.__setattr__(request, field, value)

                self.reject("request_digest_mismatch", approve=approve_then_tamper)
                self.assertEqual(self.transport.calls, [])  # not even the read-only version check
                self.assert_state_untouched()

    def test_url_in_file_swapped_at_approval_cannot_change_execution(self):
        req_path, replacement = self.path / "request.json", self.path / "replacement.json"
        unapproved_url = "https://unapproved.invalid/"
        req_path.write_text(json.dumps(self.request.document()), encoding="utf-8")
        replacement.write_text(json.dumps({**self.request.document(), "url": unapproved_url}), encoding="utf-8")
        original_digest = self.request.digest

        def approve_and_swap_file(_limit):
            replacement.replace(req_path)
            return f"APPROVE {original_digest}\n"

        terminal = mock.Mock()
        terminal.isatty.return_value = True
        terminal.readline.side_effect = approve_and_swap_file
        stderr, stdout = io.StringIO(), io.StringIO()
        stderr.isatty = lambda: True
        with (contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr),
              mock.patch.object(handoff.sys, "stdin", terminal),
              mock.patch.object(handoff, "_lab_runner", return_value=self.transport),
              mock.patch.object(handoff.Path, "home", return_value=self.path),
              mock.patch.object(handoff, "read_document", wraps=read_document) as reads):
            self.assertEqual(handoff.main([
                "execute", "--request", str(req_path), "--expected-request-sha256", original_digest,
                "--browser-instance", self.transport.browser_id, "--out", str(self.out),
            ]), 0)
        self.assertEqual(reads.call_args_list, [mock.call(req_path), mock.call(self.out)])
        self.assertEqual(json.loads(req_path.read_text(encoding="utf-8"))["url"], unapproved_url)
        navigated = [args[1] for args in self.transport.calls if args[0] == "navigate"]
        self.assertEqual(len(navigated), 1)
        self.assertEqual(urlsplit(navigated[0]).hostname, handoff.LOOPBACK)
        self.assertNotEqual(navigated[0], unapproved_url)
        receipt = read_document(self.out)[1]
        self.assertEqual(receipt["request_sha256"], original_digest)
        verify_receipt(self.request, receipt)

    def test_expired_before_or_during_approval_never_reaches_bsk(self):
        end = self.request.expires_at
        self.reject("request_outside_approval_window", clock=lambda: end)
        times = iter([self.now, end])
        self.reject("request_outside_approval_window", clock=lambda: next(times))
        self.assertEqual(self.transport.calls, [])

    def test_wrong_profile_mode_version_and_other_sessions_are_blocked(self):
        variants = {
            "local_daemon_required": {"local_auth": None},
            "one_verified_pairing_required": {"local_auth": {"mode": "optional", "paired_devices": 1}},
            "one_lab_browser_required": {"browsers": self.transport.status["browsers"] * 2},
            "dedicated_chrome_lab_required": {"browsers": [{**self.transport.status["browsers"][0], "label": "Other"}]},
            "lab_browser_must_be_idle": {"sessions": [{"session_id": "another-session"}]},
            "pinned_browser_skill_required": {"daemon_version": "0.5.1"},
        }
        for reason, change in variants.items():
            with self.subTest(reason=reason):
                self.transport = FixtureTransport()
                self.transport.status.update(change)
                err = self.reject(reason)
                self.assertEqual(err.code, 3)
                self.assertEqual(self.transport.calls, [["--version"], ["status"]])
                self.assertFalse((self.state / f"used-{self.request.request_id}").exists())

    def test_explicit_popup_id_and_bound_device_must_match(self):
        self.reject("local_popup_instance_id_required", expected="--all")
        self.assertEqual(self.transport.calls, [])
        self.reject("dedicated_chrome_lab_required", expected="00000001")
        self.transport.faults["daemon"] = [{"kind": "local", "status": "active", "browser_id": "00000001"}]
        self.reject("only_the_lab_pairing_allowed")
        self.assertFalse(any(a[0] == "session" for a in self.transport.calls))

    def test_check_local_uses_only_an_explicit_non_default_private_bsk_home(self):
        home, default_home = self.path / "home", self.path / ".bsk"
        for directory in (home, default_home):
            directory.mkdir(mode=0o700)
            if os.name == "nt":
                _set_windows_test_acl(directory)
        for bad in (None, Path("relative"), Path(handoff.__file__).resolve().parent.parent):
            with self.subTest(path=bad), self.assertRaisesRegex(HandoffError, "private_lab_bsk_home_required"):
                handoff._lab_runner(bad)
        with mock.patch.object(handoff.Path, "home", return_value=self.path):
            with self.assertRaisesRegex(HandoffError, "private_lab_bsk_home_required"):
                handoff._lab_runner(default_home)
        stdout = io.StringIO()
        with (contextlib.redirect_stdout(stdout), mock.patch.dict(os.environ, {"BSK_HOME": str(home)}),
              mock.patch.object(handoff.Path, "home", return_value=self.path),
              mock.patch.object(handoff, "SubprocessBskRunner", return_value=self.transport) as runner):
            self.assertEqual(handoff.main(["check-local", "--browser-instance", self.transport.browser_id]), 0)
        runner.assert_called_once_with(bsk_home=home.resolve())
        self.assertEqual(json.loads(stdout.getvalue()), {"status": "READY_LOCAL", "mutations_performed": 0})

    def test_private_directory_probe_is_read_only_and_browser_independent(self):
        home = self.path / "lab"
        home.mkdir(mode=0o700)
        if os.name == "nt":
            _set_windows_test_acl(home)
        self.state.mkdir(mode=0o700, exist_ok=True)
        stdout = io.StringIO()
        with (contextlib.redirect_stdout(stdout), mock.patch.object(Path, "home", return_value=self.path),
              mock.patch.object(handoff, "SubprocessBskRunner", side_effect=AssertionError)):
            self.assertEqual(handoff.main(["check-private-dirs", "--bsk-home", str(home)]), 0)
        self.assertEqual(json.loads(stdout.getvalue()), {"status": "PRIVATE_DIRS_OK"})
        self.assertEqual(list(self.state.iterdir()), [])

    def test_missing_binary_or_extension_is_not_a_pass(self):
        self.transport.faults["--version"] = BskCommandError("bsk_not_installed", "local detail not exported")
        err = self.reject("bsk_not_installed")
        self.assertEqual((err.status, err.code), ("SKIPPED", 2))
        self.transport = FixtureTransport()
        self.transport.status["browsers"] = []
        self.assertEqual(self.reject("no_browser_connected").code, 2)

    def test_replay_is_refused_across_runs_even_with_a_new_output(self):
        self.execute()
        called = self.transport.calls.copy()
        self.reject("request_already_consumed", out=self.path / "other.json")
        self.assertEqual(self.transport.calls, called)
        self.assertFalse((self.state / "active.lock").exists())

    def test_concurrent_approval_of_same_request_runs_only_once(self):
        in_window, finish_navigation = threading.Event(), threading.Event()
        second_out = self.path / "second-receipt.json"
        original_call = self.transport.call

        def hold_first_navigation(args, **kwargs):
            if args[0] == "navigate":
                self.assertTrue(self.transport.active)
                in_window.set()
                self.assertTrue(finish_navigation.wait(5), "concurrent attempt did not finish")
            return original_call(args, **kwargs)

        with (mock.patch.object(self.transport, "call", side_effect=hold_first_navigation),
              ThreadPoolExecutor(max_workers=2) as pool):
            first = pool.submit(self.execute)
            try:
                self.assertTrue(in_window.wait(5), "first attempt did not reach its window")
                calls_so_far = self.transport.calls.copy()
                second = pool.submit(self.execute, out=second_out)
                with self.assertRaisesRegex(HandoffError, "local_lab_lock_exists"):
                    second.result(timeout=5)
                self.assertEqual(self.transport.calls, calls_so_far)
            finally:
                finish_navigation.set()
            receipt = first.result(timeout=5)

        self.assertEqual(sum(args[:2] == ["session", "start"] for args in self.transport.calls), 1)
        self.assertEqual(sum(args[0] == "navigate" for args in self.transport.calls), 1)
        self.assertFalse(second_out.exists())
        verify_receipt(self.request, receipt)
        self.reject("request_already_consumed", out=second_out)

    def test_exclusive_create_race_has_one_winner_across_processes(self):
        context = multiprocessing.get_context("spawn")
        gate = context.Barrier(2)
        path = self.path / "contested.lock"
        workers = [self.start_worker(context, _process_claim, path, gate) for _ in range(2)]
        results = [self.worker_result(worker) for worker in workers]
        self.assertEqual(len({os.getpid(), *(pid for pid, _ in results)}), 3)
        self.assertCountEqual([claim for _, claim in results], ["claimed", "already_exists"])
        self.assertEqual(path.read_bytes(), b"")

    def test_spawned_attempts_and_fresh_process_replay(self):
        context = multiprocessing.get_context("spawn")
        hold, resume = context.Event(), context.Event()
        other = self.path / "other-receipt.json"
        first = self.start_attempt(context, self.out, hold=hold, resume=resume)
        try:
            self.assertTrue(hold.wait(_PROCESS_TIMEOUT), "no first simulated session reached navigation")
            self.assertTrue((self.state / "active.lock").is_file())
            self.assertTrue((self.state / f"used-{self.request.request_id}").is_file())
            second_pid, *second = self.worker_result(self.start_attempt(context, other))
            self.assertEqual(second, ["local_lab_lock_exists", 0, 0])
            self.assertFalse(other.exists())
        finally:
            resume.set()
        first_pid, result, calls, starts = self.worker_result(first)
        self.assertEqual(len({os.getpid(), first_pid, second_pid}), 3)
        self.assertEqual((result, starts), ("verified", 1))
        self.assertGreater(calls, 0)
        verify_receipt(self.request, read_document(self.out)[1])
        self.assertFalse((self.state / "active.lock").exists())
        _, *replay = self.worker_result(self.start_attempt(context, other))
        self.assertEqual(replay, ["request_already_consumed", 0, 0])
        self.assertFalse(other.exists())

    def test_abrupt_exit_retains_reservation_and_replay_is_still_blocked(self):
        context = multiprocessing.get_context("spawn")
        process, _ = self.start_attempt(context, self.out, crash=True)
        process.join(timeout=_PROCESS_TIMEOUT)
        self.assertEqual(process.exitcode, _CRASH_EXIT)
        self.assertFalse(self.out.exists())
        lock = self.state / "active.lock"
        self.assertTrue(lock.is_file())
        self.assertTrue((self.state / f"used-{self.request.request_id}").is_file())
        _, *blocked = self.worker_result(self.start_attempt(context, self.out))
        self.assertEqual(blocked, ["local_lab_lock_exists", 0, 0])
        # Manual recovery in a disposable lab with no real browser. No code
        # should do this automatically: on-device teardown is an owner gate.
        lock.unlink()
        _, *replay = self.worker_result(self.start_attempt(context, self.out))
        self.assertEqual(replay, ["request_already_consumed", 0, 0])
        self.assertFalse(self.out.exists())
        self.assertFalse(lock.exists())
        # A new request can run after the simulated teardown; the old ID stays burned.
        old_id = self.request.request_id
        self.request = Request.create(300, now=self.now)
        _, result, _, starts = self.worker_result(self.start_attempt(context, self.out))
        self.assertEqual((result, starts), ("verified", 1))
        verify_receipt(self.request, read_document(self.out)[1])
        self.assertTrue((self.state / f"used-{old_id}").is_file())

    def test_locked_lab_refuses_new_request(self):
        self.state.mkdir(mode=0o700, exist_ok=True)
        (self.state / "active.lock").touch()
        self.assertEqual(self.reject("local_lab_lock_exists").code, 3)
        self.assertEqual(self.transport.calls, [])

    @unittest.skipUnless(os.name == "posix", "POSIX owner permissions")
    def test_state_directory_refuses_shared_permissions(self):
        self.state.mkdir(mode=0o700)
        self.state.chmod(0o755)
        self.reject("private_replay_directory_required")
        self.assertEqual(self.transport.calls, [])

    def test_wrong_tab_scope_window_or_initial_url_never_navigates(self):
        for changes, reason in [({"scope": "user"}, "expected_one_owned_agent_tab"),
                                ({"window_id": 18}, "expected_one_owned_agent_tab"),
                                ({"url": "https://example.com/"}, "expected_blank_agent_tab")]:
            with self.subTest(changes=changes):
                self.request = Request.create(300, now=self.now)
                self.transport = FixtureTransport()
                self.transport.faults["tab list"] = {"tabs": [
                    {"tab_id": 801, "window_id": 1024, "scope": "agent", "url": "about:blank", **changes},
                ]}
                self.reject(reason)
                self.assertFalse(any(a[0] == "navigate" for a in self.transport.calls))
                self.assertFalse(self.transport.active)

    def test_approval_expiry_after_start_prevents_navigation_but_still_cleans_up(self):
        self.reject("request_outside_approval_window", clock=lambda: self.request.expires_at
                    if self.transport.active else self.now)
        self.assertTrue(any(a[:2] == ["session", "start"] for a in self.transport.calls))
        self.assertFalse(any(a[0] == "navigate" for a in self.transport.calls))
        self.assertFalse(self.transport.active)

    def test_redirect_is_refused_before_page_read(self):
        self.transport.faults["navigate"] = lambda args: {"tab_id": self.transport.tab_id, "url": args[1],
                                                         "final_url": "https://example.com/", "reached": "load"}
        self.reject("fixture_navigation_not_confirmed")
        self.assertFalse(any(a[0] == "observe" for a in self.transport.calls))
        self.assertFalse(self.transport.active)

    def test_positive_navigation_reply_without_http_read_is_not_evidence(self):
        self.transport.faults["navigate"] = lambda args: {"tab_id": self.transport.tab_id, "url": args[1],
                                                         "final_url": args[1], "reached": "load"}
        self.reject("no_fixture_http_observation")
        self.assertFalse(self.transport.active)

    def test_wrong_marker_no_receipt_and_session_closed(self):
        self.transport.faults["observe"] = {"tab_id": self.transport.tab_id, "text": "Not the expected synthetic marker"}
        self.reject("fixture_marker_not_observed")
        self.assertFalse(self.transport.active)

    def test_unknown_navigation_effect_burns_request_and_never_retries(self):
        self.transport.faults["navigate"] = BskCommandError("timeout", "uncertain fixture load")
        err = self.reject("browser_outcome_unknown")
        self.assertEqual((err.status, err.code), ("INCONCLUSIVE", 4))
        self.assertEqual(sum(args[0] == "navigate" for args in self.transport.calls), 1)
        self.assertFalse(self.transport.active)
        self.reject("request_already_consumed")

    def test_lost_start_reply_still_cancels_exact_lease(self):
        def lost_reply(args):
            self.transport.faults.pop("session start")
            self.transport.call(args, timeout=30)  # window actually opened in the simulation
            self.assertTrue(self.transport.active)
            raise BskCommandError("timeout", "lost response")
        self.transport.faults["session start"] = lost_reply
        self.reject("browser_outcome_unknown")
        self.assertIn(["session", "request", self.transport.lease, "--cancel"], self.transport.calls)
        self.assertFalse(any(a[0] == "navigate" for a in self.transport.calls))
        self.assertFalse(self.transport.active)

    def test_unconfirmed_cleanup_never_writes_receipt_and_retains_lock(self):
        for state in ("cleanup_failed", "closed"):
            with self.subTest(state=state):
                self.request = Request.create(300, now=self.now)
                self.transport = FixtureTransport()
                # Even a closed reply is insufficient while the live session table remains occupied.
                self.transport.faults["--cancel"] = lambda args: {"request_id": args[2], "state": state, "session": None}
                self.assertEqual(self.reject("cleanup_not_confirmed").code, 4)
                lock = self.state / "active.lock"
                self.assertTrue(lock.exists())
                self.reject("local_lab_lock_exists")
                lock.unlink()

    def test_reconnect_or_daemon_restart_cannot_be_used_to_confirm_cleanup(self):
        for restart_daemon in (True, False):
            with self.subTest(restart_daemon=restart_daemon):
                self.request = Request.create(300, now=self.now)
                self.transport = FixtureTransport()
                def disappeared(args):
                    self.transport.faults.pop("--cancel")
                    reply = self.transport.call(args, timeout=20)
                    if restart_daemon:
                        self.transport.status["pid"] += 1
                    else:
                        self.transport.status["browsers"][0]["connected_at_ms"] += 1
                    return reply
                self.transport.faults["--cancel"] = disappeared
                self.assertEqual(self.reject("cleanup_not_confirmed").code, 4)
                (self.state / "active.lock").unlink()

    def test_fixture_exposes_no_files_or_unsafe_methods(self):
        with handoff._fixture(self.request) as fixture:
            url = urlsplit(fixture.url)
            self.assertEqual(url.hostname, handoff.LOOPBACK)
            for method, path, headers, status in [
                ("GET", "/", {}, 404), ("GET", url.path, {"Host": "other.invalid"}, 404),
                ("POST", url.path, {}, 501), ("GET", f"{url.path}?extra=yes", {}, 404),
            ]:
                with self.subTest(method=method, path=path, headers=headers):
                    conn = http.client.HTTPConnection(url.hostname, url.port, timeout=3)
                    try:
                        conn.request(method, path, headers=headers)
                        self.assertEqual(conn.getresponse().status, status)
                    finally:
                        conn.close()
            self.assertFalse(fixture.served.is_set())

    def test_receipt_checks_binding_digest_and_historical_window(self):
        receipt = self.execute()
        archived_now = self.request.expires_at + 3600
        verify_receipt(self.request, receipt, now=archived_now)
        with self.assertRaisesRegex(HandoffError, "request_receipt_mismatch"):
            verify_receipt(Request.create(300, now=self.now), receipt)
        for changes, reason in [
            ({"raw": "unreviewed"}, "unsupported_receipt"),
            ({"checks": {**receipt["checks"], "fixture_served": 1}}, "receipt_checks_incomplete"),
            ({"observed_marker_sha256": "0" * 64}, "marker_mismatch"),
            ({"completed_at": self.request.expires_at}, "receipt_outside_request_window"),
            ({"completed_at": self.now + 1}, "receipt_digest_mismatch"),
        ]:
            with self.subTest(reason=reason), self.assertRaisesRegex(HandoffError, reason):
                verify_receipt(self.request, {**receipt, **changes}, now=archived_now)

    def test_recomputed_hash_is_not_a_signature(self):
        receipt = self.execute()
        # A party with the receipt can recompute its public checksum. Verification
        # without an independent trust anchor must never claim owner provenance.
        edited = {**receipt, "completed_at": self.now + 1}
        edited["receipt_sha256"] = sha256(canonical({k: v for k, v in edited.items() if k != "receipt_sha256"}))
        self.assertEqual(verify_receipt(self.request, edited, now=self.now + 1), edited["receipt_sha256"])

    def test_prepare_and_verify_cli_are_browser_independent(self):
        req_path = self.path / "request.json"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), mock.patch.object(handoff, "SubprocessBskRunner", side_effect=AssertionError):
            self.assertEqual(handoff.main(["prepare", "--out", str(req_path)]), 0)
            prepared = json.loads(stdout.getvalue())
            self.request = Request.parse(read_document(req_path)[1])
            self.assertEqual(prepared["request_sha256"], self.request.digest)
            self.execute()
            stdout.seek(0)
            stdout.truncate()
            self.assertEqual(handoff.main(["verify", "--request", str(req_path), "--receipt", str(self.out)]), 0)
            verified = json.loads(stdout.getvalue())
        self.assertEqual(verified["status"], "RECEIPT_CONSISTENT")
        self.assertIs(verified["live_browser_verified_by_receiver"], False)

    def test_untrusted_documents_cannot_request_arbitrary_actions(self):
        for updates in ({"url": "https://example.com/"}, {"incognito": False}, {"browser_instance": "00000000"},
                        {"schema": "unapproved-schema"}, {"task": "other"}, {"request_id": "unknown-id"},
                        {"expires_at": self.now + 3601}, {"issued_at": True}):
            with self.subTest(updates=updates), self.assertRaises(HandoffError):
                Request.parse({**self.request.document(), **updates})
        draft = self.path / "duplicate.json"
        draft.write_text('{"task":"one","task":"two"}', encoding="utf-8")
        with self.assertRaisesRegex(HandoffError, "duplicate_json_key"):
            read_document(draft)
        draft.write_bytes(b" " * (MAX_JSON_BYTES + 1))
        with self.assertRaisesRegex(HandoffError, "document_too_large"):
            read_document(draft)

    @unittest.skipUnless(shutil.which("ssh-keygen"), "optional OpenSSH assertion")
    def test_optional_signature_authenticates_exact_bytes_with_external_trust(self):
        self.execute()
        raw = self.out.read_bytes()
        key = self.path / "ephemeral-signing-key"
        trust = self.path / "allowed-signers"
        tool = shutil.which("ssh-keygen")
        subprocess.run([tool, "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True, capture_output=True)
        public_key = key.with_suffix(".pub").read_text(encoding="utf-8")
        trust.write_text(f"{SIGN_PRINCIPAL} {public_key}", encoding="utf-8")
        subprocess.run([tool, "-Y", "sign", "-f", str(key), "-n", SIGN_NAMESPACE, str(self.out)],
                       check=True, capture_output=True)
        signature = self.out.with_suffix(".json.sig")
        verify_signature(raw, signature, trust)
        with self.assertRaisesRegex(HandoffError, "untrusted_or_invalid_signature"):
            verify_signature(raw + b"\n", signature, trust)
        trust.write_text(f"another-principal {public_key}", encoding="utf-8")
        with self.assertRaisesRegex(HandoffError, "untrusted_or_invalid_signature"):
            verify_signature(raw, signature, trust)


class WindowsAclPolicyTest(unittest.TestCase):
    user = bytes.fromhex("01 01 00 00 00 00 00 05 10 04 00 00")
    everyone = bytes.fromhex("01 01 00 00 00 00 00 01 00 00 00 00")

    def descriptor(self, aces=(), *, owner=None, control=0x8004, null=False):
        owner = self.user if owner is None else owner
        entries = b"".join(struct.pack("<BBHI", kind, flags, 8 + len(sid), 0x1f01ff) + sid
                           for kind, flags, sid in aces)
        dacl = struct.pack("<BBHHH", 2, 0, 8 + len(entries), len(aces), 0) + entries
        header = struct.pack("<BBHIIII", 1, 0, control, 20, 0, 0, 0 if null else 20 + len(owner))
        return header + owner + (b"" if null else dacl)

    def test_dacl_accepts_only_trusted_grants_and_current_user_owner(self):
        for sid in (self.user, windows_acl._SYSTEM, windows_acl._ADMINISTRATORS):
            with self.subTest(sid=sid):
                descriptor = self.descriptor([(1, 0, self.everyone), (0, 0x10, sid)])
                self.assertTrue(windows_acl._private_descriptor(descriptor, self.user, require_user_owner=True))
        system_owned = self.descriptor(owner=windows_acl._SYSTEM)
        self.assertTrue(windows_acl._private_descriptor(system_owned, self.user, require_user_owner=False))
        self.assertFalse(windows_acl._private_descriptor(system_owned, self.user, require_user_owner=True))

    def test_dacl_fails_closed_on_untrusted_inherited_or_unreadable_acl(self):
        for descriptor in (
            self.descriptor([(0, 0, self.everyone)]),
            self.descriptor([(0, 0x10, self.everyone)]),  # inherited
            self.descriptor([(0, 0x0b, self.everyone)]),  # inherit-only (applies to children)
            self.descriptor([(9, 0, self.user)]),  # unsupported callback ACE
            self.descriptor(owner=self.everyone),
            self.descriptor(null=True),
            self.descriptor(control=0x8000),
            self.descriptor([(0, 0, self.user)])[:-1],
        ):
            with self.subTest(descriptor=descriptor.hex()):
                self.assertFalse(windows_acl._private_descriptor(descriptor, self.user, require_user_owner=True))

    def test_remote_and_out_of_profile_paths_are_rejected_before_windows_io(self):
        profile = PureWindowsPath(r"C:\Users\Lab")
        with (mock.patch.object(Path, "home", return_value=profile),
              mock.patch.object(windows_acl, "_WindowsSecurity") as native):
            for path in (r"\\server\share\lab", r"D:\lab", r"C:\Users\Lab\..\lab", r"C:\Users\Lab\file:stream"):
                with self.subTest(path=path):
                    self.assertFalse(windows_acl.is_private_windows_dir(PureWindowsPath(path)))
        native.assert_not_called()


@unittest.skipUnless(os.name == "nt", "requires native Windows NTFS ACLs")
class WindowsAclTest(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory(dir=Path.home())
        self.addCleanup(work.cleanup)
        self.path = Path(work.name)
        _set_windows_test_acl(self.path)
        self.home = self.path / "lab"
        self.state = self.path / handoff.LOCAL_STATE
        for directory in (self.home, self.state):
            directory.mkdir()
            _set_windows_test_acl(directory)

    def run_gate(self, command, *, code, expected):
        args = [command, "--bsk-home", str(self.home)]
        if command == "check-local":
            args.extend(["--browser-instance", FixtureTransport.browser_id])
        stdout = io.StringIO()
        with (mock.patch.object(Path, "home", return_value=self.path), contextlib.redirect_stdout(stdout),
              mock.patch.object(handoff, "SubprocessBskRunner", side_effect=AssertionError) as browser):
            self.assertEqual(handoff.main(args), code)
        browser.assert_not_called()
        self.assertEqual(json.loads(stdout.getvalue()), expected)

    def test_private_directory_passes_and_world_grant_blocks_browser(self):
        self.run_gate("check-private-dirs", code=0, expected={"status": "PRIVATE_DIRS_OK"})
        _set_windows_test_acl(self.home, grant_everyone=True)
        self.run_gate("check-local", code=3,
                      expected={"status": "BLOCKED", "reason": "private_lab_bsk_home_required"})

    def test_private_parent_does_not_hide_an_exposed_replay_file(self):
        now = int(time.time())
        request = Request.create(60, now=now)
        used = self.state / f"used-{request.request_id}"
        used.touch()
        _set_windows_test_acl(used, grant_everyone=True)
        self.run_gate("check-local", code=3,
                      expected={"status": "BLOCKED", "reason": "private_replay_directory_required"})
        with self.assertRaisesRegex(HandoffError, "private_replay_directory_required"):
            with handoff._exclusive_lab(self.state):
                self.fail("blocked replay directory acquired a lock")
        self.assertTrue(used.is_file())
        self.assertFalse((self.state / "active.lock").exists())

    def test_unreadable_acl_cannot_be_accepted(self):
        with mock.patch.object(windows_acl._WindowsSecurity, "descriptor", side_effect=OSError):
            self.run_gate("check-private-dirs", code=3,
                          expected={"status": "BLOCKED", "reason": "private_lab_bsk_home_required"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
