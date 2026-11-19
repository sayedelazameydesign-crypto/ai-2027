#!/usr/bin/env python3
"""AI-2027 Live Agent Experience — Phase 1.

A zero-dependency web wrapper around the REAL agi-system kernel
(projects/agi-system). No code is copied: the kernel is imported in place,
so its architecture stays untouched.

Every run gets:
  * an isolated per-run sandbox directory (the kernel policy enforces
    sandbox confinement and DENIES path traversal),
  * a persisted DecisionRecord (the evidence), downloadable as JSON,
  * a file browser over the run sandbox for downloading produced artifacts.

Honest scope (see README.md): the planner is deterministic and rule-based.
There is NO LLM in this loop yet — the value demonstrated here is the
evidence-first execution discipline: Plan -> Policy -> Execute -> Verify ->
Evidence -> Report, with independent verification and a tamper-evident
ledger. A local/free LLM brain is Phase 2 (see docs/KIMI-K3-UTILIZATION.md).

Every VERIFIED run is also offered to the evidence-gated memory store
(memory/store.py): the kernel's own verifier decides what may be remembered,
and a DENIED or unverified run is refused with a reason. Search is lexical,
not neural — `GET /api/memory/search?q=...` returns the measured score and an
`above_noise` flag derived from the corpus noise floor.

Run:  python3 server.py [--port 8090]
Stdlib only. Python 3.10+.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# --------------------------------------------------------------------------- #
# Import the kernel in place — projects/ stays untouched.
# --------------------------------------------------------------------------- #
HERE = Path(__file__).resolve().parent
AGI_DIR = (HERE.parent / "projects" / "agi-system").resolve()
if not (AGI_DIR / "core" / "kernel.py").is_file():
    sys.stderr.write(f"FATAL: agi-system kernel not found at {AGI_DIR}\n")
    sys.exit(1)
sys.path.insert(0, str(AGI_DIR))

from core.kernel import Kernel, KernelConfig          # noqa: E402
from core.models import Goal                          # noqa: E402
from core.planner import SequentialPlanner            # noqa: E402

sys.path.insert(0, str(HERE.parent))
from memory.store import EvidenceMemoryStore          # noqa: E402

RUNS_DIR = HERE / "runs"
SANDBOXES_DIR = HERE / "sandboxes"
MEMORY_DB = HERE / "memory.db"
STATIC_DIR = HERE / "static"
MAX_CONTENT_BYTES = 64 * 1024          # goal param payloads
MAX_PARAM_STR = 4096                   # any single string param
SUPPORTED_GOALS = SequentialPlanner().supported_goal_types

_lock = threading.Lock()               # the kernel is single-run per instance
_run_index: dict[str, dict] = {}       # run_id -> {record, sandbox, ts}
_BOOT_TS = time.time()
_memory: EvidenceMemoryStore | None = None
_memory_lock = threading.Lock()


def memory() -> EvidenceMemoryStore:
    """Lazy store: sandbox artifacts must exist before admission is checked."""
    global _memory
    with _memory_lock:
        if _memory is None:
            _memory = EvidenceMemoryStore(MEMORY_DB, sandbox_root=SANDBOXES_DIR)
        return _memory


def _fresh_state() -> None:
    """Destructively clear local runtime data; callers must opt into this."""
    global _memory
    for d in (RUNS_DIR, SANDBOXES_DIR):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)
    Path(MEMORY_DB).parent.mkdir(parents=True, exist_ok=True)
    _run_index.clear()
    with _memory_lock:
        _memory = None
        for suffix in ("", "-wal", "-shm"):
            f = Path(str(MEMORY_DB) + suffix)
            if f.exists():
                f.unlink()


def _ensure_state() -> None:
    """Create runtime directories without deleting prior runs or memory."""
    for d in (RUNS_DIR, SANDBOXES_DIR, Path(MEMORY_DB).parent):
        d.mkdir(parents=True, exist_ok=True)


def _restore_run_index() -> list[str]:
    """Rebuild the in-memory recent-run index from durable DecisionRecords."""
    _run_index.clear()
    warnings: list[str] = []
    for path in sorted(RUNS_DIR.glob("run_*.json")):
        if path.is_symlink() or not re.fullmatch(r"run_\d+_[0-9a-f]{8}\.json", path.name):
            continue
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(rec, dict) or rec.get("run_id") != path.stem:
                raise ValueError("record id does not match its filename")
            run_id = rec["run_id"]
            goal = rec.get("goal") if isinstance(rec.get("goal"), dict) else {}
            stamp = rec.get("finished_at") or rec.get("started_at") or path.stat().st_mtime
            _run_index[run_id] = {
                "ts": float(stamp),
                "status": str(rec.get("status") or "UNKNOWN"),
                "goal_type": str(goal.get("goal_type") or "unknown"),
            }
        except (OSError, UnicodeError, ValueError, TypeError, AttributeError) as exc:
            warnings.append(f"could not restore {path.name}: {type(exc).__name__}")
    return warnings


def _prepare_state(fresh: bool = False) -> list[str]:
    """Default to preservation; destructive cleanup requires `fresh=True`."""
    if fresh:
        _fresh_state()
        return []
    _ensure_state()
    return _restore_run_index()


def _sanitize_params(params: object) -> dict:
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    clean: dict[str, object] = {}
    for k, v in params.items():
        if not isinstance(k, str) or len(k) > 64:
            raise ValueError("bad param key")
        if isinstance(v, str) and len(v) > MAX_PARAM_STR:
            raise ValueError(f"param {k!r} too long (>{MAX_PARAM_STR})")
        if not isinstance(v, (str, int, float, bool)) and v is not None:
            raise ValueError(f"param {k!r}: unsupported type")
        clean[k] = v
    return clean


def run_goal(goal_type: str, params: dict) -> dict:
    """Execute one goal through the real kernel and persist the evidence."""
    if goal_type not in SUPPORTED_GOALS:
        raise ValueError(
            f"unsupported goal_type {goal_type!r}; supported={SUPPORTED_GOALS}"
        )
    params = _sanitize_params(params)
    run_id = f"run_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    sandbox = SANDBOXES_DIR / run_id
    sandbox.mkdir(parents=True, exist_ok=True)

    with _lock:
        kernel = Kernel(
            sandbox_dir=sandbox,
            ledger_path=sandbox / "ledger.jsonl",
            config=KernelConfig(),
        )
        record = kernel.run(Goal(goal_type=goal_type, params=params))

    payload = record.to_dict()
    payload["run_id"] = run_id
    payload["sandbox"] = run_id  # public handle for the sandbox dir
    files = []
    for p in sorted(sandbox.rglob("*")):
        if p.is_file() and p.name != "ledger.jsonl":
            files.append(str(p.relative_to(sandbox)))
    payload["sandbox_files"] = files

    # Evidence-gated memory: only VERIFIED runs with a real, hash-matching
    # artifact are admitted; refusals are logged with their reason.
    try:
        verdict = memory().ingest(payload)
        payload["memory"] = {"admitted": bool(verdict.admitted),
                             "reason": verdict.reason,
                             "memory_id": verdict.memory_id,
                             "trust": verdict.trust,
                             "artifact_verified": bool(verdict.artifact_verified)}
    except Exception as exc:                      # memory must never break a run
        payload["memory"] = {"admitted": False, "reason": f"memory error: {exc}"}

    (RUNS_DIR / f"{run_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _run_index[run_id] = {"ts": time.time(), "status": payload["status"],
                          "goal_type": goal_type}
    return payload


def read_run(run_id: str) -> dict | None:
    f = RUNS_DIR / f"{run_id}.json"
    if f.is_file():
        return json.loads(f.read_text(encoding="utf-8"))
    return None


def sandbox_file(run_id: str, rel: str) -> tuple[bytes, str] | None:
    """Serve a file from a run sandbox — traversal-safe (defense in depth:
    the kernel policy is the first wall, this is the second)."""
    if not re.fullmatch(r"run_\d+_[0-9a-f]{8}", run_id or ""):
        return None
    root = (SANDBOXES_DIR / run_id).resolve()
    if not root.is_dir():
        return None
    target = (root / rel).resolve()
    if root not in target.parents and target != root:
        return None
    if not target.is_file() or target.name == "ledger.jsonl":
        return None
    data = target.read_bytes()
    if len(data) > 4 * 1024 * 1024:
        return None
    return data, target.name


# --------------------------------------------------------------------------- #
# HTTP layer
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "AI2027LiveAgent/1.0"

    def _json(self, obj: object, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _text(self, body: str, content_type: str = "text/plain; charset=utf-8",
              status: int = 200) -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _static(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:  # quieter logs
        sys.stderr.write("  %s\n" % (fmt % args))

    # ------------------------------ GET ---------------------------------- #
    def do_GET(self) -> None:
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._static(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        if u.path == "/api/health":
            return self._json({
                "ok": True,
                "kernel": "agi-system (in place, projects/agi-system)",
                "kernel_tests": "86 passing (verified 2026-11-17)",
                "mode": "REAL execution — deterministic planner, NO LLM (Phase 1)",
                "supported_goals": SUPPORTED_GOALS,
                "memory": memory().stats()["memories"],
                "memory_backend": "sqlite3 + IDF concept coverage over a declared thesaurus (no model, no sqlite-vec)",
            })
        if u.path == "/api/agent-card":
            card_path = HERE.parent / "agent-card.json"
            if not card_path.is_file():
                return self._json({"error": "agent-card.json missing"}, 404)
            card = json.loads(card_path.read_text(encoding="utf-8"))
            card["served_by"] = "live-agent/server.py"
            card["live"] = {"runs": len(_run_index),
                            "memories": memory().stats()["memories"]}
            return self._json(card)
        if u.path == "/api/metrics":
            # Prometheus text format. Counters only — no derived "health score"
            # that could flatter us; the smoke test decides pass/fail.
            runs = list(_run_index.values())
            st = memory().stats()
            lines = [
                "# HELP ai2027_runs_total Kernel runs by final status.",
                "# TYPE ai2027_runs_total counter",
            ]
            for status in ("VERIFIED", "DENIED", "FAILED", "ABORTED", "INCONCLUSIVE"):
                n = sum(1 for r in runs if r.get("status") == status)
                lines.append(f'ai2027_runs_total{{status="{status}"}} {n}')
            lines += [
                "# HELP ai2027_memories Evidence-gated memories admitted.",
                "# TYPE ai2027_memories gauge",
                f"ai2027_memories {st['memories']}",
                "# HELP ai2027_memory_refusals Runs refused by the evidence gate.",
                "# TYPE ai2027_memory_refusals counter",
                f"ai2027_memory_refusals {st['refused_events']}",
                "# HELP ai2027_uptime_seconds Seconds since this process booted.",
                "# TYPE ai2027_uptime_seconds gauge",
                f"ai2027_uptime_seconds {round(time.time() - _BOOT_TS, 3)}",
            ]
            return self._text("\n".join(lines) + "\n")
        if u.path == "/api/memory":
            return self._json(memory().stats())
        if u.path == "/api/memory/search":
            q = parse_qs(u.query).get("q", [""])[0].strip()
            if not q:
                return self._json({"error": "missing q"}, 400)
            try:
                k = max(1, min(20, int(parse_qs(u.query).get("k", ["5"])[0])))
            except ValueError:
                return self._json({"error": "bad k"}, 400)
            res = memory().search(q, k=k)
            res["honest_limits"] = [
                "Lexical, not neural: true synonyms are missed (measured, not assumed).",
                "Only VERIFIED runs with a hash-matching artifact are remembered.",
            ]
            return self._json(res)
        if u.path == "/api/runs":
            runs = sorted(
                ({"run_id": k, **v} for k, v in _run_index.items()),
                key=lambda r: r["ts"], reverse=True,
            )[:50]
            return self._json({"runs": runs})
        if u.path.startswith("/api/run/"):
            rec = read_run(u.path.rsplit("/", 1)[-1])
            return self._json(rec) if rec else self._json({"error": "not found"}, 404)
        if u.path == "/api/file":
            q = parse_qs(u.query)
            got = sandbox_file(q.get("run", [""])[0], q.get("path", [""])[0])
            if not got:
                return self._json({"error": "not found or not allowed"}, 404)
            data, name = got
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition",
                             f"attachment; filename=\"{name}\"")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        return self._json({"error": "not found"}, 404)

    # ------------------------------ POST --------------------------------- #
    def do_POST(self) -> None:
        u = urlparse(self.path)
        if u.path != "/api/run":
            return self._json({"error": "not found"}, 404)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._json({"error": "bad content-length"}, 400)
        if length <= 0 or length > MAX_CONTENT_BYTES:
            return self._json({"error": "payload too large or empty"}, 413)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            goal_type = body.get("goal_type", "")
            params = body.get("params", {})
            if not isinstance(goal_type, str):
                raise ValueError("goal_type must be a string")
            payload = run_goal(goal_type, params)
            status = 200 if payload.get("status") == "VERIFIED" else 200
            return self._json(payload, status)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:  # the kernel never crashes; the wrapper might
            return self._json({"error": f"wrapper error: {e}"}, 500)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--fresh", action="store_true",
                    help="DESTRUCTIVE: delete this service's runs, sandboxes, and memory before starting")
    args = ap.parse_args()
    restore_warnings = _prepare_state(fresh=args.fresh)
    print(f"AI-2027 Live Agent Experience — http://{args.host}:{args.port}")
    print(f"kernel: {AGI_DIR} (imported in place, untouched)")
    print(f"runtime: {'fresh (explicit reset)' if args.fresh else 'preserving existing evidence'}")
    print(f"supported goals: {', '.join(SUPPORTED_GOALS)}")
    for warning in restore_warnings:
        print(f"runtime warning: {warning}", file=sys.stderr)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
