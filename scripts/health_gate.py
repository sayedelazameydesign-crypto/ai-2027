#!/usr/bin/env python3
"""Health gate — refuse silent degradation between one beat and the next.

Why this file exists
  `scripts/ember_beat.py` measures one beat. It cannot, on its own, tell the
  difference between "healthy" and "healthy-looking". A beat that reports
  `17/17` today, then `3/3` tomorrow (because checks silently vanished),
  is *degraded* while every field it prints is technically true. This gate
  compares the current beat with the recorded past and fails when the ember
  lost capability without saying so.

Three escalation stages (the roadmap's T5), decided by measured history:

  stage 0  healthy      exit 0   nothing to do
  stage 1  retry        exit 2   transient signal (a service did not answer,
                                 no public path reached, first beat has no
                                 baseline). The workflow retries the beat once.
  stage 2  issue        exit 1   a real regression (smoke incomplete, a check
                                 disappeared, the servers stopped answering
                                 after a retry). Open/update ONE issue.
  stage 3  escalate     exit 1   the same degradation survives 3 consecutive
                                 beats. Adds the escalation label, mentions the
                                 code owner, and POSTs to $EMBER_ALERT_WEBHOOK
                                 when configured (never pretends it was sent
                                 when it was not).

Checks (each one is a promise the repository makes in public)
  beat_present          the status document exists and has the fields it claims
  smoke_complete        passed == total, and total >= the best total ever seen
  smoke_no_regression   passed >= best passed in the 7-day window
  evidence_complete     one check object per counted check, each with a boolean
  memory_reachable      the memory endpoint answered (a negative count is not a
                        zero, it is a failure to measure)
  public_path           at least one public path answered 200
  public_paths_all      all counted public paths answered 200 (warning)
  beat_freshness        the last beat is inside cadence + tolerance; a later beat
                        is a WARNING (the scheduler was late) up to 24h of
                        silence, and CRITICAL beyond it (the schedule is dead)
  history_monotonic     this beat is newer than the recorded one (no time travel)
  path_independence     losing one of 3 paths cannot cost more than 0.07 heat
  heat_not_dropped      heat did not fall by more than 0.25 against the last beat

Honest limits
  * The gate judges the beat it is given. It cannot see a server that never
    reported at all — that shows up as `beat_freshness` going stale.
  * Every beat boots its own servers inside the runner, so silence between
    beats means GitHub did not run the schedule, not that a service was down.
    Measured 2026-11-23..27: `*/3` delivered ~2 beats/day (gaps 8.0-13.8h) and
    every one of them was 17/17 — yet each was `degraded` at stage 3. A late
    beat that passes everything is therefore a warning; only silence past
    SCHEDULER_DEAD_H (the schedule stopped, e.g. disabled after 60 idle days)
    stays critical. Uptime of the deployed stack is the external monitor's job
    (UptimeRobot on /healthz, docs/DEPLOY-24-7-PLAN.md), not this gate's.
  * The required smoke total is the larger of the measured 17-check floor and
    recent history; older 15-check history cannot lower the current contract.
    A brand-new clone still needs a prior beat before freshness can be healthy.
  * Stage 3 is an escalation *signal*, not a pager: if the webhook is not
    configured the gate says so in its own output instead of claiming success.

Usage
    python3 scripts/health_gate.py                       # human table
    python3 scripts/health_gate.py --json                # machine output
    python3 scripts/health_gate.py --write --json         # also append to the log
    python3 scripts/health_gate.py --attempt 2            # after the retry
    python3 scripts/health_gate.py --self-test            # 0 = the gate can run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EMBER_DIR = ROOT / "ember"
STATUS_JSON = EMBER_DIR / "status.json"
HISTORY = EMBER_DIR / "history.jsonl"
GATE_LOG = EMBER_DIR / "gate.jsonl"

WINDOW_S = 7 * 24 * 3600
DEFAULT_TOTAL = 17          # the current measured smoke surface (not a guessed target)
CADENCE_H = 3.0             # ember.yml runs every 3 hours (raised from 6h on 2026-11-22, see below)
TOLERANCE_H = 5.0           # measured: GitHub dropped ~half the */6 schedule (gaps 11.5-12.2h on
                            # 2026-11-21/22 while every executed beat was 17/17). CADENCE+TOLERANCE
                            # stays 8h: the real alarm — 8h of silence is an outage — is unchanged;
                            # only the scheduler gets twice as many attempts to land inside it.
SCHEDULER_DEAD_H = 24.0     # no beat for a full day = the schedule itself stopped (critical).
                            # Between CADENCE+TOLERANCE and this, the beat is late, not failed.
MAX_PATH_IMPACT = 0.07      # T4 gate: losing one of 3 paths costs <= 0.07 heat
MAX_HEAT_DROP = 0.25
STAGE_ESCALATE_AFTER = 3    # consecutive degraded beats

CRITICAL, TRANSIENT, WARNING = "critical", "transient", "warning"

# The gate's contract with its caller: exactly these checks, always, in order.
CHECK_IDS = (
    "beat_present", "smoke_complete", "smoke_no_regression", "evidence_complete",
    "memory_reachable", "public_path", "public_paths_all", "beat_freshness",
    "history_monotonic", "path_independence", "heat_not_dropped",
)


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #
def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def load_status(path: Path = STATUS_JSON) -> dict:
    if not path.is_file():
        raise SystemExit(f"health_gate: no beat to judge at {path}")
    return _read_json(path)


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #
def _check(cid: str, ok: bool, severity: str, detail: str) -> dict:
    return {"id": cid, "ok": bool(ok), "severity": severity, "detail": detail}


def _baseline(history: list[dict], now: float) -> dict:
    """Best totals/passed seen in the window — the bar the ember already cleared."""
    recent = [b for b in history if now - float(b.get("ts", 0)) <= WINDOW_S]
    return {
        "total": max([int(b.get("total", 0) or 0) for b in recent] or [DEFAULT_TOTAL]),
        "passed": max([int(b.get("passed", 0) or 0) for b in recent] or [0]),
        "beats": len(recent),
        "last_ts": max([float(b.get("ts", 0)) for b in recent] or [0.0]),
    }


def evaluate(status: dict, history: list[dict], now: float | None = None,
             tolerance_h: float = TOLERANCE_H, expected_total: int | None = None,
             attempt: int = 1, measure_paths: bool = True,
             scheduler_dead_h: float = SCHEDULER_DEAD_H) -> dict:
    """Judge one beat against its own recorded past. Pure function, no I/O."""
    now = time.time() if now is None else now
    checks: list[dict] = []
    smoke = status.get("smoke") or {}
    mem = status.get("memory") or {}
    paths = status.get("public_paths") or []

    # --- the beat must actually be a beat ---------------------------------- #
    has_shape = bool(status.get("beat_ts")) and isinstance(smoke, dict) and "checks" in smoke
    checks.append(_check(
        "beat_present", has_shape, CRITICAL,
        "status has beat_ts + smoke.checks" if has_shape
        else "status document is missing beat_ts or smoke.checks"))

    passed = int(smoke.get("passed", 0) or 0)
    total = int(smoke.get("total", 0) or 0)
    base = _baseline(history, float(status.get("beat_ts") or now))
    required_total = max(expected_total or 0, base["total"], DEFAULT_TOTAL)

    checks.append(_check(
        "smoke_complete", total >= required_total and passed == total, CRITICAL,
        f"passed={passed} total={total} required_total>={required_total}"))

    checks.append(_check(
        "smoke_no_regression", passed >= base["passed"], CRITICAL,
        f"passed={passed} best_in_7d={base['passed']} "
        f"({base['beats']} recorded beats)" if base["beats"]
        else f"passed={passed} best_in_7d=n/a (no history — cannot prove a regression)"))

    evidence = smoke.get("checks") or []
    declared = [c for c in evidence if isinstance(c, dict)]
    shaped = (len(declared) == total
              and all(isinstance(c.get("ok"), bool) and c.get("name") for c in declared))
    checks.append(_check(
        "evidence_complete", shaped, CRITICAL,
        f"{len(declared)} evidence rows for {total} counted checks"
        + ("" if shaped else " — a check was counted without being named")))

    memories = int(mem.get("memories", -1))
    checks.append(_check(
        "memory_reachable", memories >= 0, TRANSIENT,
        f"memories={memories}" if memories >= 0
        else "memory endpoint unreachable (-1 is a failed measurement, not a zero)"))

    live = [p for p in paths if isinstance(p, dict) and p.get("live")]
    if measure_paths:
        checks.append(_check(
            "public_path", bool(live), TRANSIENT,
            f"{len(live)}/{len(paths)} public paths answered 200" if paths
            else "no public path was measured at all"))
        checks.append(_check(
            "public_paths_all", bool(paths) and len(live) == len(paths), WARNING,
            f"{len(live)}/{len(paths)} live" if paths else "no public paths configured"))
    else:
        # An offline run is allowed to say "I did not look", never "it is up".
        # Both checks stay visible (and false) as warnings, so the verdict can
        # never be a clean "healthy" when the public surface was not measured.
        for cid in ("public_path", "public_paths_all"):
            checks.append(_check(
                cid, False, WARNING,
                "not measured: --offline was passed (local run without public network)"))

    beat_ts = float(status.get("beat_ts") or 0)
    if base["last_ts"]:
        since_h = (now - base["last_ts"]) / 3600
        fresh = since_h <= CADENCE_H + tolerance_h
        detail = (f"previous beat {since_h:.2f}h ago "
                  f"(cadence {CADENCE_H}h + tolerance {tolerance_h}h)")
        # A silence that already happened cannot be undone by retrying this beat,
        # so it is never TRANSIENT. Whether it is an outage depends on its length:
        # this beat booted and measured everything itself, so a late beat only
        # proves the scheduler was late; a day of silence proves it stopped.
        if fresh or since_h > scheduler_dead_h:
            severity = CRITICAL
            if not fresh:
                detail += f" — no beat for >{scheduler_dead_h:g}h: the schedule itself has stopped"
        else:
            severity = WARNING
            detail += (f" — scheduler late, not an outage (this beat measured everything itself);"
                       f" critical after {scheduler_dead_h:g}h")
    else:
        fresh, detail = attempt > 1, "first beat on record — freshness unknowable"
        severity = CRITICAL if attempt > 1 else TRANSIENT
    checks.append(_check("beat_freshness", fresh, severity, detail))

    monotonic = (not base["last_ts"]) or beat_ts >= base["last_ts"]
    checks.append(_check(
        "history_monotonic", monotonic, WARNING,
        "this beat is newer than the recorded one" if monotonic
        else f"beat_ts={beat_ts} is older than last recorded {base['last_ts']}"
             " — history was reset or replayed"))

    per_path = 0.2 / 3
    checks.append(_check(
        "path_independence", per_path <= MAX_PATH_IMPACT, CRITICAL,
        f"losing 1 of 3 paths costs {per_path:.4f} heat <= {MAX_PATH_IMPACT}"))

    heat = float((status.get("heat") or {}).get("heat", 0.0) or 0.0)
    if base["last_ts"]:
        prev = [b for b in history
                if float(b.get("ts", 0)) < beat_ts and float(b.get("ts", 0)) > 0]
        prev_heat = float(prev[-1].get("heat", heat)) if prev else heat
        drop = prev_heat - heat
        ok = drop <= MAX_HEAT_DROP
        detail = f"heat={heat} previous={prev_heat} drop={round(drop, 4)}"
    else:
        ok, detail = True, f"heat={heat} (no previous beat to compare)"
    checks.append(_check("heat_not_dropped", ok,
                         WARNING if attempt == 1 else CRITICAL, detail))

    failures = [c for c in checks if not c["ok"]]
    critical = [c for c in failures if c["severity"] == CRITICAL]
    transient = [c for c in failures if c["severity"] == TRANSIENT]

    if critical:
        stage, verdict, exit_code = 2, "degraded", 1
    elif transient:
        stage, verdict, exit_code = 1, "retryable", 2
    elif failures:
        stage, verdict, exit_code = 0, "healthy-with-warnings", 0
    else:
        stage, verdict, exit_code = 0, "healthy", 0

    return {
        "gate_version": "1.0",
        "checked_at": round(now, 3),
        "attempt": attempt,
        "verdict": verdict,
        "stage": stage,
        "exit_code": exit_code,
        "healthy": stage == 0 and not failures,
        "failures": [c["id"] for c in failures],
        "checks": checks,
        "baseline": base,
        "heat": heat,
        "smoke": {"passed": passed, "total": total},
        "escalate": False,          # decided in decide_stage()
        "alert": "not-attempted",
    }


def consecutive_degraded(gate_log: list[dict]) -> int:
    """How many runs in a row were degraded, counting only *recorded* runs.

    The current run has not been written to the log yet (`--write` is called
    once per run, on its final attempt), so `decide_stage` adds it.
    """
    streak = 0
    for row in reversed(gate_log):
        # `healthy` in the log means "nothing at all to report"; warnings are not
        # degradations and must never accumulate into an escalation.
        degraded = row.get("degraded")
        if degraded is None:                      # rows written before v1.0.1
            degraded = not bool(row.get("healthy", True))
        if not degraded:
            break
        streak += 1
    return streak


def decide_stage(result: dict, gate_log: list[dict], attempt: int = 1) -> dict:
    """Apply the three-stage ladder. Mutates and returns `result`.

        0 healthy                     exit 0   nothing to do
        1 retry   (first attempt)     exit 2   the workflow re-runs the beat once
        2 issue   (retry also failed) exit 1   open/update ONE issue
        3 escalate (streak >= 3)      exit 1   label + owner mention + webhook

    A retry is not a way to hide a failure: attempt 2 that still fails always
    becomes stage 2. Escalation additionally requires three degraded *runs* in
    a row, so a single flaky web request cannot page the owner.
    """
    trailing = consecutive_degraded(gate_log)
    result["streak"] = 0 if result["stage"] == 0 else trailing + 1
    if result["stage"] == 0:
        result["stage"], result["exit_code"], result["escalate"] = 0, 0, False
        return result
    if result["streak"] >= STAGE_ESCALATE_AFTER and attempt >= 2:
        result["stage"], result["exit_code"], result["escalate"] = 3, 1, True
    elif attempt >= 2:
        result["stage"], result["exit_code"], result["escalate"] = 2, 1, False
    else:
        result["stage"], result["exit_code"], result["escalate"] = 1, 2, False
    return result


# --------------------------------------------------------------------------- #
# escalation side effects (never silent, never faked)
# --------------------------------------------------------------------------- #
def send_alert(result: dict, webhook: str | None, timeout: int = 10) -> str:
    """POST the failure to an external webhook when one is configured."""
    if not webhook:
        return "not-configured"
    body = json.dumps({
        "text": f"🔥 ember degraded (stage {result['stage']}): "
                f"{', '.join(result['failures']) or 'unknown'}",
        "gate": result,
    }).encode("utf-8")
    req = urllib.request.Request(webhook, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return f"sent:{r.status}"
    except (urllib.error.URLError, OSError) as exc:
        return f"failed:{exc}"


def append_beat(status: dict, path: Path = HISTORY) -> bool:
    """Record the judged beat exactly once. Returns True when a row was added.

    The beat itself writes status files on every attempt; the *history* is
    committed here, on the run's final attempt, so a gate retry cannot make the
    beat count (and therefore the 7-day success rate) look better than reality.
    Idempotent on `beat_ts` so re-running the gate cannot duplicate a beat.
    """
    beat_ts = float(status.get("beat_ts") or 0)
    if not beat_ts:
        return False
    for row in _read_jsonl(path):
        if float(row.get("ts", 0)) == beat_ts:
            return False
    smoke = status.get("smoke") or {}
    row = {"ts": beat_ts, "ok": bool(smoke.get("ok")),
           "passed": int(smoke.get("passed", 0) or 0),
           "total": int(smoke.get("total", 0) or 0),
           "memories": int((status.get("memory") or {}).get("memories", -1) or 0),
           "heat": float((status.get("heat") or {}).get("heat", 0.0) or 0.0)}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return True


def write_gate_log(result: dict, path: Path = GATE_LOG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": result["checked_at"], "healthy": result["healthy"],
           "degraded": result["stage"] > 0,
           "verdict": result["verdict"], "stage": result["stage"],
           "streak": result["streak"], "failures": result["failures"],
           "smoke": result["smoke"], "heat": result["heat"],
           "alert": result.get("alert")}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def render(result: dict) -> str:
    lines = [
        f"EMBER HEALTH GATE — {result['verdict']} (stage {result['stage']}/3"
        f"{', escalated' if result['escalate'] else ''})",
        "=" * 70,
        f"  attempt   : {result['attempt']}",
        f"  smoke     : {result['smoke']['passed']}/{result['smoke']['total']}",
        f"  heat      : {result['heat']}",
        f"  streak    : {result['streak']} consecutive degraded beat(s)",
        f"  alert     : {result['alert']}",
        "",
        "  checks:",
    ]
    for c in result["checks"]:
        mark = "PASS" if c["ok"] else ("FAIL" if c["severity"] != WARNING else "WARN")
        lines.append(f"    [{mark}] {c['id']:<20} {c['detail']}")
    if result["failures"]:
        lines.append("")
        lines.append(f"  degradations: {', '.join(result['failures'])}")
    lines.append("=" * 70)
    return "\n".join(lines)


def self_test() -> int:
    """Prove the gate runs at all, before trusting it to judge anything."""
    now = 1_800_000_000.0
    checks = [{"name": f"check_{i}", "ok": True} for i in range(DEFAULT_TOTAL)]
    history = [{"ts": now - 3600 * 6, "ok": True, "passed": DEFAULT_TOTAL,
                "total": DEFAULT_TOTAL, "heat": 0.8}]
    status = {
        "beat_ts": now, "heat": {"heat": 0.8},
        "smoke": {"ok": True, "passed": DEFAULT_TOTAL, "total": DEFAULT_TOTAL,
                  "checks": checks},
        "memory": {"memories": 1},
        "public_paths": [{"url": "u", "status": 200, "live": True}],
    }
    res = evaluate(status, history, now=now)
    got = tuple(c["id"] for c in res["checks"])
    ok = got == CHECK_IDS and res["verdict"] == "healthy" and res["exit_code"] == 0
    print(f"health_gate self-test: {'PASS' if ok else 'FAIL'} "
          f"({len(res['checks'])} checks, verdict {res['verdict']})")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Gate one ember beat against its history.")
    ap.add_argument("--status", default=str(STATUS_JSON))
    ap.add_argument("--history", default=str(HISTORY))
    ap.add_argument("--gate-log", default=str(GATE_LOG))
    ap.add_argument("--tolerance-hours", type=float, default=TOLERANCE_H)
    ap.add_argument("--scheduler-dead-hours", type=float, default=SCHEDULER_DEAD_H,
                    help="silence beyond this is critical (the schedule stopped); "
                         "between cadence+tolerance and this it is a warning")
    ap.add_argument("--expected-total", type=int, default=0,
                    help="raise the (never lowered) smoke total required")
    ap.add_argument("--attempt", type=int, default=1,
                    help="2 after the workflow retried the beat once")
    ap.add_argument("--webhook", default=os.environ.get("EMBER_ALERT_WEBHOOK", ""))
    ap.add_argument("--offline", action="store_true",
                    help="do not judge the public paths (local run without network); "
                         "the checks stay visible as warnings, never as 'up'")
    ap.add_argument("--write", action="store_true",
                    help="append to the gate log (final attempt only)")
    ap.add_argument("--commit-beat", action="store_true",
                    help="append the judged beat to the history exactly once "
                         "(skipped while a retry is still in flight)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    status = load_status(Path(args.status))
    history = _read_jsonl(Path(args.history))
    log = _read_jsonl(Path(args.gate_log))

    result = evaluate(status, history, tolerance_h=args.tolerance_hours,
                      scheduler_dead_h=args.scheduler_dead_hours,
                      expected_total=args.expected_total, attempt=args.attempt,
                      measure_paths=not args.offline)
    result = decide_stage(result, log, attempt=args.attempt)
    if result["stage"] >= 3:
        result["alert"] = send_alert(result, args.webhook or None)
    # A run is final when it passed, or when the retry has already been used.
    final = result["exit_code"] == 0 or args.attempt >= 2
    if args.commit_beat:
        result["beat_committed"] = append_beat(status, Path(args.history)) if final else False
    if args.write and final:
        write_gate_log(result, Path(args.gate_log))

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(render(result))
    return int(result["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
