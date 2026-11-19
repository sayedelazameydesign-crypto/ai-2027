#!/usr/bin/env python3
"""AI-2027 Unified Command HUD — LIVE control over the REAL deterministic kernel.

NOT a simulation and NOT a JavaScript port: every `/api/run` calls
`live-agent/server.py::run_goal`, which imports `projects/agi-system/core`
in place and returns a real DecisionRecord with independent SHA-256 evidence.

Layers shown in one screen:
  * live telemetry (p50/p95 measured here, counters, latency sparkline)
  * an append-only audit log of real DecisionRecords
  * an escape-test button (../../escape.txt) and a 100-input red-team suite
  * a HUD-level dispatch freeze (kill switch)
  * a read-only health audit of all 48 projects (PROVEN/DECLARED/SIMULATED/...)

Honest limits:
  * the kill switch gates THIS HUD's dispatch endpoint only. It does not stop
    arbitrary processes on the machine, and it does not gate verification
    endpoints (red-team / audit) — those must keep working to prove the wall.
  * the planner is deterministic; there is NO LLM in this loop (Phase 2 is a
    separate, still-undecided decision).
  * browser-facing code uses RELATIVE urls only, so it works behind the Arena
    preview proxy as well as on localhost.

Run: python3 command-hud/server.py --port 8090 --host 0.0.0.0
Stdlib only. Python 3.10+.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import audit as audit_mod          # noqa: E402
import kernel_bridge              # noqa: E402
import redteam                    # noqa: E402

sys.path.insert(0, str(HERE.parent / "memory"))
import store as memory_store      # noqa: E402  (L1 memory, stdlib sqlite3)

STATIC = HERE / "static"
MAX_BODY = 64 * 1024
MAX_HISTORY = 400

BOOT = time.time()
LOCK = threading.RLock()
SRV = None  # the real live-agent module, loaded in main()

MEM = None   # EvidenceMemoryStore, loaded in main()

STATE = {
    "frozen": False,
    "history": [],          # newest last, capped at 400
    "redteam": None,        # last red-team report
    "audit": None,          # last audit dict
    "audit_thread": None,
    "boot_errors": [],
}

HONESTY = {
    "kernel": "NOT a simulation: REAL agi-system kernel (projects/agi-system/core), imported in place — not copied, not reimplemented",
    "planner": "deterministic rule-based planner — NO LLM, no API key, no network call",
    "evidence": "independent Verifier re-reads the sandbox; HUD additionally re-hashes produced files with hashlib.sha256",
    "kill_switch": "gates this HUD's /api/run dispatch only; it does NOT stop OS processes or disable verification endpoints",
    "timings": "browser/HUD measurements on this container; NOT the published bench.py reference numbers (jitter published)",
    "audit": "read-only. DECLARED != PROVEN. 47 of 48 projects were NOT executed in this sandbox.",
    "redteam": "100 real adversarial inputs. 75 DENIED + 25 CONTAINED-as-literal + 0 breaches is the honest result, not '100/100 DENIED'.",
    "memory": ("L1 cumulative memory: sqlite3 + IDF concept coverage over a declared thesaurus (NOT neural, NOT sqlite-vec). "
               "Only VERIFIED runs with a passing independent hash check are admitted. Measured recall@3 on a "
               "paraphrase set is 11/16 = 0.688 with 0/7 near-miss false positives; still lexical, still declared; see memory/README.md."),
    "browser_skill": ("L5 BrowserSkill adapter (Stage 1/3 · mode=local): read-only status via "
                      "browser_skill_adapter/probe.py (BSK_AUTO_START=0, zero mutations). "
                      "Reports SKIPPED when bsk/daemon/extension is absent — never simulated."),
}


#: Files the kernel writes for its own bookkeeping. They are NOT user evidence:
#: hashing one of them would report a sha256 for a DENIED run — a false proof.
INTERNAL_FILES = {"ledger.jsonl", "metrics.json"}


def _sha256_of_run(rec: dict, params: dict) -> dict | None:
    """Independently re-read the INTENDED target file and hash it.

    Only VERIFIED runs that actually produced the requested file get a hash.
    DENIED / FROZEN / FAILED runs must return None: no execution happened, so
    there is no produced artefact to prove. Found by test_hud.py — the kernel
    writes metrics.json even on denial, and hashing it was a false claim.
    """
    if not SRV or rec.get("status") != "VERIFIED":
        return None
    rel = params.get("path")
    if not isinstance(rel, str) or not rel or Path(rel).name in INTERNAL_FILES:
        return None
    root = (SRV.SANDBOXES_DIR / str(rec.get("sandbox") or rec.get("run_id"))).resolve()
    try:
        f = (root / rel).resolve()
        if root not in f.parents or not f.is_file():
            return None
        data = f.read_bytes()
    except (OSError, ValueError):
        return None
    return {"file": rel, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def _timings() -> dict:
    # Only REAL kernel calls belong in a latency distribution. Control-plane
    # events (kill toggle) and frozen dispatches have elapsed_ms=0 and would
    # otherwise drag p50 to 0 — a fake measurement. Found by live smoke test.
    vals = [h["elapsed_ms"] for h in STATE["history"]
            if h.get("elapsed_ms") and h["elapsed_ms"] > 0
            and h.get("status") in ("VERIFIED", "DENIED", "FAILED")
            and h.get("source") != "control"]
    if not vals:
        return {"n": 0, "p50_ms": None, "p95_ms": None, "max_ms": None,
                "spread_pct": None, "series": []}
    s = sorted(vals)
    return {
        "n": len(s),
        "p50_ms": round(statistics.median(s), 3),
        "p95_ms": round(s[min(len(s) - 1, int(0.95 * len(s)))], 3),
        "max_ms": round(max(s), 3),
        "mean_ms": round(statistics.mean(s), 3),
        "spread_pct": round((max(s) - min(s)) / min(s) * 100, 1) if min(s) > 0 else None,
        "series": [round(v, 3) for v in vals[-60:]],
    }


def _counters() -> dict:
    c = {"VERIFIED": 0, "DENIED": 0, "FAILED": 0, "FROZEN": 0,
         "ERROR": 0, "CONTROL": 0}
    for h in STATE["history"]:
        k = h.get("status")
        if k in c:
            c[k] += 1
        else:
            c["ERROR"] += 1
    return c


def _history_path() -> Path:
    """HUD-owned durable event log, kept beside its isolated memory DB."""
    if SRV is None:
        raise RuntimeError("kernel runtime is not initialized")
    return Path(SRV.MEMORY_DB).parent / "hud-history.jsonl"


def _append_history_entries(entries: list[dict]) -> None:
    """Append one batch to the local JSONL log; the caller holds ``LOCK``."""
    if not entries:
        return
    path = _history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for entry in entries:
        saved = {k: v for k, v in entry.items()
                 if k not in ("history_persisted", "persistence_error")}
        lines.append(json.dumps(saved, ensure_ascii=False, default=str,
                                separators=(",", ":")) + "\n")
    with path.open("ab") as stream:
        stream.write("".join(lines).encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())


def _history_entry_from_run(rec: dict) -> dict | None:
    """Rebuild a HUD summary when a kernel record survived but its last HUD
    append was interrupted. The kernel record remains the evidence source.
    """
    run_id = rec.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"run_\d+_[0-9a-f]{8}", run_id):
        return None
    if (rec.get("sandbox") or run_id) != run_id:
        return None
    goal = rec.get("goal") if isinstance(rec.get("goal"), dict) else {}
    params = goal.get("params") if isinstance(goal.get("params"), dict) else {}
    try:
        started = float(rec.get("started_at") or time.time())
        finished = float(rec.get("finished_at") or started)
    except (TypeError, ValueError):
        started, finished = time.time(), time.time()
    evidence = rec.get("evidence") if isinstance(rec.get("evidence"), list) else []
    checks = evidence[0].get("checks", {}) if evidence and isinstance(evidence[0], dict) else {}
    if not isinstance(checks, dict):
        checks = {}
    files = rec.get("sandbox_files") if isinstance(rec.get("sandbox_files"), list) else []
    plan = rec.get("plan") if isinstance(rec.get("plan"), dict) else {}
    actions = plan.get("actions") or plan.get("steps") or []
    reasons = rec.get("denial_reasons") if isinstance(rec.get("denial_reasons"), list) else []
    return {
        "run_id": run_id,
        "status": rec.get("status", "UNKNOWN"),
        "goal_type": goal.get("goal_type", "unknown"),
        "params": params,
        "source": "recovered",
        "reason": (reasons or [rec.get("error") or ""])[0],
        "elapsed_ms": round(max(0.0, (finished - started) * 1000), 3),
        "sha256": _sha256_of_run(rec, params),
        "evidence_checks": checks,
        "sandbox_files": [f for f in files
                          if isinstance(f, str) and Path(f).name not in INTERNAL_FILES],
        "policy": rec.get("policy"),
        "plan_steps": len(actions) if isinstance(actions, list) else 0,
        "memory": rec.get("memory"),
        "ts": started,
        "history_persisted": False,
    }


def restore_history() -> list[dict]:
    """Restore recent HUD events and recover any run omitted by a crash.

    The JSONL contains HUD metadata/control events; durable kernel run files
    backfill a run if the process stopped between kernel persistence and the
    HUD append. Invalid lines are reported, never silently rewritten/deleted.
    """
    if SRV is None:
        return []
    errors: list[str] = []
    saved: deque[dict] = deque(maxlen=MAX_HISTORY)
    try:
        history_file = _history_path()
        if history_file.is_file():
            with history_file.open("r", encoding="utf-8") as stream:
                for line_no, line in enumerate(stream, 1):
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                        if not isinstance(entry, dict) or not isinstance(entry.get("run_id"), str):
                            raise ValueError("event must be an object with a run_id")
                        entry["history_persisted"] = True
                        saved.append(entry)
                    except (json.JSONDecodeError, ValueError) as exc:
                        if len(errors) < 20:
                            errors.append(f"{history_file.name}:{line_no}: {type(exc).__name__}")
    except (OSError, UnicodeError) as exc:
        errors.append(f"hud-history.jsonl: {type(exc).__name__}")

    entries = list(saved)
    recovered_events: list[dict] = []
    logged_run_ids = {e["run_id"] for e in entries
                      if re.fullmatch(r"run_\d+_[0-9a-f]{8}", e["run_id"])}
    try:
        run_files = sorted(Path(SRV.RUNS_DIR).glob("run_*.json"))[-MAX_HISTORY:]
    except OSError as exc:
        run_files = []
        errors.append(f"run index: {type(exc).__name__}")
    for path in run_files:
        if path.is_symlink() or not re.fullmatch(r"run_\d+_[0-9a-f]{8}\.json", path.name):
            continue
        if path.stem in logged_run_ids:
            continue
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(rec, dict) or rec.get("run_id") != path.stem:
                raise ValueError("record id does not match its filename")
            recovered = _history_entry_from_run(rec)
            if recovered:
                entries.append(recovered)
                recovered_events.append(recovered)
        except (OSError, UnicodeError, ValueError, TypeError, AttributeError) as exc:
            if len(errors) < 20:
                errors.append(f"{path.name}: {type(exc).__name__}")

    if recovered_events:
        try:
            with LOCK:
                _append_history_entries(recovered_events)
            for entry in recovered_events:
                entry["history_persisted"] = True
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            if len(errors) < 20:
                errors.append(f"history backfill: {type(exc).__name__}")

    def event_order(entry: dict) -> tuple[float, str]:
        try:
            stamp = float(entry.get("ts") or 0)
        except (TypeError, ValueError):
            stamp = 0.0
        return stamp, str(entry.get("run_id", ""))

    entries.sort(key=event_order)
    if errors:
        with LOCK:
            STATE["boot_errors"].extend(f"history restore warning: {e}" for e in errors)
            del STATE["boot_errors"][:-50]
    return entries[-MAX_HISTORY:]


def _record(entry: dict) -> bool:
    """Append a HUD event durably, then publish it in the bounded live view."""
    persisted = False
    error = None
    with LOCK:
        try:
            _append_history_entries([entry])
            persisted = True
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            error = f"{type(exc).__name__}: {exc}"

        entry["history_persisted"] = persisted
        if error:
            STATE["boot_errors"].append(f"HUD history persistence failed: {error}")
            del STATE["boot_errors"][:-50]
        STATE["history"].append(entry)
        if len(STATE["history"]) > MAX_HISTORY:
            del STATE["history"][:-MAX_HISTORY]
    return persisted


def dispatch(goal_type: str, params: dict, source: str = "manual") -> dict:
    """Run one goal through the real kernel. Honours the HUD dispatch freeze."""
    ts = time.time()
    if STATE["frozen"]:
        out = {
            "run_id": f"frozen_{int(ts)}_{len(STATE['history'])}",
            "status": "FROZEN", "goal_type": goal_type,
            "params": params, "source": source,
            "reason": "HUD dispatch freeze engaged — system in read-only mode. "
                      "The kernel was NOT called.",
            "elapsed_ms": 0.0, "sha256": None,
            "evidence_checks": {}, "sandbox_files": [],
        }
        _record(out)
        return out

    t0 = time.perf_counter()
    rec = None
    try:
        rec = SRV.run_goal(goal_type, params)
        elapsed = round((time.perf_counter() - t0) * 1000, 3)
        ev = (rec.get("evidence") or [{}])[0].get("checks", {}) if rec.get("evidence") else {}
        plan = rec.get("plan") or {}
        plan_steps = plan.get("actions") or plan.get("steps") or []
        out = {
            "run_id": rec.get("run_id"), "status": rec.get("status"),
            "goal_type": goal_type, "params": params, "source": source,
            "reason": (rec.get("denial_reasons") or [rec.get("error") or ""])[0],
            "elapsed_ms": elapsed,
            "sha256": _sha256_of_run(rec, params),
            "evidence_checks": ev,
            "sandbox_files": [f for f in rec.get("sandbox_files", [])
                              if Path(f).name not in INTERNAL_FILES],
            "policy": rec.get("policy"),
            "plan_steps": len(plan_steps) if isinstance(plan_steps, list) else 0,
        }
    except Exception as exc:
        elapsed = round((time.perf_counter() - t0) * 1000, 3)
        out = {
            "run_id": f"error_{int(ts)}", "status": "ERROR",
            "goal_type": goal_type, "params": params, "source": source,
            "reason": f"{type(exc).__name__}: {exc}",
            "elapsed_ms": elapsed, "sha256": None,
            "evidence_checks": {}, "sandbox_files": [],
        }
    out["ts"] = ts
    # Offer EVERY run to memory, not only VERIFIED ones: the admission gate is
    # the single door, and its refusals must be logged. Skipping refused runs
    # here silently emptied admission_log — caught by the live smoke test
    # (refused_events stayed 0 while a DENIED run happened) and fixed so the
    # "refused and logged" claim is actually true.
    if MEM is not None and isinstance(rec, dict):
        try:
            v = MEM.ingest(rec)
            out["memory"] = {"admitted": v.admitted, "duplicate": v.duplicate,
                             "memory_id": v.memory_id, "reason": v.reason}
        except Exception as exc:  # memory must never break the kernel path
            out["memory"] = {"admitted": False, "reason": f"memory error: {exc}"}
    _record(out)
    return out


def _run_audit(full: bool, background: bool = True) -> None:
    def worker():
        try:
            d = audit_mod.build(fast=not full)
            with LOCK:
                STATE["audit"] = d
        except Exception as exc:
            with LOCK:
                STATE["boot_errors"].append(f"audit failed: {type(exc).__name__}: {exc}")
    if background:
        t = threading.Thread(target=worker, daemon=True, name="audit-probes")
        STATE["audit_thread"] = t
        t.start()
    else:
        worker()


def state_payload() -> dict:
    with LOCK:
        a = STATE["audit"]
        return {
            "ok": True,
            "server": "AI-2027 Unified Command HUD",
            "booted_at": BOOT, "uptime_s": round(time.time() - BOOT, 1),
            "frozen": STATE["frozen"],
            "kernel": {
                "path": "projects/agi-system/core",
                "imported_in_place": True,
                "planner": "deterministic (NO LLM)",
                "supported_goals": list(SRV.SUPPORTED_GOALS) if SRV else [],
                "python": platform.python_version(),
                "platform": platform.platform(),
            },
            "counters": _counters(),
            "timings": _timings(),
            "history": list(STATE["history"][-30:])[::-1],
            "redteam": STATE["redteam"],
            "audit": ({"mode": a["mode"], "generated_at": a["generated_at"],
                       "summary": a["summary"], "repo_checks": a["repo_checks"],
                       "probes": a["probes"], "architecture": a["architecture"],
                       "critical": a["critical"][:20],
                       "projects": a["projects"]} if a else None),
            "audit_busy": bool(STATE["audit_thread"] and STATE["audit_thread"].is_alive()),
            "boot_errors": list(STATE["boot_errors"]),
            "honesty": HONESTY,
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "AI2027CommandHUD/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("  %s\n" % (fmt % args))

    def _json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, name: str) -> None:
        p = STATIC / name
        if not p.is_file():
            return self._json({"error": "not found"}, 404)
        data = p.read_bytes()
        ctype = "text/html; charset=utf-8" if name.endswith(".html") else \
                "application/javascript; charset=utf-8" if name.endswith(".js") else \
                "text/css; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if n <= 0 or n > MAX_BODY:
            return {}
        try:
            v = json.loads(self.rfile.read(n).decode("utf-8"))
            return v if isinstance(v, dict) else {}
        except (ValueError, UnicodeDecodeError):
            return {}

    # ------------------------------ GET ---------------------------------- #
    def do_GET(self) -> None:
        u = urlparse(self.path)
        p = u.path
        if p in ("/", "/index.html"):
            return self._static("index.html")
        if p in ("/app.js", "/static/app.js"):
            return self._static("app.js")
        if p == "/api/health":
            return self._json({"ok": True, "service": "command-hud",
                               "kernel": "REAL agi-system (imported in place)",
                               "frozen": STATE["frozen"],
                               "no_llm": True, "stdlib_only": True})
        if p == "/api/state":
            return self._json(state_payload())
        if p == "/api/memory":
            if MEM is None:
                return self._json({"error": "memory not initialised"}, 503)
            return self._json({"stats": MEM.stats(), "verify": MEM.verify(),
                               "recent": [dict(r) for r in reversed(MEM.rows()[-10:])]})
        if p == "/api/browser/status":
            probe_script = HERE.parent / "browser_skill_adapter" / "probe.py"
            if not probe_script.is_file():
                return self._json({"error": "browser_skill_adapter not present"}, 404)
            proc = subprocess.run(
                [sys.executable, str(probe_script), "--json"],
                capture_output=True, text=True, timeout=15, shell=False,
            )
            try:
                data = json.loads(proc.stdout)
            except ValueError:
                return self._json({"error": "probe returned invalid JSON",
                                   "exit_code": proc.returncode}, 502)
            data["hud_frozen"] = STATE["frozen"]
            return self._json(data, 200)
        if p == "/api/audit":
            a = STATE["audit"]
            return self._json(a) if a else self._json({"error": "audit still computing"}, 503)
        if p.startswith("/api/record/"):
            rid = p.rsplit("/", 1)[-1]
            rec = SRV.read_run(rid) if SRV else None
            return self._json(rec) if rec else self._json({"error": "not found"}, 404)
        return self._json({"error": "not found"}, 404)

    # ------------------------------ POST --------------------------------- #
    def do_POST(self) -> None:
        u = urlparse(self.path)
        p = u.path
        body = self._body()
        if p == "/api/run":
            gt = body.get("goal_type", "create_file")
            params = body.get("params", {})
            if not isinstance(gt, str) or not isinstance(params, dict):
                return self._json({"error": "goal_type must be a string and params an object"}, 400)
            return self._json(dispatch(gt, params, "manual"))
        if p == "/api/escape":
            return self._json(dispatch("create_file",
                                      {"path": "../../escape.txt", "content": "x"},
                                      "escape-test"))
        if p == "/api/pulse":
            n = len(STATE["history"]) + 1
            return self._json(dispatch("create_file",
                                      {"path": f"pulse/pulse_{n}.txt", "content": f"pulse-{n}"},
                                      "live-pulse"))
        if p == "/api/kill":
            enabled = bool(body.get("enabled", not STATE["frozen"]))
            with LOCK:
                STATE["frozen"] = enabled
            history_persisted = _record({"run_id": f"kill_{int(time.time())}", "status": "CONTROL",
                                         "goal_type": "kill-switch", "params": {"enabled": enabled},
                                         "source": "control", "ts": time.time(), "elapsed_ms": 0.0,
                                         "reason": "HUD dispatch freeze " + ("ENGAGED" if enabled else "RELEASED"),
                                         "sha256": None, "evidence_checks": {}, "sandbox_files": []})
            return self._json({"ok": True, "frozen": STATE["frozen"],
                               "history_persisted": history_persisted,
                               "scope": HONESTY["kill_switch"]})
        if p == "/api/redteam":
            # Verification endpoint: deliberately NOT gated by the freeze.
            r = redteam.run()
            with LOCK:
                STATE["redteam"] = r
            return self._json(r)
        if p == "/api/memory/search":
            if MEM is None or not isinstance(body.get("query"), str) or not body["query"].strip():
                return self._json({"error": "query required"}, 400)
            return self._json(MEM.search(body["query"], int(body.get("k", 5))))
        if p == "/api/audit/refresh":
            _run_audit(full=True, background=True)
            return self._json({"ok": True, "status": "audit refresh started",
                               "note": "5 real probes are executing in the background"})
        return self._json({"error": "not found"}, 404)


def main() -> None:
    global SRV
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--runtime-dir", default=os.environ.get("AI2027_HUD_RUNTIME_DIR"),
                    help="isolate persistent HUD evidence/history/memory under this directory")
    ap.add_argument("--fast-audit", action="store_true",
                    help="skip the 5 subprocess probes at boot")
    ap.add_argument("--fresh", action="store_true",
                    help="DESTRUCTIVE: delete this HUD runtime's evidence, history, and memory before starting")
    args = ap.parse_args()

    global MEM
    runtime_root = (Path(args.runtime_dir).expanduser().resolve()
                    if args.runtime_dir else kernel_bridge.RUNTIME)
    SRV = kernel_bridge.load(runtime=runtime_root, fresh=args.fresh)
    STATE["history"] = restore_history()
    hud_memory_db = runtime_root / "memory.sqlite"
    if args.fresh:
        for suffix in ("", "-wal", "-shm"):
            Path(str(hud_memory_db) + suffix).unlink(missing_ok=True)
    MEM = memory_store.EvidenceMemoryStore(
        hud_memory_db, sandbox_root=SRV.SANDBOXES_DIR)
    STATE["audit"] = audit_mod.build(fast=True)
    if not args.fast_audit:
        _run_audit(full=True, background=True)

    print(f"AI-2027 Unified Command HUD — http://{args.host}:{args.port}")
    print(f"kernel      : {kernel_bridge.ROOT / 'projects/agi-system'} (REAL, imported in place)")
    print(f"goals       : {', '.join(SRV.SUPPORTED_GOALS)}")
    print(f"runtime     : {runtime_root} (isolated evidence trail)")
    print(f"runtime mode: {'fresh (explicit reset)' if args.fresh else 'preserving existing evidence/history'}")
    print("honest scope: deterministic planner, NO LLM. Kill switch gates HUD dispatch only.")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
