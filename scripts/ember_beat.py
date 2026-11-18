#!/usr/bin/env python3
"""Ember beat — one heartbeat of the project, measured, not narrated.

What a beat does
  1. runs the REAL smoke test over HTTP against already-running servers
     (`scripts/runtime_smoke.py --json`) — no mocks, no simulation;
  2. appends the result to `ember/history.jsonl` (append-only, git-tracked so the
     beat survives process death and CI runner recycling);
  3. computes the HEAT INDEX from measured history, not from optimism;
  4. writes `ember/status.json` (machine) and `STATUS.md` (human), both derived
     from the same variables so they can never disagree;
  5. exits 1 when degraded, so CI can open an issue instead of smiling.

Heat Index (documented in docs/ROADMAP-2027.md):
    0.4 * success_rate_7d
  + 0.2 * recency      (1 - min(longest_gap_hours, 24) / 24)
  + 0.2 * fuel         (min(memories, 100) / 100)
  + 0.2 * embers       (live_paths / 3)

Honest limits, stated up front:
  * A beat is NOT a health guarantee: it measures the servers it was given.
  * If no beat ran for a week, `success_rate_7d` is computed over the beats that
    exist and `recency` collapses to 0 — silence shows up as cold, never as warm.
  * The index is a scheduling signal, not a quality metric. Quality lives in the
    smoke checks and `memory/evaluate.py`.

Usage:
    python3 scripts/ember_beat.py                     # human table
    python3 scripts/ember_beat.py --json              # machine output
    python3 scripts/ember_beat.py --no-smoke          # status only (cold compute)
    python3 scripts/ember_beat.py --pages-url URL     # count a live public path
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import network_preflight  # noqa: E402  (same-directory helper, stdlib only)

ROOT = Path(__file__).resolve().parent.parent
EMBER_DIR = ROOT / "ember"
STATUS_JSON = EMBER_DIR / "status.json"
HISTORY = EMBER_DIR / "history.jsonl"
STATUS_MD = ROOT / "STATUS.md"
SMOKE = ROOT / "scripts" / "runtime_smoke.py"
WINDOW_S = 7 * 24 * 3600


# --------------------------------------------------------------------------- #
# measurement
# --------------------------------------------------------------------------- #
def run_smoke(base: str, hud: str | None, timeout: int = 180) -> dict:
    """Run the real smoke as a subprocess and parse its --json output."""
    cmd = [sys.executable, str(SMOKE), "--json", "--live-agent", base]
    if hud:
        cmd += ["--hud", hud]
    else:
        cmd += ["--no-hud"]
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    out = (proc.stdout or "").strip().splitlines()
    payload: dict
    try:
        payload = json.loads(out[-1]) if out else {}
    except json.JSONDecodeError:
        payload = {}
    if not payload:
        payload = {"ok": False, "passed": 0, "total": 0,
                   "checks": [], "error": (proc.stderr or "")[-400:]}
    payload["exit_code"] = proc.returncode
    payload["started_at"] = started
    return payload


def memory_counts(base: str, timeout: int = 10) -> tuple[int, int]:
    """(memories, refusals) from the live agent, or (-1, -1) if unreachable."""
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/api/memory",
                                    timeout=timeout) as r:
            d = json.loads(r.read())
        return int(d.get("memories", 0)), int(d.get("refused_events", 0))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return -1, -1


def public_paths(urls: list[str], timeout: int = 10,
                 report: "network_preflight.Report | None" = None,
                 opener=None) -> list[dict]:
    """Check public URLs, distinguishing "dead" from "I cannot see the internet".

    A network failure used to be flattened to ``status: 0, live: False`` — which
    published "the sites are down" from an environment that simply had no
    egress (measured 2026-11-18). Each entry now carries a ``verdict``:

        live     the server answered 200
        dead     a server answered, with a non-200 status (that is evidence)
        blocked  we could not reach it *and* preflight proved we cannot judge

    ``blocked`` entries are excluded from the heat index by ``beat()`` so a blind
    environment cannot cool the repository down.
    """
    report = report or network_preflight.cached_preflight()
    open_fn = opener or urllib.request.urlopen
    out = []
    for u in urls:
        code = 0
        verdict = "blocked"
        if report.allows(u):
            try:
                req = urllib.request.Request(u, method="GET")
                with open_fn(req, timeout=timeout) as r:
                    code = r.status
                verdict = "live" if code == 200 else "dead"
            except urllib.error.HTTPError as exc:      # an answer, not a silence
                code = exc.code
                verdict = "dead"
            except (urllib.error.URLError, OSError):
                # Egress exists but this host did not answer: a real failure.
                code = 0
                verdict = "dead"
        out.append({"url": u, "status": code, "live": verdict == "live",
                    "verdict": verdict})
    return out


# --------------------------------------------------------------------------- #
# heat
# --------------------------------------------------------------------------- #
def load_history() -> list[dict]:
    if not HISTORY.is_file():
        return []
    rows = []
    for line in HISTORY.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def heat_index(history: list[dict], memories: int, embers_live: int,
               now: float | None = None, embers_measured: bool = True) -> dict:
    """Deterministic Heat Index over measured beats. See module docstring."""
    now = now or time.time()
    recent = [b for b in history if now - float(b.get("ts", 0)) <= WINDOW_S]
    beats_7d = len(recent)
    success = (sum(1 for b in recent if b.get("ok")) / beats_7d) if beats_7d else 0.0

    stamps = sorted(float(b.get("ts", 0)) for b in recent)
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    last = stamps[-1] if stamps else None
    longest_gap_h = round(max(gaps) / 3600, 2) if gaps else 0.0
    since_last_h = round((now - last) / 3600, 2) if last else None
    # recency uses the gap that hurts most: silence since the last beat OR the
    # longest silence inside the window. A repo that ran once and stopped is cold.
    # NOTE: `since_last_h or 24.0` was a real bug — 0.0 is falsy, so a beat taken
    # seconds ago scored as 24h of silence. Caught by running it, not by review.
    worst_gap_h = max(longest_gap_h, 24.0 if since_last_h is None else since_last_h)
    recency = max(0.0, 1.0 - min(worst_gap_h, 24.0) / 24.0)
    fuel = min(max(memories, 0), 100) / 100
    embers = min(embers_live, 3) / 3
    # An unmeasurable component is DROPPED and the weights are renormalised —
    # never scored as zero. A blind environment must not look like a cold repo.
    weights = {"success": 0.4, "recency": 0.2, "fuel": 0.2,
               "embers": 0.2 if embers_measured else 0.0}
    scale = sum(weights.values())
    components = {k: round(w * v, 4) for (k, w), v in zip(
        weights.items(), (success, recency, fuel, embers))}
    total = round(sum(components.values()) / scale, 4) if scale else 0.0
    verdict = ("burning" if total >= 0.8 else
               "hot-without-flame" if total >= 0.5 else "going-out")
    return {"heat": total, "verdict": verdict, "beats_7d": beats_7d,
            "success_rate_7d": round(success, 4), "longest_gap_h": longest_gap_h,
            "since_last_beat_h": since_last_h, "memories": memories,
            "embers_live": embers_live, "embers_measured": embers_measured,
            "weights": {k: round(w / scale, 4) for k, w in weights.items()} if scale else {},
            "components": components}


# --------------------------------------------------------------------------- #
# publishing
# --------------------------------------------------------------------------- #
def write_status(status: dict) -> None:
    EMBER_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_JSON.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    checks = status["smoke"]["checks"]
    egress = status.get("egress") or {}
    blocked = int(status.get("public_paths_blocked", 0) or 0)
    judged = [p for p in (status.get("public_paths") or []) if p.get("verdict") != "blocked"]
    live_paths = sum(1 for p in judged if p.get("live"))
    if blocked:
        paths_row = (f"{live_paths}/{len(judged)} public "
                     f"(**{blocked} BLOCKED** — not counted, see egress)")
    elif judged:
        paths_row = f"{live_paths}/{len(judged)} public"
    else:
        paths_row = (f"{status['heat']['embers_live']} ember(s), local agent only "
                     "— no public URL configured")
    egress_row = (egress.get("egress", "not measured") if egress else "not measured")
    if egress and not egress.get("general_egress"):
        egress_row += " — public sites CANNOT be judged from this runner"
    rows = "\n".join(
        f"| {'PASS' if c['ok'] else 'FAIL'} | {c['name']} | {c.get('note', '')} |"
        for c in checks
    ) or "| — | no checks recorded | — |"
    STATUS_MD.write_text(f"""# 🔥 Ember status

> Generated by `scripts/ember_beat.py` — do not edit by hand.
> Every number here comes from an executed command, never from a promise.

| Field | Value |
|---|---|
| Heat Index | **{status['heat']['heat']}** ({status['heat']['verdict']}) |
| Last beat (UTC) | `{status['beat_at_iso']}` |
| Smoke | **{status['smoke']['passed']}/{status['smoke']['total']}** checks |
| Beats in last 7 days | {status['heat']['beats_7d']} |
| Success rate (7d) | {status['heat']['success_rate_7d']} |
| Longest silence (h) | {status['heat']['longest_gap_h']} |
| Memories admitted | {status['memory']['memories']} (refused {status['memory']['refusals']}) |
| Public egress | {egress_row} |
| Public paths live | {paths_row} |

## This beat

| Result | Check | Evidence |
|---|---|---|
{rows}

## Honest limits

- A beat measures the servers it was given — it is not an uptime guarantee.
- No beat for a week ⇒ `recency` = 0 ⇒ the index goes cold. Silence is never
  reported as warmth.
- Heat is a scheduling signal, not a quality metric.
- A public path that could not be reached **because this runner has no general
  egress** is recorded as `blocked` and removed from the heat weight, not
  counted as a dead site (`scripts/network_preflight.py`).
""", encoding="utf-8")


def append_history(status: dict) -> None:
    EMBER_DIR.mkdir(parents=True, exist_ok=True)
    row = {"ts": status["beat_ts"], "ok": status["smoke"]["ok"],
           "passed": status["smoke"]["passed"], "total": status["smoke"]["total"],
           "memories": status["memory"]["memories"],
           "heat": status["heat"]["heat"]}
    with HISTORY.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def beat(base: str, hud: str | None, pages_urls: list[str], do_smoke: bool) -> dict:
    started = time.time()
    if do_smoke:
        smoke = run_smoke(base, hud)
    else:
        smoke = {"ok": False, "passed": 0, "total": 0, "checks": [],
                 "error": "smoke skipped by flag (--no-smoke)"}
    memories, refusals = memory_counts(base)
    egress = network_preflight.cached_preflight() if pages_urls else None
    paths = public_paths(pages_urls, report=egress) if pages_urls else []
    judged = [p for p in paths if p["verdict"] != "blocked"]
    blocked = [p for p in paths if p["verdict"] == "blocked"]
    embers_live = (1 if memories >= 0 else 0) + sum(1 for p in judged if p["live"])
    # With no public URL configured there is nothing to measure ⇒ old semantics.
    embers_measured = bool(judged) or not pages_urls

    history = load_history()
    now = time.time()
    provisional = {"ts": now, "ok": smoke.get("ok", False)}
    heat = heat_index(history + [provisional], memories, embers_live, now=now,
                      embers_measured=embers_measured)

    status = {
        "beat_ts": now,
        "beat_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "duration_ms": round((time.time() - started) * 1000, 1),
        "smoke": {"ok": bool(smoke.get("ok")), "passed": smoke.get("passed", 0),
                  "total": smoke.get("total", 0),
                  "checks": smoke.get("checks", []),
                  "error": smoke.get("error")},
        "memory": {"memories": memories, "refusals": refusals},
        "public_paths": paths,
        "public_paths_blocked": len(blocked),
        "egress": egress.to_dict() if egress else None,
        "heat": heat,
        "degraded": not bool(smoke.get("ok")),
    }
    return status


def main() -> int:
    ap = argparse.ArgumentParser(description="One measured beat of the ember.")
    ap.add_argument("--live-agent", default="http://127.0.0.1:8090")
    ap.add_argument("--hud", default="http://127.0.0.1:8091")
    ap.add_argument("--no-hud", action="store_true")
    ap.add_argument("--no-smoke", action="store_true",
                    help="do not run the smoke (status/heat only)")
    ap.add_argument("--pages-url", action="append", default=[],
                    help="public URL to count as an ember (repeatable)")
    ap.add_argument("--write", action="store_true",
                    help="write ember/status.json + STATUS.md and append history")
    ap.add_argument("--no-history", action="store_true",
                    help="write the status files but do not append a history row "
                         "(used while a health-gate retry is still in flight, so a "
                         "retried run cannot inflate the beat count)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    status = beat(args.live_agent, None if args.no_hud else args.hud,
                  args.pages_url, do_smoke=not args.no_smoke)
    if args.write:
        write_status(status)
        if not args.no_history:
            append_history(status)

    if args.json:
        print(json.dumps(status, ensure_ascii=False))
    else:
        print(f"EMBER BEAT — {status['beat_at_iso']}")
        print("=" * 66)
        print(f"  smoke     : {status['smoke']['passed']}/{status['smoke']['total']}"
              + ("" if status['smoke']['ok'] else f"  ({status['smoke'].get('error')})"))
        print(f"  memories  : {status['memory']['memories']}"
              f" (refused {status['memory']['refusals']})")
        print(f"  embers    : {status['heat']['embers_live']} live"
              + (f" · {status.get('public_paths_blocked', 0)} BLOCKED"
                 if status.get("public_paths_blocked") else ""))
        if status.get("egress"):
            eg = status["egress"]
            print(f"  egress    : {eg['egress']}"
                  + ("" if eg["general_egress"]
                     else "  (public sites cannot be judged here)"))
        print(f"  heat      : {status['heat']['heat']} ({status['heat']['verdict']})")
        if args.write:
            print(f"  wrote     : {STATUS_JSON.relative_to(ROOT)} · "
                  f"{STATUS_MD.relative_to(ROOT)}")
        print("=" * 66)
        print("DEGRADED — a beat failed" if status["degraded"] else "healthy beat")
    return 1 if status["degraded"] else 0


if __name__ == "__main__":
    sys.exit(main())
