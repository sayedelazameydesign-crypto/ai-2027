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
import os
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER_PY = ROOT / "live-agent" / "server.py"
_MODULE = "ai2027_live_agent_server"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _human(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{n / 1024:.0f} KB"


def _dir_bytes(*paths: Path) -> int:
    total = 0
    for root in paths:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
    return total


def _prune(server, max_disk_bytes: int, keep_run_id: str | None = None) -> dict:
    """Delete the OLDEST runs until the evidence tree fits under the cap.

    Pruning is by run directory mtime, so the newest run — the one a visitor is
    looking at — survives. `keep_run_id` is an extra guard for the in-flight run.
    """
    removed, freed = 0, 0
    while _dir_bytes(server.RUNS_DIR, server.SANDBOXES_DIR) > max_disk_bytes:
        candidates = []
        for d in list(server.SANDBOXES_DIR.glob("run_*")) + list(server.RUNS_DIR.glob("run_*.json")):
            if keep_run_id and keep_run_id in d.name:
                continue
            try:
                candidates.append((d.stat().st_mtime, d))
            except OSError:
                continue
        if not candidates:
            break  # nothing prunable: the cap cannot be honoured, do not loop
        candidates.sort(key=lambda t: t[0])
        _, victim = candidates[0]
        try:
            if victim.is_dir():
                freed += _dir_bytes(victim)
                shutil.rmtree(victim, ignore_errors=True)
            else:
                freed += victim.stat().st_size
                victim.unlink()
            removed += 1
        except OSError:
            break
    return {"removed": removed, "freed_bytes": freed}


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
    state = {"runs": 0, "pruned": 0, "rejected": 0, "freed": 0}
    lock = threading.Lock()
    real_run_goal = server.run_goal

    def guarded_run_goal(goal_type, params):
        """Cap the two things that can take the container down."""
        if read_only:
            with lock:
                state["rejected"] += 1
            raise ValueError(
                "this deployment is READ-ONLY: POST /api/run is disabled "
                "(set AI2027_READ_ONLY=0 to enable writes)")
        with lock:
            if state["runs"] >= max_runs:
                state["rejected"] += 1
                raise ValueError(
                    f"run budget exhausted ({max_runs} per process); the demo "
                    "restarts with a fresh budget")
            state["runs"] += 1
        payload = real_run_goal(goal_type, params)
        run_id = payload.get("run_id")
        with lock:
            out = _prune(server, max_disk_bytes, keep_run_id=run_id)
            state["pruned"] += out["removed"]
            state["freed"] += out["freed_bytes"]
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
        size = _dir_bytes(server.RUNS_DIR, server.SANDBOXES_DIR)
        print(f"stopped: runs={state['runs']} pruned={state['pruned']} "
              f"freed={_human(state['freed'])} rejected={state['rejected']} "
              f"disk_now={_human(size)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
