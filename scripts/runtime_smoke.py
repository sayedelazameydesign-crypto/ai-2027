#!/usr/bin/env python3
"""End-to-end smoke test against RUNNING servers (real HTTP, real kernel).

`live-agent/test_server.py` and `command-hud/test_hud.py` start the servers
in-process. This script hits a server that is already listening — i.e. exactly
what a human clicks — and fails loudly if any promise is not kept.

Covered promises:
  1. the service answers and declares its honest scope (no LLM)
  2. a normal goal really reaches VERIFIED, with all five verifier checks true
  3. the artifact can be downloaded and its bytes match what was requested
  4. the run record (evidence) is retrievable by run_id
  5. POSIX traversal is DENIED
  6. Windows-style traversal is DENIED (the 2026-11-17 hardening)
  7. a VERIFIED run is admitted to the evidence-gated memory store, and a
     refused (DENIED) run is NOT remembered — with the reason reported
  8. memory search finds the remembered run over real HTTP, and an unrelated
     query produces no confident hit (the noise floor is measured, not assumed)
  9. the HUD dispatches to the same kernel, and its 100-attack gate still passes

Usage:
    python3 live-agent/server.py --port 8090 &
    python3 command-hud/server.py --port 8091 &
    python3 scripts/runtime_smoke.py                       # both
    python3 scripts/runtime_smoke.py --no-hud              # live agent only
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 20


class Fail(Exception):
    pass


def call(base: str, path: str, payload: dict | None = None) -> tuple[int, bytes]:
    url = base.rstrip("/") + path
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:  # 4xx is a legitimate answer here
        return e.code, e.read()


def jcall(base: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    status, raw = call(base, path, payload)
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Fail(f"{path} did not return JSON ({status}): {raw[:120]!r}") from exc


def expect(cond: bool, msg: str) -> None:
    if not cond:
        raise Fail(msg)


def check_live_agent(base: str, results: list[tuple[str, bool, str]]) -> None:
    status, health = jcall(base, "/api/health")
    expect(status == 200 and health.get("ok") is True, f"/api/health -> {status} {health}")
    expect("NO LLM" in json.dumps(health), "health must declare the no-LLM scope")
    results.append(("live-agent: health + honest scope", True,
                    health.get("mode", "")[:70]))

    content = "runtime smoke @ 2026-11-17"
    status, rec = jcall(base, "/api/run", {
        "goal_type": "create_file",
        "params": {"path": "smoke/proof.txt", "content": content},
    })
    expect(status == 200, f"run -> HTTP {status}")
    expect(rec.get("status") == "VERIFIED", f"expected VERIFIED, got {rec.get('status')}: {rec.get('error')}")
    checks = (rec.get("evidence") or [{}])[0].get("checks", {})
    for key in ("claimed_ok", "file_exists", "correct_path", "correct_content", "hash_matches"):
        expect(checks.get(key) is True, f"verifier check {key} was not true: {checks}")
    run_id = rec["run_id"]
    results.append(("live-agent: create_file -> VERIFIED (5/5 checks)", True, run_id))

    status, raw = call(base, "/api/file?" + urllib.parse.urlencode(
        {"run": run_id, "path": "smoke/proof.txt"}))
    expect(status == 200, f"download -> HTTP {status}")
    expect(raw.decode() == content, f"downloaded bytes differ: {raw[:60]!r}")
    results.append(("live-agent: artifact download matches byte-for-byte", True,
                    f"{len(raw)} bytes"))

    status, rec2 = jcall(base, f"/api/run/{run_id}")
    expect(status == 200 and rec2.get("status") == "VERIFIED", "run record not retrievable")
    expect(bool(rec2.get("evidence")), "run record carries no evidence")
    results.append(("live-agent: evidence retrievable by run_id", True, run_id))

    status, denied = jcall(base, "/api/run", {
        "goal_type": "create_file", "params": {"path": "../../etc/pwned", "content": "x"}})
    reasons = denied.get("denial_reasons") or []
    expect(denied.get("status") == "DENIED", f"traversal was not DENIED: {denied.get('status')}")
    expect(any("escapes the sandbox" in r for r in reasons), f"unexpected denial reason: {reasons}")
    results.append(("live-agent: POSIX traversal -> DENIED", True, reasons[0][:60] + "…"))

    status, denied2 = jcall(base, "/api/run", {
        "goal_type": "create_file", "params": {"path": "..\\..\\pwned.txt", "content": "x"}})
    reasons2 = denied2.get("denial_reasons") or []
    expect(denied2.get("status") == "DENIED",
           f"windows-style traversal was not DENIED: {denied2.get('status')}")
    expect(any("cross-platform" in r for r in reasons2), f"unexpected reason: {reasons2}")
    results.append(("live-agent: Windows-style traversal -> DENIED", True, reasons2[0][:60] + "…"))

    # ---------------------------- memory store --------------------------- #
    status, stats = jcall(base, "/api/memory")
    expect(status == 200, f"/api/memory -> HTTP {status}")
    expect(stats.get("memories", 0) >= 1,
           f"no VERIFIED run was remembered: {stats.get('memories')} memories")
    expect(stats.get("refused_events", 0) >= 1,
           f"the DENIED run was not recorded as refused: {stats}")
    results.append(("live-agent: VERIFIED run admitted to memory, DENIED run refused",
                    True, f"memories={stats['memories']} refused={stats['refused_events']}"))

    mem_id = (rec.get("memory") or {}).get("memory_id")
    expect(bool(mem_id), f"run record carries no memory_id: {rec.get('memory')}")
    expect((denied.get("memory") or {}).get("admitted") is False,
           f"a DENIED run was admitted to memory: {denied.get('memory')}")

    q = urllib.parse.urlencode({"q": "runtime smoke proof txt", "k": 3})
    status, found = jcall(base, "/api/memory/search?" + q)
    expect(status == 200, f"memory search -> HTTP {status}")
    top = (found.get("results") or [{}])[0]
    expect(top.get("memory_id") == mem_id,
           f"search did not return the remembered run: {top.get('memory_id')} != {mem_id}")
    expect(top.get("above_noise") is True,
           f"the exact-content hit did not clear the noise floor: {found.get('threshold')}")
    results.append(("live-agent: memory search returns the real run above noise", True,
                    f"score={top.get('score')} threshold={found.get('threshold')}"))

    status, unrelated = jcall(base, "/api/memory/search?" + urllib.parse.urlencode(
        {"q": "kubernetes cluster autoscaling", "k": 3}))
    expect(status == 200, f"CJK-free unrelated search -> HTTP {status}")
    expect(unrelated.get("confident") == 0,
           f"unrelated query produced confident hits: {unrelated.get('results')}")
    results.append(("live-agent: unrelated query -> no confident hit (honest recall)",
                    True, f"top_score={(unrelated.get('results') or [{}])[0].get('score')}"))


def check_hud(base: str, results: list[tuple[str, bool, str]]) -> None:
    status, health = jcall(base, "/api/health")
    expect(status == 200 and health.get("ok") is True, f"HUD health -> {status} {health}")
    expect(health.get("no_llm") is True, "HUD must declare no_llm")
    results.append(("hud: health (stdlib, no LLM)", True, "ok"))

    status, rec = jcall(base, "/api/run", {
        "goal_type": "create_file", "params": {"path": "smoke/hud.txt", "content": "via hud"}})
    expect(rec.get("status") == "VERIFIED", f"HUD run not VERIFIED: {rec.get('status')}")
    results.append(("hud: dispatch -> real kernel VERIFIED", True, rec["run_id"]))

    status, esc = jcall(base, "/api/escape", {})
    expect(status == 200 and esc.get("status") == "DENIED", f"HUD escape -> {esc.get('status')}")
    results.append(("hud: built-in escape test -> DENIED", True, "ok"))

    status, rt = jcall(base, "/api/redteam", {})
    expect(status == 200, f"redteam -> HTTP {status}")
    expect(rt.get("attacks") == 100, f"expected 100 attacks, got {rt.get('attacks')}")
    expect(rt.get("breaches") == 0, f"BREACHES: {rt.get('breach_files')}")
    expect(rt.get("all_gates_pass") is True, f"red-team gates failed: {rt.get('gates')}")
    results.append(("hud: 100-attack gate (0 breaches, all gates)", True,
                    f"denied={rt.get('denied')} contained={rt.get('contained')}"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live-agent", default="http://127.0.0.1:8090")
    ap.add_argument("--hud", default="http://127.0.0.1:8091")
    ap.add_argument("--no-hud", action="store_true", help="skip the HUD checks")
    args = ap.parse_args()

    results: list[tuple[str, bool, str]] = []
    failed = 0
    print("RUNTIME SMOKE — real HTTP against running servers\n" + "=" * 66)
    try:
        check_live_agent(args.live_agent, results)
    except Fail as exc:
        failed += 1
        results.append((f"live-agent: FAILED — {exc}", False, ""))
    if not args.no_hud:
        try:
            check_hud(args.hud, results)
        except Fail as exc:
            failed += 1
            results.append((f"hud: FAILED — {exc}", False, ""))

    for name, ok, note in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{note}]" if note else ""))
    passed = sum(1 for _, ok, _ in results if ok)
    print("=" * 66)
    print(f"{passed}/{len(results)} checks passed"
          + ("" if not failed else f" — {failed} FAILURE(S)"))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
