#!/usr/bin/env python3
"""Bridge to the REAL agi-system kernel, with an isolated runtime directory.

We do NOT copy or reimplement the kernel. We import `live-agent/server.py` in
place (which itself inserts `projects/agi-system` into sys.path and imports
`core.*` directly). All runtime paths (runs, sandboxes, and the kernel's SQLite
state) are redirected so the HUD never writes into the Live Agent demo's
separate evidence trail. Existing HUD runtime data is preserved by default; a
clean reset is explicit (`fresh=True`).

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


def load(runtime: Path | None = None, fresh: bool = False):
    """Return the live-agent module, pointed at `runtime`.

    runtime defaults to command-hud/runtime (gitignored). Pass a tempdir in
    tests. Existing evidence is retained by default; `fresh=True` is a
    destructive reset for disposable test/demo runtimes only.
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
    # Use the Live Agent's validated runtime setup so the HUD shares its
    # no-symlink layout checks and owner-only permission policy without sharing
    # the Live Agent's separate runtime data.
    mod._configure_runtime(rt)
    mod._restrict_runtime_permissions()
    mod._prepare_state(fresh=fresh)
    history_file = rt / "state" / "hud-history.jsonl"
    if fresh:
        # The HUD's summary/audit trail is separate from the kernel DB. A full
        # explicit reset must clear both stores, and only this runtime.
        history_file.unlink(missing_ok=True)
    return mod
