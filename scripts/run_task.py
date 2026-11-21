#!/usr/bin/env python3
"""Run one real task end-to-end and hand back an evidence link (T6).

The 2027 acceptance criterion for T6 is a sentence anyone can falsify:

    "an outside user completes a full task in under 60 seconds, and every
     result carries an evidence link."

Prose cannot satisfy that, so this script is the measurement:

  1. (optionally) boots the real stack with `scripts/ember_up.sh`;
  2. submits a real goal to the real kernel over real HTTP;
  3. re-reads the persisted run record — the evidence — by `run_id`;
  4. downloads the produced artifact and recomputes its SHA-256 locally, then
     compares it with the hash the kernel recorded (an independent check, not a
     copy of the kernel's own claim);
  5. writes the whole bundle to disk and prints the two links;
  6. fails when the measured wall time exceeds the budget.

Nothing here is simulated. If the task is DENIED, this script reports DENIED and
exits non-zero — a refusal is a correct outcome, not a successful task.

Usage
    python3 scripts/run_task.py --boot
    python3 scripts/run_task.py --goal create_file \
        --param path=demo/hello.txt --param content="hello 2027"
    python3 scripts/run_task.py --json            # machine output
    python3 scripts/run_task.py --budget 60       # the published ceiling (default)

Exit codes
    0  VERIFIED, evidence verified, inside budget
    1  task/evidence failure or partial/failed/unreported cleanup; not_applicable is
       accepted only when the direct Live Agent mode is explicitly reported
    2  the agent was unreachable
    3  the task succeeded but exceeded the budget
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EVIDENCE_DIR = ROOT / "launch" / "evidence"
TIMEOUT = 30


from live_agent_auth import authorization_header, open_url  # noqa: E402


class Unreachable(Exception):
    pass


def http_json(base: str, path: str, payload: dict | None = None) -> dict:
    url = base.rstrip("/") + path
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    authorization = authorization_header(
        base, path, "POST" if payload is not None else "GET")
    if authorization:
        headers["Authorization"] = authorization
    req = urllib.request.Request(
        url, data=data, method="POST" if payload is not None else "GET",
        headers=headers)
    try:
        with open_url(req, timeout=TIMEOUT) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:      # 4xx is a legitimate answer
        try:
            return json.loads(exc.read())
        except ValueError:
            raise Unreachable(f"{path} -> HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise Unreachable(f"{path}: {exc}") from exc


def http_bytes(base: str, path: str) -> bytes:
    url = base.rstrip("/") + path
    headers = {}
    authorization = authorization_header(base, path, "GET")
    if authorization:
        headers["Authorization"] = authorization
    request = urllib.request.Request(url, headers=headers)
    try:
        with open_url(request, timeout=TIMEOUT) as r:
            return r.read()
    except (urllib.error.URLError, OSError) as exc:
        raise Unreachable(f"{path}: {exc}") from exc


def wait_for_agent(base: str, seconds: int = 30) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if http_json(base, "/api/health").get("ok"):
                return True
        except Unreachable:
            time.sleep(0.5)
    return False


def boot_stack(timeout: int = 180) -> dict:
    """Boot through the one-command entry point, not a private code path."""
    started = time.time()
    proc = subprocess.run(
        ["bash", str(ROOT / "scripts" / "ember_up.sh"), "--no-browser", "--json",
         "--no-smoke"],
        capture_output=True, text=True, timeout=timeout)
    payload = {}
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                break
            except ValueError:
                continue
    payload["boot_ms"] = round((time.time() - started) * 1000, 1)
    payload["boot_exit"] = proc.returncode
    if proc.returncode != 0:
        payload["boot_stderr"] = (proc.stderr or "")[-400:]
    return payload


def parsed_params(pairs: list[str]) -> dict:
    out: dict[str, object] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--param expects k=v, got {pair!r}")
        key, value = pair.split("=", 1)
        out[key] = value
    return out


def run_task(base: str, goal_type: str, params: dict, budget: float,
             evidence_dir: Path, boot: dict | None,
             wait_s: int = 30) -> tuple[dict, int]:
    t0 = time.time()
    try:
        if not wait_for_agent(base, seconds=wait_s):
            raise Unreachable(f"no healthy agent at {base}")

        submitted = time.time()
        record = http_json(base, "/api/run", {"goal_type": goal_type, "params": params})
        submitted_ms = round((time.time() - submitted) * 1000, 1)
        if record.get("error") and not record.get("run_id"):
            raise Unreachable(f"agent rejected the request: {record['error']}")
        run_id = record.get("run_id", "")
        status = record.get("status", "")
        cleanup = record.get("cleanup")
        if not isinstance(cleanup, dict):
            cleanup = {"status": "unreported", "warnings": []}
        cleanup_status = cleanup.get("status")
        cleanup_accepted = cleanup_status == "complete" or (
            cleanup_status == "not_applicable"
            and cleanup.get("reason") == "direct_live_agent_mode"
        )
        cleanup_failed = not cleanup_accepted

        fetched = time.time()
        evidence = http_json(base, f"/api/run/{run_id}") if run_id else {}
        evidence_ms = round((time.time() - fetched) * 1000, 1)

        # Independent artifact check: download the bytes and hash them here.
        artifact = None
        expected = None
        for obs in record.get("observations") or []:
            data = obs.get("data") or {}
            if data.get("sha256"):
                expected = data["sha256"]
                rel = data.get("path", "")
                raw = http_bytes(base, "/api/file?" + urllib.parse.urlencode(
                    {"run": run_id, "path": rel}))
                actual = hashlib.sha256(raw).hexdigest()
                artifact = {"path": rel, "bytes": len(raw),
                            "sha256": actual, "expected_sha256": expected,
                            "hash_verified": actual == expected,
                            "url": base.rstrip("/") + "/api/file?" + urllib.parse.urlencode(
                                {"run": run_id, "path": rel})}
                break

        checks = [c for ev in (record.get("evidence") or []) for c in (ev.get("checks") or {}).values()]
        total_ms = round((time.time() - t0) * 1000, 1)

        result = {
            "task": {"goal_type": goal_type, "params": params},
            "run_id": run_id,
            "status": status,
            "verified": status == "VERIFIED",
            "cleanup": cleanup,
            "cleanup_complete": cleanup_status == "complete",
            "cleanup_accepted": cleanup_accepted,
            "verifier_checks_passed": (bool(checks) and all(bool(c) for c in checks)),
            "claims": {
                "elapsed_ms": total_ms,
                "kernel_duration_ms": record.get("duration_ms"),
                "submit_ms": submitted_ms,
                "evidence_fetch_ms": evidence_ms,
                "boot_ms": (boot or {}).get("boot_ms"),
                "budget_ms": budget * 1000,
                "within_budget": total_ms <= budget * 1000,
            },
            "evidence_link": base.rstrip("/") + f"/api/run/{run_id}" if run_id else None,
            "artifact": artifact,
            "evidence_fetched": bool(evidence.get("status")) if evidence else False,
            "memory": record.get("memory"),
            "honest_notes": [
                "The 60 s budget covers this process: submit + evidence re-read + artifact re-hash.",
                "The artifact hash is recomputed here, not copied from the kernel's claim.",
                "Cleanup is reported separately; only complete or direct-mode not_applicable cleanup is accepted.",
                "An unreported cleanup status fails closed; a wrapper must report its prune outcome.",
                "DENIED is a correct kernel outcome — it is reported as a failure of the *task*.",
            ],
        }
    except Unreachable as exc:
        return ({"status": "UNREACHABLE", "error": str(exc),
                 "task": {"goal_type": goal_type, "params": params}}, 2)

    # ---- decide --------------------------------------------------------- #
    code = 0
    if not result["verified"] or not result["verifier_checks_passed"]:
        code = 1
    elif artifact is not None and not artifact["hash_verified"]:
        code = 1
    elif cleanup_failed:
        code = 1
    elif not result["claims"]["within_budget"]:
        code = 3

    result["exit_code"] = code
    result["verdict"] = {0: "VERIFIED-AND-WITHIN-BUDGET",
                         1: "TASK-FAILED",
                         2: "UNREACHABLE",
                         3: "OVER-BUDGET"}[code]

    if run_id:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        bundle = evidence_dir / f"task_{run_id}.json"
        bundle.write_text(json.dumps({"result": result, "record": record},
                                     ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
        try:
            result["bundle"] = str(bundle.relative_to(ROOT))
        except ValueError:            # --evidence-dir outside the repo
            result["bundle"] = str(bundle)
        bundle.write_text(json.dumps({"result": result, "record": record},
                                     ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    return result, code


def render(result: dict) -> str:
    claims = result.get("claims") or {}
    art = result.get("artifact") or {}
    lines = [
        f"REAL TASK — {result['verdict']}",
        "=" * 72,
        f"  run_id    : {result.get('run_id', '—')}",
        f"  goal      : {result['task']['goal_type']} {json.dumps(result['task']['params'])}",
        f"  status    : {result['status']}  (verifier checks all true: "
        f"{result.get('verifier_checks_passed')})",
        f"  elapsed   : {claims.get('elapsed_ms')} ms  "
        f"(budget {claims.get('budget_ms')} ms)",
    ]
    cleanup = result.get("cleanup") or {}
    cleanup_status = cleanup.get("status", "unreported")
    warnings = cleanup.get("warnings")
    if isinstance(warnings, list) and warnings:
        cleanup_status += " · " + ", ".join(str(item) for item in warnings)
    lines.append(f"  cleanup   : {cleanup_status}")
    if art:
        lines += [
            f"  artifact  : {art['path']} · {art['bytes']} bytes",
            f"  sha256    : {art['sha256']}",
            f"  hash check: {'MATCH (recomputed locally)' if art['hash_verified'] else 'MISMATCH'}",
            f"  download  : {art['url']}",
        ]
    lines += [
        f"  evidence  : {result.get('evidence_link')}",
        f"  bundle    : {result.get('bundle', '—')}",
        "=" * 72,
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="One real task, measured, with an evidence link.")
    ap.add_argument("--goal", default="create_file")
    ap.add_argument("--param", action="append", default=[],
                    help="k=v (repeatable)")
    ap.add_argument("--live-agent", default="http://127.0.0.1:8090")
    ap.add_argument("--budget", type=float, default=60.0,
                    help="wall-clock ceiling in seconds (T6 publishes 60)")
    ap.add_argument("--evidence-dir", default=str(DEFAULT_EVIDENCE_DIR))
    ap.add_argument("--wait", type=int, default=30,
                    help="seconds to wait for a healthy agent before giving up")
    ap.add_argument("--boot", action="store_true",
                    help="boot the stack first with scripts/ember_up.sh")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    params = parsed_params(args.param)
    if args.goal in ("create_file", "append_file") and "content" not in params:
        params["content"] = f"evidence-first task at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}"
    if args.goal in ("create_file", "append_file") and "path" not in params:
        params["path"] = "demo/hello-2027.txt"

    boot = boot_stack() if args.boot else None
    if boot is not None and boot.get("boot_exit") not in (0, None):
        print(json.dumps({"error": "boot failed", "boot": boot}) if args.json else
              f"boot failed: {boot}", file=sys.stderr)
        return 2

    result, code = run_task(args.live_agent, args.goal, params, args.budget,
                            Path(args.evidence_dir), boot, wait_s=args.wait)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(render(result))
    return code


if __name__ == "__main__":
    sys.exit(main())
