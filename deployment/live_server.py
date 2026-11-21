#!/usr/bin/env python3
"""Live server launcher with a hard disk cap — for hosting the demo 24/7.

WHY THIS EXISTS
===============
`live-agent/server.py` is a *local* demo and must not be exposed directly. The
launcher adds a bounded deployment boundary; its security guarantees are
intentionally limited and measured, not implied:

  * **Writes fail closed.** `POST /api/run` is disabled unless a strong
    `AI2027_WRITE_TOKEN` is configured; when enabled, every request needs its
    bearer token. Do not put that shared token in public HTML or JavaScript.
  * **A process-local rate limit** applies to write attempts by the peer IP seen
    by Python. Forwarded-IP headers are ignored because clients can spoof them.
  * **Private API routes fail closed.** Only `GET /api/health` is public and
    deliberately minimal. All other `/api/` reads use per-principal token hashes;
    writes require the operator token. This is a bounded interim control, not
    multi-user identity or authorization.
  * **No gateway or edge protection is included.** Use a trusted HTTPS gateway
    for user sessions, authorization, and distributed rate limits before any
    public interactive service.
  * **No disk cap (upstream).** Every run writes 4 files (~69.5 KB); without
    cleanup, ~7,500 requests fill a 512 MB tier in about 1.4 minutes. This
    launcher caps disk use and the number of runs per process.
  * **The kernel lock serialises every run** (`_lock` in `server.py`), so a
    handful of clients can also make the demo unusable for everyone.

The launcher also reads `PORT`, binds `0.0.0.0` on a PaaS, prunes only validated
run groups under its managed storage directories, and prints an honest startup
banner. A public deployment stays read-only unless its operator deliberately
configures the write token and disables the read-only override.

Usage
-----
    python3 deployment/live_server.py                 # read-only unless a token is configured
    python3 deployment/live_server.py --fresh          # explicit destructive reset
    AI2027_WRITE_TOKEN=<secret> python3 deployment/live_server.py
    AI2027_READ_ONLY=1 python3 deployment/live_server.py

Environment
-----------
    PORT                             listening port (default 8090)
    HOST                             bind address (default 0.0.0.0 in PaaS, else 127.0.0.1)
    AI2027_LIVE_RUNTIME_DIR           runtime root; default keeps the existing live-agent/ paths
    AI2027_MAX_DISK_MB                cap for generated evidence (default 256)
    AI2027_MAX_RUNS                   hard cap on runs per process (default 5000)
    AI2027_WRITE_TOKEN                >=32-character bearer secret; absent means writes disabled
    AI2027_READ_ONLY                  "1" always disables POST /api/run
    AI2027_READ_TOKEN_HASHES          JSON map of reader IDs to SHA-256 hex digests; absent denies private reads
    AI2027_READ_RATE_LIMIT             private read attempts per peer IP/window (default 120)
    AI2027_READ_RATE_WINDOW_SECONDS    read rate-limit window (default 60; max 86400)
    AI2027_WRITE_RATE_LIMIT            write attempts per peer IP per window (default 10)
    AI2027_WRITE_RATE_WINDOW_SECONDS   write rate-limit window (default 60; max 86400)

Runtime files are preserved across process restarts on the same filesystem.
Survival across instance replacement/redeploy depends on a persistent mount;
this launcher and `render.yaml` do not provision one.

Stdlib only — this file adds no dependency to a zero-dependency project.
"""
from __future__ import annotations

import argparse
import hmac
import importlib.util
import json
import math
import os
import re
import shutil
import stat
import sys
import threading
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
SERVER_PY = ROOT / "live-agent" / "server.py"
_MODULE = "ai2027_live_agent_server"
_RUN_ID = re.compile(r"run_\d+_[0-9a-f]{8}\Z")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


class SlidingWindowRateLimiter:
    """Bound write attempts per observed peer IP in a process-local time window."""

    def __init__(self, max_requests: int, window_seconds: int, max_clients: int = 4096):
        if max_requests < 1 or window_seconds < 1 or max_clients < 1:
            raise ValueError("rate limiter bounds must be positive")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.max_clients = max_clients
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _expire(events: deque[float], cutoff: float) -> None:
        while events and events[0] <= cutoff:
            events.popleft()

    def allow(self, client: str, now: float | None = None) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)``; ``now`` enables deterministic tests."""
        now = time.monotonic() if now is None else now
        cutoff = now - self.window_seconds
        with self._lock:
            events = self._events.get(client)
            if events is not None:
                self._expire(events, cutoff)
                if not events:
                    self._events.pop(client, None)
                    events = None

            if events is None:
                if len(self._events) >= self.max_clients:
                    # Expire inactive peers before failing closed at the bounded table size.
                    for peer, peer_events in list(self._events.items()):
                        self._expire(peer_events, cutoff)
                        if not peer_events:
                            self._events.pop(peer, None)
                    if len(self._events) >= self.max_clients:
                        return False, max(1, math.ceil(self.window_seconds))
                events = deque()
                self._events[client] = events

            if len(events) >= self.max_requests:
                retry_after = math.ceil(events[0] + self.window_seconds - now)
                return False, max(1, retry_after)
            events.append(now)
            return True, 0


def _bearer_token_matches(header: str, expected: str) -> bool:
    scheme, separator, supplied = header.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not supplied or any(
        char.isspace() for char in supplied
    ):
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def _respond_json(handler, body: dict, status: int, headers: dict[str, str] | None = None) -> None:
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    for name, value in (headers or {}).items():
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(raw)


def _cleanup_report(prune_result: dict) -> dict:
    """Return a stable, path-free cleanup outcome suitable for API clients."""
    raw_failures = prune_result.get("failures", [])
    failures = [str(item) for item in raw_failures] if isinstance(raw_failures, (list, tuple)) else []
    warnings: set[str] = set()
    if raw_failures and not failures:
        warnings.add("CLEANUP_FAILED")
    failure_map = (
        ("unsafe-storage-layout", "UNSAFE_STORAGE_LAYOUT"),
        ("disk-usage-scan-failed", "DISK_USAGE_SCAN_FAILED"),
        ("candidate-scan-failed", "CLEANUP_CANDIDATE_SCAN_FAILED"),
        (":outside-storage", "CLEANUP_PATH_REJECTED"),
        (":unexpected-file-type", "CLEANUP_PATH_REJECTED"),
        (":stat-failed", "CLEANUP_METADATA_CHECK_FAILED"),
        (":invalid-json", "RUN_RECORD_VALIDATION_FAILED"),
        (":id-mismatch", "RUN_RECORD_VALIDATION_FAILED"),
        ("ledger:invalid-or-mismatched", "LEDGER_VALIDATION_FAILED"),
        (":safe-recursive-delete-unavailable", "SAFE_DELETE_UNAVAILABLE"),
        (":record:delete-failed", "RUN_RECORD_CLEANUP_FAILED"),
        (":sandbox:delete-failed", "SANDBOX_CLEANUP_FAILED"),
        (":ledger:delete-failed", "LEDGER_CLEANUP_FAILED"),
        ("disk-cap-unmet", "DISK_CAP_UNMET"),
    )
    for failure in failures:
        mapped = next((code for marker, code in failure_map if marker in failure), None)
        warnings.add(mapped or "CLEANUP_FAILED")

    partial_runs = prune_result.get("partial_runs")
    if not isinstance(partial_runs, (list, tuple, set)):
        partial_runs = []
    partial_count = len(partial_runs)
    removed = prune_result.get("removed", 0)
    freed = prune_result.get("freed_bytes", 0)
    removed = removed if isinstance(removed, int) and not isinstance(removed, bool) and removed >= 0 else 0
    freed = freed if isinstance(freed, int) and not isinstance(freed, bool) and freed >= 0 else 0
    if partial_count:
        warnings.add("CLEANUP_PARTIAL")
    if not warnings:
        status = "complete"
    elif partial_runs or removed > 0:
        status = "partial"
    else:
        status = "failed"
    return {
        "status": status,
        "completed": True,
        "warnings": sorted(warnings),
        "removed_runs": removed,
        "partial_run_count": partial_count,
        "freed_bytes": freed,
    }


def _attach_cleanup_result(server, payload: dict, prune_result: dict) -> tuple[dict, bool]:
    """Attach and persist the safe cleanup report; return (report, persist_failed)."""
    cleanup = _cleanup_report(prune_result)
    payload["cleanup"] = cleanup
    persist_failed = False
    try:
        server._persist_run_record(payload)
    except Exception:
        cleanup["warnings"] = sorted(set(cleanup["warnings"] + [
            "CLEANUP_RESULT_PERSIST_FAILED"
        ]))
        if cleanup["status"] == "complete":
            cleanup["status"] = "partial"
        persist_failed = True

    run_index = getattr(server, "_run_index", None)
    run_id = payload.get("run_id")
    if isinstance(run_index, dict) and isinstance(run_id, str):
        entry = run_index.get(run_id)
        if isinstance(entry, dict):
            entry["cleanup"] = {"status": cleanup["status"]}
    return cleanup, persist_failed


def _human(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{n / 1024:.0f} KB"


def _dir_bytes(*paths: Path, strict: bool = False) -> int:
    """Count regular files and symlink entries without following links.

    Normal shutdown reporting is best-effort. Pruning uses ``strict=True`` so
    an unreadable subtree aborts cleanup instead of making the cap look met.
    """
    total = 0

    def on_walk_error(error: OSError) -> None:
        if strict:
            raise error

    for root in paths:
        try:
            root_stat = root.lstat()
        except OSError:
            if strict:
                raise
            continue
        if stat.S_ISLNK(root_stat.st_mode) or stat.S_ISREG(root_stat.st_mode):
            total += root_stat.st_size
            continue
        if not stat.S_ISDIR(root_stat.st_mode):
            continue
        for directory, subdirs, filenames in os.walk(
            root, followlinks=False, onerror=on_walk_error
        ):
            for name in list(subdirs):
                item = Path(directory) / name
                try:
                    item_stat = item.lstat()
                except OSError:
                    if strict:
                        raise
                    subdirs.remove(name)
                    continue
                if stat.S_ISLNK(item_stat.st_mode):
                    total += item_stat.st_size
                    subdirs.remove(name)
                elif not stat.S_ISDIR(item_stat.st_mode):
                    subdirs.remove(name)
            for name in filenames:
                try:
                    item_stat = (Path(directory) / name).lstat()
                except OSError:
                    if strict:
                        raise
                    continue
                if stat.S_ISREG(item_stat.st_mode) or stat.S_ISLNK(item_stat.st_mode):
                    total += item_stat.st_size
    return total


def _storage_layout(server) -> tuple[Path, Path, Path, Path] | None:
    """Return the expected runtime, runs, sandboxes, and ledger paths.

    Pruning is deliberately disabled if a storage root or the ledger directory
    is redirected by a symlink. The runtime root itself may be anywhere; the
    three managed directories must remain its direct, canonical children.
    """
    try:
        runs_dir = Path(server.RUNS_DIR)
        sandboxes_dir = Path(server.SANDBOXES_DIR)
        runtime_root = runs_dir.parent.resolve()
        expected_runs = runtime_root / "runs"
        expected_sandboxes = runtime_root / "sandboxes"
        ledger_dir = runs_dir / "ledgers"
        runs_stat = runs_dir.lstat()
        sandboxes_stat = sandboxes_dir.lstat()
        if not stat.S_ISDIR(runs_stat.st_mode) or not stat.S_ISDIR(sandboxes_stat.st_mode):
            return None
        if ledger_dir.exists() or ledger_dir.is_symlink():
            ledger_stat = ledger_dir.lstat()
            if not stat.S_ISDIR(ledger_stat.st_mode):
                return None
        if (
            runs_dir.name != "runs"
            or sandboxes_dir.name != "sandboxes"
            or runs_dir.resolve() != expected_runs
            or sandboxes_dir.resolve() != expected_sandboxes
            or ledger_dir.resolve() != expected_runs / "ledgers"
        ):
            return None
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    return runtime_root, runs_dir, sandboxes_dir, ledger_dir


def _remove_run_artifact(path: Path, kind: str) -> int:
    """Remove one expected artifact without following symlinks."""
    try:
        metadata = path.lstat()
    except OSError:
        raise
    if stat.S_ISLNK(metadata.st_mode):
        raise OSError("refusing to prune a symlink")
    if kind == "sandbox":
        if not stat.S_ISDIR(metadata.st_mode):
            raise OSError("sandbox path is not a directory")
        if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
            raise OSError("safe recursive deletion is unavailable on this platform")
        size = _dir_bytes(path, strict=True)
        shutil.rmtree(path)
        return size
    if kind not in {"record", "ledger"} or not stat.S_ISREG(metadata.st_mode):
        raise OSError("run evidence path is not a regular file")
    path.unlink()
    return metadata.st_size


def _ledger_matches_run(
    path: Path, run_id: str, public_record: dict | None = None
) -> bool:
    """Verify ledger identity, including records written before IDs were unified.

    Older Live Agent runs used a separate kernel ID inside each ledger while the
    filename and saved record used the public ID. Their final ledger record must
    still exactly match the sibling record (apart from that old ID) before the
    ledger can be treated as belonging to this run.
    """
    events: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as ledger:
            for line in ledger:
                if not line.strip():
                    continue
                event = json.loads(line)
                if not isinstance(event, dict):
                    return False
                events.append(event)
    except (OSError, UnicodeError, ValueError, RuntimeError):
        return False
    if not events:
        return False

    ledger_id = None
    for event in events:
        event_run_id = event.get("run_id")
        if not isinstance(event_run_id, str) or not event_run_id:
            return False
        if ledger_id is None:
            ledger_id = event_run_id
        elif event_run_id != ledger_id:
            return False
    if ledger_id is None:
        return False
    final = events[-1]
    payload = final.get("payload")
    final_record = payload.get("record") if isinstance(payload, dict) else None
    if (
        final.get("type") != "DECISION_RECORDED"
        or not isinstance(final_record, dict)
        or final_record.get("run_id") != ledger_id
    ):
        return False

    if ledger_id != run_id:
        # Compatibility for pre-fix runs: their old kernel IDs were a 12-hex
        # suffix and the persisted record retains every other final-record field.
        if public_record is None or not re.fullmatch(r"run_[0-9a-f]{12}", ledger_id):
            return False
        if any(
            public_record.get(key) != value
            for key, value in final_record.items()
            if key != "run_id"
        ):
            return False
        return public_record.get("status") == payload.get("status")

    if public_record is not None:
        if any(
            public_record.get(key) != value
            for key, value in final_record.items()
            if key != "run_id"
        ):
            return False
        if public_record.get("status") != payload.get("status"):
            return False
    return True


def _forget_run(server, run_id: str) -> None:
    """Keep the in-memory run list consistent with pruned records."""
    run_index = getattr(server, "_run_index", None)
    if isinstance(run_index, dict):
        run_index.pop(run_id, None)


def _prune(server, max_disk_bytes: int, keep_run_id: str | None = None) -> dict:
    """Prune oldest, validated run groups without escaping the runtime root.

    A generated run ID ties its record, sandbox, and append-only ledger together.
    The ledger is removed last: if an earlier deletion fails, its audit evidence
    remains. Any incomplete prune is returned to the caller and logged, never
    counted as a successful prune. ``keep_run_id`` protects the current run.
    """
    removed, freed = 0, 0
    failures: list[str] = []
    partial_runs: list[str] = []
    layout = _storage_layout(server)
    if layout is None:
        return {
            "removed": 0,
            "freed_bytes": 0,
            "failures": ["unsafe-storage-layout"],
            "partial_runs": [],
            "disk_bytes": None,
            "over_cap_bytes": None,
        }
    _, runs_dir, sandboxes_dir, ledger_dir = layout
    try:
        expected_parents = {
            "sandbox": sandboxes_dir.resolve(),
            "record": runs_dir.resolve(),
            "ledger": ledger_dir.resolve(),
        }
        disk_bytes = _dir_bytes(runs_dir, sandboxes_dir, strict=True)
    except (OSError, RuntimeError):
        return {
            "removed": 0,
            "freed_bytes": 0,
            "failures": ["disk-usage-scan-failed"],
            "partial_runs": [],
            "disk_bytes": None,
            "over_cap_bytes": None,
        }
    if disk_bytes <= max_disk_bytes:
        return {
            "removed": 0,
            "freed_bytes": 0,
            "failures": [],
            "partial_runs": [],
            "disk_bytes": disk_bytes,
            "over_cap_bytes": 0,
        }

    groups: dict[str, dict[str, tuple[float, Path]]] = {}
    run_records: dict[str, dict] = {}
    blocked_groups: set[str] = set()
    sources = (
        ("sandbox", sandboxes_dir, "run_*"),
        ("record", runs_dir, "run_*.json"),
        ("ledger", ledger_dir, "run_*.jsonl"),
    )
    try:
        candidates = [
            (kind, path)
            for kind, directory, pattern in sources
            for path in directory.glob(pattern)
        ]
    except (OSError, RuntimeError):
        return {
            "removed": 0,
            "freed_bytes": 0,
            "failures": ["candidate-scan-failed"],
            "partial_runs": [],
            "disk_bytes": disk_bytes,
            "over_cap_bytes": max(0, disk_bytes - max_disk_bytes),
        }

    for kind, path in candidates:
        if kind == "sandbox":
            run_id = path.name
        elif kind == "record":
            run_id = path.name[:-5]
        else:
            run_id = path.name[:-6]
        if not _RUN_ID.fullmatch(run_id) or run_id == keep_run_id:
            continue
        try:
            if path.parent.resolve() != expected_parents[kind]:
                blocked_groups.add(run_id)
                failures.append(f"{run_id}:{kind}:outside-storage")
                continue
            metadata = path.lstat()
        except (OSError, RuntimeError):
            blocked_groups.add(run_id)
            failures.append(f"{run_id}:{kind}:stat-failed")
            continue
        expected_type = stat.S_ISDIR if kind == "sandbox" else stat.S_ISREG
        if not expected_type(metadata.st_mode):
            blocked_groups.add(run_id)
            failures.append(f"{run_id}:{kind}:unexpected-file-type")
            continue
        if kind == "record":
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError, RuntimeError):
                blocked_groups.add(run_id)
                failures.append(f"{run_id}:record:invalid-json")
                continue
            if (
                not isinstance(record, dict)
                or record.get("run_id") != run_id
                or record.get("sandbox") != run_id
            ):
                blocked_groups.add(run_id)
                failures.append(f"{run_id}:record:id-mismatch")
                continue
            run_records[run_id] = record
        elif kind == "ledger" and not _ledger_matches_run(
            path, run_id, run_records.get(run_id)
        ):
            blocked_groups.add(run_id)
            failures.append(f"{run_id}:ledger:invalid-or-mismatched")
            continue
        groups.setdefault(run_id, {})[kind] = (metadata.st_mtime, path)

    oldest_first = sorted(
        (
            min(modified for modified, _ in members.values()),
            run_id,
        )
        for run_id, members in groups.items()
        if run_id not in blocked_groups
    )
    for _, run_id in oldest_first:
        members = groups[run_id]
        if "sandbox" in members and not getattr(
            shutil.rmtree, "avoids_symlink_attacks", False
        ):
            failures.append(f"{run_id}:sandbox:safe-recursive-delete-unavailable")
            continue
        if "record" not in members:
            _forget_run(server, run_id)
        deleted_any = False
        failed = False
        # Remove the record first so any later failure cannot leave an API-visible
        # record claiming the sandbox and ledger are still complete. Keep the
        # append-only ledger until all other artifacts have been removed.
        for kind in ("record", "sandbox", "ledger"):
            if kind not in members:
                continue
            path = members[kind][1]
            try:
                if path.parent.resolve() != expected_parents[kind]:
                    raise OSError("artifact parent changed during prune")
                size = _remove_run_artifact(path, kind)
            except (OSError, RuntimeError):
                failures.append(f"{run_id}:{kind}:delete-failed")
                if deleted_any or kind == "sandbox":
                    # rmtree can fail after deleting some descendants even when
                    # no sibling artifact had yet been removed.
                    partial_runs.append(run_id)
                failed = True
                break
            freed += size
            deleted_any = True
            if kind == "record":
                _forget_run(server, run_id)
        if failed:
            break  # preserve oldest-first order; do not delete a newer run
        _forget_run(server, run_id)
        removed += 1
        try:
            disk_bytes = _dir_bytes(runs_dir, sandboxes_dir, strict=True)
        except (OSError, RuntimeError):
            failures.append("disk-usage-scan-failed")
            disk_bytes = None
            break
        if disk_bytes <= max_disk_bytes:
            break

    try:
        disk_bytes = _dir_bytes(runs_dir, sandboxes_dir, strict=True)
    except (OSError, RuntimeError):
        if "disk-usage-scan-failed" not in failures:
            failures.append("disk-usage-scan-failed")
        disk_bytes = None
    over_cap_bytes = (
        max(0, disk_bytes - max_disk_bytes) if disk_bytes is not None else None
    )
    if over_cap_bytes and not failures:
        failures.append("disk-cap-unmet:no-safe-candidates")
    return {
        "removed": removed,
        "freed_bytes": freed,
        "failures": failures,
        "partial_runs": partial_runs,
        "disk_bytes": disk_bytes,
        "over_cap_bytes": over_cap_bytes,
    }


def main() -> int:
    if not SERVER_PY.is_file():
        sys.stderr.write(f"FATAL: {SERVER_PY} not found\n")
        return 2

    ap = argparse.ArgumentParser(description="Bounded deployment wrapper for the Live Agent")
    ap.add_argument("--fresh", action="store_true",
                    help="DESTRUCTIVE: delete this Live Agent runtime before starting")
    args = ap.parse_args()

    port = _env_int("PORT", 8090)
    on_paas = any(os.environ.get(k) for k in ("PORT", "RENDER", "DYNO", "SPACE_ID"))
    host = os.environ.get("HOST") or ("0.0.0.0" if on_paas else "127.0.0.1")
    max_disk_mb = _env_int("AI2027_MAX_DISK_MB", 256)
    max_runs = _env_int("AI2027_MAX_RUNS", 5000)
    write_token = os.environ.get("AI2027_WRITE_TOKEN", "")
    if write_token and (len(write_token) < 32 or write_token.strip() != write_token):
        sys.stderr.write("FATAL: AI2027_WRITE_TOKEN must be at least 32 characters with no surrounding whitespace\n")
        return 2
    try:
        rate_limit = _bounded_env_int("AI2027_WRITE_RATE_LIMIT", 10, 1, 10000)
        rate_window = _bounded_env_int("AI2027_WRITE_RATE_WINDOW_SECONDS", 60, 1, 86400)
    except ValueError as exc:
        sys.stderr.write(f"FATAL: {exc}\n")
        return 2
    forced_read_only = os.environ.get("AI2027_READ_ONLY", "") == "1"
    writes_enabled = bool(write_token) and not forced_read_only
    rate_limiter = SlidingWindowRateLimiter(rate_limit, rate_window)

    # Import the server as a module so we can wrap run_goal before it starts.
    spec = importlib.util.spec_from_file_location(_MODULE, SERVER_PY)
    if spec is None or spec.loader is None:
        sys.stderr.write("FATAL: cannot load live-agent/server.py\n")
        return 2
    server = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE] = server
    spec.loader.exec_module(server)
    try:
        server.configure_api_access_from_env()
    except ValueError:
        sys.stderr.write("FATAL: invalid API access configuration\n")
        return 2
    runtime_dir = os.environ.get("AI2027_LIVE_RUNTIME_DIR")
    if runtime_dir:
        server._configure_runtime(runtime_dir)
    server._restrict_runtime_permissions()
    restore_warnings = server._prepare_state(fresh=args.fresh)

    max_disk_bytes = max_disk_mb * 1024 * 1024
    state = {
        "runs": 0,
        "pruned": 0,
        "rejected": 0,
        "freed": 0,
        "prune_warnings": 0,
        "partial_prunes": 0,
    }
    lock = threading.Lock()
    real_run_goal = server.run_goal
    real_do_get = server.Handler.do_GET
    real_do_post = server.Handler.do_POST

    def guarded_do_get(handler):
        """Keep run records and artifacts stable while a prune is in progress."""
        with lock:
            return real_do_get(handler)

    def reject_post(handler, status: int, message: str, headers: dict[str, str] | None = None):
        with lock:
            state["rejected"] += 1
        handler.close_connection = True
        response_headers = {"Connection": "close"}
        response_headers.update(headers or {})
        return _respond_json(handler, {"error": message}, status, response_headers)

    def guarded_do_post(handler):
        """Require an operator token and bound write attempts before parsing input."""
        if urlsplit(handler.path).path != "/api/run":
            return real_do_post(handler)
        if forced_read_only:
            return reject_post(
                handler, 403,
                "this deployment is READ-ONLY: POST /api/run is disabled",
            )
        if not write_token:
            return reject_post(
                handler, 503,
                "write endpoint unavailable: AI2027_WRITE_TOKEN is not configured",
            )
        allowed, retry_after = rate_limiter.allow(str(handler.client_address[0]))
        if not allowed:
            return reject_post(
                handler, 429,
                "write rate limit exceeded; retry after the advertised interval",
                {"Retry-After": str(retry_after)},
            )
        if not _bearer_token_matches(handler.headers.get("Authorization", ""), write_token):
            return reject_post(
                handler, 401,
                "valid bearer token required",
                {"WWW-Authenticate": 'Bearer realm="ai-2027-live-agent"'},
            )
        return real_do_post(handler)

    server.Handler.do_GET = guarded_do_get
    server.Handler.do_POST = guarded_do_post

    def guarded_run_goal(goal_type, params):
        """Serialize writes, preserve the run budget, and report prune failures."""
        if not writes_enabled:
            with lock:
                state["rejected"] += 1
            raise ValueError(
                "writes are disabled: configure AI2027_WRITE_TOKEN and set "
                "AI2027_READ_ONLY=0")
        # Keep the run and its following prune in one critical section. Otherwise
        # another request could be pruning a run that has a sandbox but no record yet.
        with lock:
            if state["runs"] >= max_runs:
                state["rejected"] += 1
                raise ValueError(
                    f"run budget exhausted ({max_runs} per process); the demo "
                    "restarts with a fresh budget")
            state["runs"] += 1
            payload = real_run_goal(goal_type, params, cleanup_mode="wrapped")
            run_id = payload.get("run_id")
            try:
                prune_out = _prune(server, max_disk_bytes, keep_run_id=run_id)
            except Exception:
                prune_out = {
                    "removed": 0,
                    "freed_bytes": 0,
                    "failures": ["cleanup-internal-failure"],
                    "partial_runs": [],
                    "disk_bytes": None,
                    "over_cap_bytes": None,
                }
            cleanup, persist_failed = _attach_cleanup_result(server, payload, prune_out)
            state["pruned"] += prune_out["removed"]
            state["freed"] += prune_out["freed_bytes"]
            state["partial_prunes"] += len(prune_out["partial_runs"])
            if cleanup["warnings"]:
                state["prune_warnings"] += 1
                cleanup_event = {
                    "event": "cleanup",
                    "request_id": server.current_request_id(),
                    "status": cleanup["status"],
                    "warnings": cleanup["warnings"],
                }
                sys.stderr.write(json.dumps(cleanup_event, separators=(",", ":")) + "\n")
                sys.stderr.flush()
            if persist_failed:
                state["prune_warnings"] += 1
        return payload

    server.run_goal = guarded_run_goal

    print("AI-2027 Live Agent — deployed mode", flush=True)
    print(f"  bind             : {host}:{port}", flush=True)
    print(f"  runtime mode     : {'fresh (explicit reset)' if args.fresh else 'preserving existing evidence'}", flush=True)
    print("  storage scope    : same-filesystem process restart only; mount durability is host-specific", flush=True)
    print(f"  disk cap         : {max_disk_mb} MB (oldest runs pruned after a run)", flush=True)
    print(f"  run budget       : {max_runs} per process", flush=True)
    if restore_warnings:
        sys.stderr.write(json.dumps({
            "event": "runtime_restore",
            "warning_count": len(restore_warnings),
        }, separators=(",", ":")) + "\n")
    if forced_read_only:
        write_state = "DISABLED (AI2027_READ_ONLY=1)"
    elif not write_token:
        write_state = "DISABLED (no AI2027_WRITE_TOKEN; fail-closed)"
    else:
        write_state = "ENABLED (operator token required)"
    print(f"  write path       : {write_state}", flush=True)
    print(f"  private reads    : {'per-principal token hashes configured' if server.read_access_configured() else 'fail-closed; no reader configured'}", flush=True)
    print(f"  read rate limit  : {server._API_READ_RATE_LIMITER.max_requests} per {server._API_READ_RATE_LIMITER.window_seconds}s per peer IP", flush=True)
    print(f"  write rate limit : {rate_limit} per {rate_window}s per observed peer IP", flush=True)
    print("  health endpoint  : public and minimal; all other /api routes require a reader token", flush=True)
    print("  edge protection  : not provided; use a trusted gateway and edge rate limiting", flush=True)
    print("  kernel           : deterministic planner, NO LLM, stdlib only", flush=True)

    httpd = server.SafeThreadingHTTPServer((host, port), server.Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        try:
            size = _human(_dir_bytes(server.RUNS_DIR, server.SANDBOXES_DIR))
        except (OSError, RuntimeError):
            size = "unavailable"
        print(f"stopped: runs={state['runs']} pruned={state['pruned']} "
              f"partial_prunes={state['partial_prunes']} "
              f"prune_warnings={state['prune_warnings']} "
              f"freed={_human(state['freed'])} rejected={state['rejected']} "
              f"disk_now={size}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
