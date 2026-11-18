#!/usr/bin/env python3
"""Prepare, approve locally, and verify one fixed BrowserSkill handoff (Stage 1).

A request is data, not a command. Only the owner can approve execution in a
local terminal. No service accepts commands from Arena or GitHub.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator

if __package__:
    from .adapter import PINNED_CLI_VERSION, PROTOCOL_VERSION, BskCommandError, DomainPolicy, SubprocessBskRunner
    from .handoff_contract import (DEFAULT_TTL, TASK, HandoffError, Request, build_receipt,
                                   marker, read_document, sha256, verify_receipt, verify_signature)
    from .windows_acl import is_private_windows_dir
else:
    from adapter import PINNED_CLI_VERSION, PROTOCOL_VERSION, BskCommandError, DomainPolicy, SubprocessBskRunner
    from handoff_contract import (DEFAULT_TTL, TASK, HandoffError, Request, build_receipt,
                                  marker, read_document, sha256, verify_receipt, verify_signature)
    from windows_acl import is_private_windows_dir

LAB_LABEL = "arena-handoff-lab"
LOCAL_STATE = ".ai-2027-browser-handoff"
LOOPBACK = "127.0.0.1"
# Leave the recoverable start unclaimed; the upstream reaper still owns the
# lease if the local Python process disappears before its finally block.
LEASE_MS = 180_000


def _now() -> int:
    return int(time.time())


def _blocked(reason: str) -> HandoffError:
    return HandoffError(reason, status="BLOCKED", code=3)


def _inconclusive(reason: str) -> HandoffError:
    return HandoffError(reason, status="INCONCLUSIVE", code=4)


def _rpc(runner: Any, args: list[str], *, timeout: float = 12, expect_json: bool = True) -> Any:
    try:
        return runner.call(args, timeout=timeout, expect_json=expect_json)
    except BskCommandError as exc:
        # Never export raw stdout/stderr, daemon paths, or observed page content.
        if exc.is_unavailable:
            raise HandoffError(exc.code, status="SKIPPED", code=2) from exc
        if exc.code in ("permission_denied", "pairing_required"):
            raise _blocked("local_browser_permission_required") from exc
        if exc.is_uncertain or exc.data.get("reached") == "timeout" or exc.code == "local_transport_error":
            raise _inconclusive("browser_outcome_unknown") from exc
        raise HandoffError("browser_command_failed", status="FAILED") from exc
    except (OSError, ValueError, RecursionError) as exc:
        raise _inconclusive("browser_outcome_unknown") from exc


def _object_rpc(runner: Any, args: list[str], *, timeout: float = 12) -> dict[str, Any]:
    result = _rpc(runner, args, timeout=timeout)
    if not isinstance(result, dict):
        raise HandoffError("invalid_browser_reply", status="FAILED")
    return result


def _safe_id(value: Any) -> bool:
    # IDs returned by the daemon are data, not arbitrary CLI switches.
    return isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", value) is not None


def _positive_id(value: Any) -> bool:
    return type(value) is int and 0 < value < 2**63


@dataclass(frozen=True)
class LabConnection:
    instance_id: str
    daemon_pid: int
    connected_at_ms: int


def _lab_status(runner: Any, expected: str, owned: tuple[str, int] | None = None) -> LabConnection:
    status = _object_rpc(runner, ["status"])
    # v0.5.0 SystemStatusResult uses daemon_version. In server mode its
    # local_auth is null (not a usable locally paired daemon).
    if status.get("daemon_version") != PINNED_CLI_VERSION or status.get("protocol_version") != PROTOCOL_VERSION:
        raise _blocked("pinned_browser_skill_required")
    auth, browsers = status.get("local_auth"), status.get("browsers")
    if not isinstance(auth, dict) or not _positive_id(status.get("pid")):
        raise _blocked("local_daemon_required")
    if browsers == []:
        raise HandoffError("no_browser_connected", status="SKIPPED", code=2)
    if auth.get("mode") != "required" or type(auth.get("paired_devices")) is not int or auth["paired_devices"] != 1:
        raise _blocked("one_verified_pairing_required")
    if not isinstance(browsers, list) or len(browsers) != 1 or not isinstance(browsers[0], dict):
        raise _blocked("one_lab_browser_required")
    browser = browsers[0]
    if browser.get("browser_name") != "chrome" or browser.get("label") != LAB_LABEL or browser.get("instance_id") != expected:
        raise _blocked("dedicated_chrome_lab_required")
    if (browser.get("extension_version") != PINNED_CLI_VERSION
            or browser.get("extension_protocol_version") != PROTOCOL_VERSION
            or browser.get("version_skew", False) is not False
            or browser.get("unresponsive", False) is not False
            or not _positive_id(browser.get("connected_at_ms"))):
        raise _blocked("healthy_pinned_extension_required")
    count, sessions = browser.get("session_count"), status.get("sessions")
    if type(count) is not int or count != int(owned is not None) or not isinstance(sessions, list) or len(sessions) != count:
        raise _blocked("lab_browser_must_be_idle" if owned is None else "session_binding_changed")
    if owned is not None:
        session = sessions[0]
        if (not isinstance(session, dict) or session.get("browser_instance_id") != expected
                or session.get("session_id") != owned[0] or not _positive_id(session.get("agent_window_id"))
                or session["agent_window_id"] != owned[1]):
            raise _blocked("session_binding_changed")
    return LabConnection(expected, status["pid"], browser["connected_at_ms"])


def _require_lab_connection(runner: Any, lab: LabConnection, owned: tuple[str, int] | None = None) -> None:
    # Detect daemon replacement and browser reconnect. These status values
    # are continuity hints, not proofs of the Chrome profile or OS identity.
    if _lab_status(runner, lab.instance_id, owned) != lab:
        raise _blocked("lab_connection_changed")


def check_local(runner: Any, expected: str) -> LabConnection:
    if re.fullmatch(r"[0-9a-f]{8}", expected) is None:
        raise _blocked("local_popup_instance_id_required")
    version = _rpc(runner, ["--version"], timeout=5, expect_json=False)
    if version != f"bsk {PINNED_CLI_VERSION}":
        raise _blocked("pinned_browser_skill_required")
    lab = _lab_status(runner, expected)
    # Status reports just a count. Bind the sole active local device record
    # to the same popup ID; never inspect the pairing store or export devices.
    devices = _rpc(runner, ["daemon", "devices"])
    if not isinstance(devices, list) or len(devices) != 1 or not isinstance(devices[0], dict):
        raise _blocked("only_the_lab_pairing_allowed")
    device = devices[0]
    if device.get("kind") != "local" or device.get("status") != "active" or device.get("browser_id") != expected:
        raise _blocked("only_the_lab_pairing_allowed")
    return lab


def prompt_approval(request: Request, browser_id: str) -> None:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise _blocked("interactive_local_approval_required")
    phrase = f"APPROVE {request.digest}"
    print(
        f"Request {request.request_id}, expires {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime(request.expires_at))}.\n"
        f"In the paired {LAB_LABEL!r} Chrome instance {browser_id}: open ONE incognito Agent Window,\n"
        "navigate ONCE to this process's synthetic loopback page, observe its marker, then close the session.\n"
        "No account pages, borrowed tabs, downloads, or uploads.\n"
        "The request digest matches the separately supplied SHA-256. Proceed only in a fresh, signed-out\n"
        "lab profile without another incognito window. Do not interact with the Agent Window during the task.\n"
        f"Type exactly {phrase} to execute once, or press Enter to decline:\n> ",
        end="", file=sys.stderr, flush=True,
    )
    if sys.stdin.readline(len(phrase) + 3).rstrip("\r\n") != phrase:
        raise HandoffError("operator_declined")


def _write_new(path: Path, raw: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)


def _write_document(path: Path, payload: dict[str, Any]) -> None:
    try:
        _write_new(path, (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("ascii"))
    except FileExistsError as exc:
        raise HandoffError("output_already_exists") from exc
    except OSError as exc:
        raise _blocked("output_not_writable") from exc


def _private_dir(path: Path, reason: str) -> None:
    try:
        if os.name == "nt":
            if is_private_windows_dir(path):
                return
        elif os.name == "posix":
            meta = path.lstat()
            if stat.S_ISDIR(meta.st_mode) and meta.st_uid == os.getuid() and not meta.st_mode & 0o077:
                return
    except OSError as exc:
        raise _blocked(reason) from exc
    raise _blocked(reason)


def _checked_lab_home(path: Path | None) -> Path:
    reason = "private_lab_bsk_home_required"
    if path is None or not path.is_absolute():
        raise _blocked(reason)
    _private_dir(path, reason)
    try:
        home = path.resolve(strict=True)
        if home == (Path.home() / ".bsk").resolve() or home.is_relative_to(Path(__file__).resolve().parent.parent):
            raise _blocked(reason)
    except (OSError, RuntimeError) as exc:
        raise _blocked(reason) from exc
    return home


def _lab_runner(path: Path | None) -> SubprocessBskRunner:
    # Pass the validated directory explicitly, overriding any ambient BSK_HOME.
    # Never fall back to the default (possibly personal) daemon.
    return SubprocessBskRunner(bsk_home=_checked_lab_home(path))


@contextmanager
def _exclusive_lab(state: Path) -> Iterator[None]:
    try:
        # Windows Python 3.10–3.12 mkdir(0o700) does not set a private DACL.
        # The owner must provision the directory before any attempt.
        if os.name == "posix":
            state.mkdir(mode=0o700, exist_ok=True)
        _private_dir(state, "private_replay_directory_required")
        lock = state / "active.lock"
        _write_new(lock, b"")
    except FileExistsError as exc:
        raise _blocked("local_lab_lock_exists") from exc
    except OSError as exc:
        raise _blocked("local_replay_state_unavailable") from exc
    retain = False
    try:
        yield
    except HandoffError as exc:
        # A later request must not start while this one may still own a window.
        retain = exc.reason == "cleanup_not_confirmed"
        raise
    finally:
        if not retain:
            lock.unlink()


@dataclass(frozen=True)
class Fixture:
    url: str
    nonce: str
    marker: str
    served: threading.Event


@contextmanager
def _fixture(request: Request) -> Iterator[Fixture]:
    """One in-memory GET route on IPv4 loopback. No files, writes or redirects."""
    nonce = uuid.uuid4().hex
    expected = marker(request, nonce)
    path = f"/browser-handoff/{uuid.uuid4().hex}"
    body = (f"<!doctype html><html lang=en><title>AI-2027 local browser handoff</title>"
            f"<main><h1>{expected}</h1></main></html>").encode("ascii")
    served = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(3)

        def log_message(self, *args: Any) -> None:
            pass  # no user agents, request headers, URLs or IPs in any report

        def do_GET(self) -> None:
            if self.path != path or self.headers.get_all("Host") != [authority]:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            served.set()

    class FixtureServer(ThreadingHTTPServer):
        def handle_error(self, request: Any, client_address: Any) -> None:
            pass  # a failed send does not set the event or become a receipt

    with FixtureServer((LOOPBACK, 0), Handler) as server:
        authority = f"{LOOPBACK}:{server.server_port}"
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            yield Fixture(f"http://{authority}{path}", nonce, expected, served)
        finally:
            server.shutdown()
            thread.join()


def _agent_tab(runner: Any, session_id: str, window_id: int) -> dict[str, Any]:
    tabs = _object_rpc(runner, ["tab", "list", "--session", session_id, "--scope", "agent"]).get("tabs")
    if not isinstance(tabs, list) or len(tabs) != 1 or not isinstance(tabs[0], dict):
        raise _blocked("expected_one_owned_agent_tab")
    tab = tabs[0]
    if (tab.get("scope") != "agent" or not _positive_id(tab.get("tab_id"))
            or not _positive_id(tab.get("window_id")) or tab["window_id"] != window_id):
        raise _blocked("expected_one_owned_agent_tab")
    return tab


def _close_request(runner: Any, lease: str, lab: LabConnection) -> None:
    try:
        closed = _object_rpc(runner, ["session", "request", lease, "--cancel"], timeout=20)
        if (closed.get("request_id") != lease or closed.get("state") != "closed"
                or "session" not in closed or closed["session"] is not None or closed.get("cleanup_error") is not None):
            raise _inconclusive("cleanup_not_confirmed")
        # An empty session table on a different daemon or reconnected browser
        # is not evidence that this request's window was torn down.
        _require_lab_connection(runner, lab)
    except (HandoffError, KeyboardInterrupt) as exc:
        raise _inconclusive("cleanup_not_confirmed") from exc


def _observe_fixture(runner: Any, request: Request, lab: LabConnection, fixture: Fixture,
                     clock: Callable[[], int]) -> str:
    # The target is built in the same process, never received from a plan, an
    # observation or a redirect. This exception allows only our loopback host.
    DomainPolicy(allow_localhost=True, allowed_hosts=frozenset({LOOPBACK})).check_url(fixture.url)
    request.require_current(clock())
    lease = f"{time.time_ns() // 1_000_000 + LEASE_MS}:{uuid.uuid4()}"
    try:
        prepared = _object_rpc(runner, ["session", "request", lease, "--prepare"])
        if (prepared.get("request_id") != lease or prepared.get("state") != "prepared"
                or "session" not in prepared or prepared["session"] is not None or prepared.get("cleanup_error") is not None):
            raise _inconclusive("session_admission_not_confirmed")
        _require_lab_connection(runner, lab)
        request.require_current(clock())
        started = _object_rpc(runner, [
            "session", "start", "--request-id", lease, "--browser", lab.instance_id,
            "--incognito", "--no-focus", "--name", "AI-2027 local handoff",
        ], timeout=30)
        session_id, window_id = started.get("session_id"), started.get("agent_window_id")
        if not _safe_id(session_id) or not _positive_id(window_id) or started.get("browser_instance_id") != lab.instance_id:
            raise _inconclusive("isolated_window_not_confirmed")
        owned = (session_id, window_id)
        _require_lab_connection(runner, lab, owned)
        initial_tab = _agent_tab(runner, session_id, window_id)
        if initial_tab.get("url") != "about:blank":
            raise _blocked("expected_blank_agent_tab")
        tab_id = initial_tab["tab_id"]
        request.require_current(clock())
        nav = _object_rpc(runner, [
            "navigate", fixture.url, "--session", session_id, "--tab-id", str(tab_id),
            "--wait-until", "load", "--timeout", "10s",
        ], timeout=16)
        if (not _positive_id(nav.get("tab_id")) or nav["tab_id"] != tab_id
                or nav.get("reached") != "load" or nav.get("url") != fixture.url or nav.get("final_url") != fixture.url
                or nav.get("error_text") or nav.get("dialogs") or nav.get("effect_state") == "unknown"):
            raise _inconclusive("fixture_navigation_not_confirmed")
        if not fixture.served.wait(timeout=1):
            raise _inconclusive("no_fixture_http_observation")
        _require_lab_connection(runner, lab, owned)
        tab = _agent_tab(runner, session_id, window_id)
        if tab["tab_id"] != tab_id or tab.get("url") != fixture.url:
            raise _inconclusive("fixture_tab_changed")
        request.require_current(clock())
        obs = _object_rpc(runner, ["observe", "--session", session_id, "--tab-id", str(tab_id),
                                   "--max-tokens", "256"])
        text = obs.get("text")
        if (not _positive_id(obs.get("tab_id")) or obs["tab_id"] != tab_id or obs.get("dialogs")
                or obs.get("truncated") or not isinstance(text, str)
                or re.search(rf"(?<![\w-]){re.escape(fixture.marker)}(?![\w-])", text) is None):
            raise HandoffError("fixture_marker_not_observed", status="FAILED")
        # Export only a match to a value we generated, not a substring from
        # arbitrary page text, URL, session ID, instance ID, or CLI output.
        return sha256(fixture.marker.encode("ascii"))
    finally:
        _close_request(runner, lease, lab)


def execute(request: Request, out: Path, *, state: Path, runner: Any, expected: str,
            expected_request_sha256: str, approve: Callable[[Request, str], None] = prompt_approval,
            clock: Callable[[], int] = _now) -> dict[str, Any]:
    request.require_digest(expected_request_sha256)
    request.require_current(clock())
    if out.exists() or out.is_symlink():
        raise HandoffError("output_already_exists")
    if not out.parent.is_dir():
        raise _blocked("output_directory_required")
    # Check even for non-CLI callers before displaying user-supplied text.
    if re.fullmatch(r"[0-9a-f]{8}", expected) is None:
        raise _blocked("local_popup_instance_id_required")
    approve(request, expected)
    # Re-hash the exact approved snapshot before any bsk call (even preflight).
    # The downloaded file is not consulted after approval.
    request.require_digest(expected_request_sha256)
    request.require_current(clock())
    with _exclusive_lab(state):
        used = state / f"used-{request.request_id}"
        if used.exists() or used.is_symlink():
            raise HandoffError("request_already_consumed")
        lab = check_local(runner, expected)
        started_at = clock()
        request.require_current(started_at)
        # Atomic burn before any start; a failed or lost response is NOT a retry.
        try:
            _write_new(used, b"")
        except FileExistsError as exc:
            raise HandoffError("request_already_consumed") from exc
        except OSError as exc:
            raise _blocked("local_replay_state_unavailable") from exc
        try:
            with _fixture(request) as fixture:
                observed_hash = _observe_fixture(runner, request, lab, fixture, clock)
        except OSError as exc:
            raise _blocked("local_fixture_unavailable") from exc
        finished_at = clock()
        receipt = build_receipt(request, fixture.nonce, observed_hash, started_at, finished_at)
        verify_receipt(request, receipt, now=finished_at)
        _write_document(out, receipt)
        verify_receipt(request, read_document(out)[1], now=finished_at)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="make a bounded request without using a browser")
    prepare.add_argument("--out", required=True, type=Path)
    prepare.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL)
    folders = commands.add_parser("check-private-dirs", help="read-only directory check before provisioning the lab")
    check = commands.add_parser("check-local", help="check lab readiness (read-only, no approvals or window)")
    run = commands.add_parser("execute", help="requires the owner's interactive local approval")
    run.add_argument("--request", required=True, type=Path)
    run.add_argument("--expected-request-sha256", required=True,
                     help="original request_sha256 received separately from Arena")
    run.add_argument("--out", required=True, type=Path)
    for local in (folders, check, run):
        local.add_argument("--bsk-home", type=Path, default=os.environ.get("BSK_HOME") or None,
                           help="absolute path to the private lab daemon's BSK_HOME (defaults to env var, never ~/.bsk)")
    for local in (check, run):
        local.add_argument("--browser-instance", required=True, help="Instance ID from the verified lab Chrome popup")
    verify = commands.add_parser("verify", help="check a receipt against the original request, not a live browser")
    verify.add_argument("--request", required=True, type=Path)
    verify.add_argument("--receipt", required=True, type=Path)
    verify.add_argument("--signature", type=Path)
    verify.add_argument("--trusted-signers", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            request = Request.create(args.ttl_seconds)
            _write_document(args.out, request.document())
            result = {"status": "PREPARED", "task": TASK, "request_id": request.request_id,
                      "request_sha256": request.digest, "expires_at": request.expires_at}
        elif args.command == "check-private-dirs":
            _checked_lab_home(args.bsk_home)
            _private_dir(Path.home() / LOCAL_STATE, "private_replay_directory_required")
            result = {"status": "PRIVATE_DIRS_OK"}
        elif args.command == "check-local":
            if os.name == "nt":
                _private_dir(Path.home() / LOCAL_STATE, "private_replay_directory_required")
            check_local(_lab_runner(args.bsk_home), args.browser_instance)
            result = {"status": "READY_LOCAL", "mutations_performed": 0}
        else:
            request = Request.parse(read_document(args.request)[1])
            if args.command == "execute":
                receipt = execute(request, args.out, state=Path.home() / LOCAL_STATE,
                                  runner=_lab_runner(args.bsk_home), expected=args.browser_instance,
                                  expected_request_sha256=args.expected_request_sha256)
                result = {"status": "VERIFIED_LOCAL", "request_id": request.request_id,
                          "receipt_sha256": receipt["receipt_sha256"]}
            else:
                if (args.signature is None) != (args.trusted_signers is None):
                    raise HandoffError("signature_and_trust_file_required_together")
                raw, receipt = read_document(args.receipt)
                digest = verify_receipt(request, receipt)
                if args.signature is not None:
                    verify_signature(raw, args.signature, args.trusted_signers)
                result = {"status": "OWNER_ATTESTED" if args.signature else "RECEIPT_CONSISTENT",
                          "request_id": request.request_id, "receipt_sha256": digest,
                          "live_browser_verified_by_receiver": False}
        print(json.dumps(result, sort_keys=True))
        return 0
    except KeyboardInterrupt:
        print(json.dumps({"status": "INCONCLUSIVE", "reason": "operator_interrupted"}))
        return 4
    except HandoffError as exc:
        print(json.dumps({"status": exc.status, "reason": exc.reason}))
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
