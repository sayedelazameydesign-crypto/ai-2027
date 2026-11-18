#!/usr/bin/env python3
"""Run one verified local browser task end-to-end (Stage 1 · Mode 1).

Matches the discipline of `scripts/run_task.py`:
  1. preflight `probe.py` (no `bsk` / daemon / browser -> exit 2 SKIPPED,
     permission block -> exit 3 BLOCKED — never faked as success or crash);
  2. `DomainPolicy` checks the URL BEFORE opening any Agent Window;
  3. `ApprovalEngine` issues a single-use grant for the one `navigate` mutation;
  4. `with adapter.session(...)` opens a background Agent Window (`--no-focus`),
     navigates, runs `sync()`, captures `observe` (or `extract` / `screenshot`),
     and ALWAYS executes `bsk session stop` in `finally`;
  5. `verify_and_record()` independently re-reads the saved evidence file,
     recomputes SHA-256, checks all 7 gates, and writes `record.json`;
  6. optional `--memory-db PATH` offers the record to `memory/store.py` (L1),
     which admits ONLY `VERIFIED` + matching on-disk SHA-256;
  7. enforces a falsifiable wall-clock budget (default 60 s).

Exit codes:
    0  VERIFIED and within budget
    1  DENIED, FAILED, or INCONCLUSIVE
    2  SKIPPED (`bsk`, daemon, or browser extension not connected)
    3  BLOCKED (permission error) or exceeded `--budget`

Usage:
    python3 browser_skill_adapter/run_local_task.py --url https://example.com
    python3 browser_skill_adapter/run_local_task.py --url http://127.0.0.1:8090/ --allow-localhost --json
    python3 browser_skill_adapter/run_local_task.py --url https://example.com --incognito --capture extract
    python3 browser_skill_adapter/run_local_task.py --url https://example.com --memory-db browser_skill_adapter/runtime/memory.db

Stdlib only. Python 3.10+.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from adapter import (  # noqa: E402
    BskCommandError,
    BrowserSkillAdapter,
    DomainPolicy,
    PolicyError,
)
from probe import BLOCKED, OK, SKIPPED, run_probe  # noqa: E402

OVER_BUDGET = 3


def execute_local_task(
    url: str,
    *,
    capture: str = "observe",
    browser: str | None = None,
    incognito: bool = False,
    allow_localhost: bool = False,
    allow_http: bool = False,
    budget_s: float = 60.0,
    bsk_bin: str | Path | None = None,
    bsk_home: str | Path | None = None,
    sandbox_root: str | Path | None = None,
    memory_db: str | Path | None = None,
    runner: Any | None = None,
) -> tuple[int, dict[str, Any]]:
    """Run one policy-gated local browser task and return `(exit_code, bundle)`."""
    t0 = time.perf_counter()
    budget_ms = round(float(budget_s) * 1000.0, 1)

    domain = DomainPolicy(allow_http=allow_http, allow_localhost=allow_localhost)
    adapter = BrowserSkillAdapter(
        mode="local",
        runner=runner,
        domain_policy=domain,
        sandbox_root=sandbox_root,
        bsk_bin=bsk_bin,
        bsk_home=bsk_home,
    )

    # Policy wall FIRST: a forbidden URL (file://, metadata IP) is DENIED even
    # without touching the browser daemon.
    try:
        origin = domain.check_url(url)
    except PolicyError as exc:
        sess = adapter.session(browser=browser, incognito=incognito, name="ai2027-local-task")
        sess.denied_reason = str(exc)
        rec = sess.verify_and_record(
            goal_type=f"browser_{capture}",
            artifact_rel_path="evidence/capture.json",
            extra_params={"origin": "denied"},
        )
        mem_verdict = _maybe_ingest_memory(memory_db, adapter.sandbox_root, rec)
        return 1, _pack_bundle(rec, None, t0, budget_ms, mem_verdict, verdict="DENIED")

    # Preflight second: distinguish SKIPPED / BLOCKED from FAILED.
    pre = run_probe(bsk_bin=bsk_bin, bsk_home=bsk_home, runner=adapter.runner)
    if pre["exit_code"] != OK:
        code = int(pre["exit_code"])
        return code, {
            "status": pre["verdict"],
            "verified": False,
            "mode": "local",
            "stage": "1/3",
            "origin": origin,
            "probe": pre,
            "claims": {
                "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 1),
                "budget_ms": budget_ms,
                "within_budget": True,
            },
        }

    artifact_rel = "evidence/screenshot.png" if capture == "screenshot" else "evidence/capture.json"
    declared_sha: str | None = None
    summary_text = ""
    sess = adapter.session(browser=browser, incognito=incognito, focus=False,
                           name="ai2027-local-task")
    nav_grant = adapter.approvals.issue("navigate", url, approved_by="run_local_task")

    try:
        with sess:
            nav = sess.navigate(url, grant=nav_grant)
            if nav["status"] == "ok":
                synced = sess.sync()
                if capture == "observe":
                    obs = sess.observe()
                    _, declared_sha = sess.save_evidence_json(artifact_rel, {
                        "origin": origin,
                        "sync_context_present": bool(synced.get("context_id")),
                        "observation": obs["envelope"],
                    })
                    data = obs["envelope"].get("data") or {}
                    title = data.get("title") if isinstance(data, dict) else ""
                    summary_text = f"browser observe {origin} {title or ''}".strip()
                elif capture == "extract":
                    ext = sess.extract()
                    _, declared_sha = sess.save_evidence_json(artifact_rel, {
                        "origin": origin,
                        "extraction": ext["envelope"],
                    })
                    summary_text = f"browser extract {origin}"
                elif capture == "screenshot":
                    sess.screenshot(artifact_rel)
                    summary_text = f"browser screenshot {origin}"
                else:
                    raise PolicyError(f"unsupported capture mode {capture!r}")
    except (PolicyError, BskCommandError) as exc:
        if isinstance(exc, PolicyError):
            sess.denied_reason = str(exc)
        else:
            summary_text = str(exc)[:240]

    rec = sess.verify_and_record(
        goal_type=f"browser_{capture}",
        artifact_rel_path=artifact_rel,
        declared_sha256=declared_sha,
        extra_params={"origin": origin, "capture": capture},
        summary_text=summary_text,
    )
    rec_path = sess.sandbox / "record.json"
    rec_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    mem_verdict = _maybe_ingest_memory(memory_db, adapter.sandbox_root, rec)
    bundle = _pack_bundle(rec, rec_path, t0, budget_ms, mem_verdict, verdict=rec["status"])

    if rec["status"] != "VERIFIED":
        return 1, bundle
    if not bundle["claims"]["within_budget"]:
        return OVER_BUDGET, bundle
    return 0, bundle


def _maybe_ingest_memory(memory_db: str | Path | None, sandbox_root: Path,
                         rec: dict[str, Any]) -> dict[str, Any] | None:
    if not memory_db:
        return None
    sys.path.insert(0, str(ROOT))
    from memory.store import EvidenceMemoryStore  # local stdlib package
    store = EvidenceMemoryStore(Path(memory_db), sandbox_root=sandbox_root)
    v = store.ingest(rec)
    return {
        "admitted": v.admitted,
        "reason": v.reason,
        "memory_id": v.memory_id,
        "trust": v.trust,
        "artifact_verified": v.artifact_verified,
        "duplicate": v.duplicate,
    }


def _pack_bundle(rec: dict[str, Any], rec_path: Path | None, t0: float,
                 budget_ms: float, mem_verdict: dict[str, Any] | None,
                 *, verdict: str) -> dict[str, Any]:
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    within = elapsed_ms <= budget_ms
    obs_data = ((rec.get("observations") or [{}])[0] or {}).get("data") or {}
    checks = ((rec.get("evidence") or [{}])[0] or {}).get("checks") or {}
    return {
        "run_id": rec["run_id"],
        "status": verdict,
        "verified": bool(rec.get("verified")) and within,
        "mode": rec["mode"],
        "stage": f"{rec['stage']}/3",
        "session_closed": bool((rec.get("session") or {}).get("closed")),
        "artifact": {
            "path": obs_data.get("path"),
            "bytes": obs_data.get("bytes"),
            "sha256": obs_data.get("sha256"),
            "hash_verified": bool(checks.get("hash_matches")),
        },
        "checks": checks,
        "record_path": str(rec_path) if rec_path else None,
        "memory": mem_verdict,
        "claims": {
            "elapsed_ms": elapsed_ms,
            "budget_ms": budget_ms,
            "within_budget": within,
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="https://example.com", help="target URL to inspect")
    ap.add_argument("--capture", choices=("observe", "extract", "screenshot"),
                    default="observe")
    ap.add_argument("--browser", help="explicit browser instance_id or label")
    ap.add_argument("--incognito", action="store_true",
                    help="open an isolated incognito Agent Window")
    ap.add_argument("--allow-localhost", action="store_true",
                    help="permit http://127.0.0.1 / localhost (for debugging live-agent/HUD)")
    ap.add_argument("--allow-http", action="store_true",
                    help="permit plaintext http:// on non-loopback hosts")
    ap.add_argument("--budget", type=float, default=60.0,
                    help="wall-clock budget in seconds (exit 3 if exceeded)")
    ap.add_argument("--bsk-bin", help="explicit path to `bsk` binary")
    ap.add_argument("--bsk-home", help="explicit BSK_HOME directory")
    ap.add_argument("--sandbox-root", help="override per-run evidence sandbox root")
    ap.add_argument("--memory-db", help="optional L1 sqlite DB to offer the DecisionRecord to")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args(argv)

    code, bundle = execute_local_task(
        args.url,
        capture=args.capture,
        browser=args.browser,
        incognito=args.incognito,
        allow_localhost=args.allow_localhost,
        allow_http=args.allow_http,
        budget_s=args.budget,
        bsk_bin=args.bsk_bin,
        bsk_home=args.bsk_home,
        sandbox_root=args.sandbox_root,
        memory_db=args.memory_db,
    )
    if args.json:
        print(json.dumps(bundle, ensure_ascii=False, indent=2))
    else:
        c = bundle["claims"]
        print(f"BROWSERSKILL TASK — status={bundle['status']} · verified={bundle['verified']} · "
              f"{c['elapsed_ms']} ms (budget {c['budget_ms']} ms) · exit={code}")
        if bundle.get("artifact"):
            a = bundle["artifact"]
            print(f"  artifact : {a['path']} · bytes={a['bytes']} · "
                  f"hash_verified={a['hash_verified']} · sha256={a['sha256']}")
        if bundle.get("record_path"):
            print(f"  evidence : {bundle['record_path']}")
        if bundle.get("memory"):
            m = bundle["memory"]
            print(f"  memory   : admitted={m['admitted']} · trust={m['trust']} · {m['reason']}")
        if bundle.get("probe"):
            print(f"  next     : {bundle['probe'].get('next_action')}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
