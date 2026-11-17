#!/usr/bin/env python3
"""Bridge to the REAL agi-system kernel, with an isolated runtime directory.

We do NOT copy or reimplement the kernel. We import `live-agent/server.py` in
place (which itself inserts `projects/agi-system` into sys.path and imports
`core.*` directly). Only RUNS_DIR / SANDBOXES_DIR are redirected so the HUD
never writes into the live-agent demo's evidence trail.

Stdlib only.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE_SERVER = ROOT / "live-agent" / "server.py"
RUNTIME = ROOT / "command-hud" / "runtime"
_MODULE_NAME = "ai2027_live_agent_server"


def load(runtime: Path | None = None, fresh: bool = True):
    """Return the live-agent module, pointed at `runtime`.

    runtime defaults to command-hud/runtime (gitignored). Pass a tempdir in
    tests. `fresh=True` clears the evidence trail for a clean boot.
    """
    if not LIVE_SERVER.is_file():
        raise RuntimeError(f"live-agent server not found: {LIVE_SERVER}")
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, LIVE_SERVER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load live-agent/server.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = mod
    spec.loader.exec_module(mod)

    rt = Path(runtime) if runtime is not None else RUNTIME
    mod.RUNS_DIR = rt / "runs"
    mod.SANDBOXES_DIR = rt / "sandboxes"
    if fresh:
        mod._fresh_state()
    return mod
