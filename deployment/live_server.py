#!/usr/bin/env python3
"""Live server launcher with a hard disk cap — for hosting the demo 24/7.

WHY THIS EXISTS
===============
`live-agent/server.py` was written as a *local* demo. Exposed to the internet it
is not safe, and the numbers are measured, not assumed:

  * **No authentication.** `POST /api/run` is open to anyone.
  * **No rate limiting.** ~90 runs/second per connection.
  * **No disk cap.** Every run writes 4 files (~69.5 KB) and they are never
    deleted, so **~7,500 requests fill a 512 MB free tier in about 1.4 minutes**,
    and ~75,000 fill a 5 GB Hugging Face Space in about 14 minutes. After that
    the container dies and the site goes down.
  * **The kernel lock serialises every run** (`_lock` in `server.py`), so a
    handful of clients also makes the demo unusable for everyone.

This launcher does not pretend to fix the missing auth — that is a deliberate,
documented decision (an unauthenticated public demo should stay *read-mostly*,
and the write path should be capped or disabled). What it does fix is the part
that would silently take the service down:

  1. reads `PORT` from the environment (Render/HF/Fly all inject it),
  2. binds `0.0.0.0` (a container must, or nothing is reachable),
  3. caps total disk usage of `runs/` + `sandboxes/`, pruning oldest first,
  4. caps the total number of runs per process,
  5. prints one honest banner stating exactly what IS and IS NOT protected.

Usage
-----
    python3 deployment/live_server.py                 # preserve evidence on normal start
    python3 deployment/live_server.py --fresh          # explicit destructive reset
    PORT=10000 python3 deployment/live_server.py      # any PaaS
    AI2027_READ_ONLY=1 python3 deployment/live_server.py

Environment
-----------
    PORT                     listening port (default 8090)
    HOST                     bind address (default 0.0.0.0 in PaaS, else 127.0.0.1)
    AI2027_LIVE_RUNTIME_DIR   runtime root; default keeps the existing live-agent/ paths
    AI2027_MAX_DISK_MB        cap for generated evidence (default 256)
    AI2027_MAX_RUNS           hard cap on runs per process (default 5000)
    AI2027_READ_ONLY          "1" disables POST /api/run entirely

Runtime files are preserved across process restarts on the same filesystem.
Survival across instance replacement/redeploy depends on a persistent mount;
this launcher and `render.yaml` do not provision one.

Stdlib only — this file adds no dependency to a zero-dependency project.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import stat
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER_PY = ROOT / "live-agent" / "server.py"
_MODULE = "ai2027_live_agent_server"
_RUN_ID = re.compile(r"run_\d+_[0-9a-f]{8}\Z")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


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
    read_only = os.environ.get("AI2027_READ_ONLY", "") == "1"

    # Import the server as a module so we can wrap run_goal before it starts.
    spec = importlib.util.spec_from_file_location(_MODULE, SERVER_PY)
    if spec is None or spec.loader is None:
        sys.stderr.write("FATAL: cannot load live-agent/server.py\n")
        return 2
    server = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE] = server
    spec.loader.exec_module(server)
    runtime_dir = os.environ.get("AI2027_LIVE_RUNTIME_DIR")
    if runtime_dir:
        server._configure_runtime(runtime_dir)
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

    def guarded_do_get(handler):
        """Keep run records and artifacts stable while a prune is in progress."""
        with lock:
            return real_do_get(handler)

    server.Handler.do_GET = guarded_do_get

    def guarded_run_goal(goal_type, params):
        """Serialize writes, preserve the run budget, and report prune failures."""
        if read_only:
            with lock:
                state["rejected"] += 1
            raise ValueError(
                "this deployment is READ-ONLY: POST /api/run is disabled "
                "(set AI2027_READ_ONLY=0 to enable writes)")
        # Keep the run and its following prune in one critical section. Otherwise
        # another request could be pruning a run that has a sandbox but no record yet.
        with lock:
            if state["runs"] >= max_runs:
                state["rejected"] += 1
                raise ValueError(
                    f"run budget exhausted ({max_runs} per process); the demo "
                    "restarts with a fresh budget")
            state["runs"] += 1
            payload = real_run_goal(goal_type, params)
            run_id = payload.get("run_id")
            out = _prune(server, max_disk_bytes, keep_run_id=run_id)
            state["pruned"] += out["removed"]
            state["freed"] += out["freed_bytes"]
            state["partial_prunes"] += len(out["partial_runs"])
            if out["failures"]:
                state["prune_warnings"] += 1
                details = ", ".join(out["failures"][:4])
                if "unsafe-storage-layout" in out["failures"]:
                    remaining = "storage layout rejected; no paths pruned"
                elif out["disk_bytes"] is None:
                    remaining = "disk usage unavailable; cleanup status may be partial"
                else:
                    remaining = (
                        f"usage={_human(out['disk_bytes'])}, "
                        f"over_cap={out['over_cap_bytes']} bytes"
                    )
                print(
                    f"disk prune warning: {details}; {remaining}",
                    file=sys.stderr,
                    flush=True,
                )
        return payload

    server.run_goal = guarded_run_goal

    print("AI-2027 live demo — deployed mode", flush=True)
    print(f"  bind             : {host}:{port}", flush=True)
    print(f"  runtime          : {server.RUNS_DIR.parent}", flush=True)
    print(f"  runtime mode     : {'fresh (explicit reset)' if args.fresh else 'preserving existing evidence'}", flush=True)
    print(f"  storage scope    : same-filesystem process restart only; instance replacement depends on a persistent mount", flush=True)
    print(f"  disk cap         : {max_disk_mb} MB (oldest runs pruned after a run)", flush=True)
    print(f"  run budget       : {max_runs} per process", flush=True)
    for warning in restore_warnings:
        print(f"  runtime warning  : {warning}", file=sys.stderr, flush=True)
    print(f"  write path       : {'DISABLED (read-only)' if read_only else 'ENABLED, unauthenticated'}", flush=True)
    print("  NOT protected    : no login, no rate limit, no per-IP quota", flush=True)
    print("  kernel           : deterministic planner, NO LLM, stdlib only", flush=True)
    print(f"  kernel found     : {(ROOT / 'projects' / 'agi-system')}", flush=True)

    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer((host, port), server.Handler)
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
