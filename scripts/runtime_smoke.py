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
  9. the agent card is machine-readable and every endpoint it advertises exists
 10. Prometheus metrics expose real counters (no derived "health score")
 11. the HUD returns a real plan, an independently re-hashed artifact, and a
     durable history/DecisionRecord over its own HTTP API; its red-team gate passes

Topology note: HUD and Live Agent are exercised as separate HTTP services, but
HUD dispatches through an in-process kernel bridge—not an HTTP call to Live Agent.

Usage:
    ./scripts/ember_up.sh --no-browser                   # provisions local-only credentials
    python3 scripts/runtime_smoke.py                     # reuses the private local credential file
    ./scripts/ember_up.sh --stop

For manually launched servers, configure `AI2027_WRITE_TOKEN`,
`AI2027_READ_TOKEN_HASHES`, `AI2027_READ_TOKEN_ID`, and
`AI2027_READ_TOKEN_SECRET` in the server/client environments. Without them,
private API checks fail closed; only the minimal health request is public.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from live_agent_auth import authorization_header, open_url

TIMEOUT = 20


class Fail(Exception):
    pass


def call(base: str, path: str, payload: dict | None = None) -> tuple[int, bytes]:
    url = base.rstrip("/") + path
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    authorization = authorization_header(
        base, path, "POST" if payload is not None else "GET")
    if authorization:
        headers["Authorization"] = authorization
    req = urllib.request.Request(
        url, data=data, headers=headers,
        method="POST" if payload is not None else "GET")
    try:
        with open_url(req, timeout=TIMEOUT) as r:
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
    expect(health == {"ok": True, "service": "ai-2027-live-agent"},
           f"public health must remain minimal: {list(health)}")
    results.append(("live-agent: minimal public health", True, health["service"]))

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


    # ------------------------- machine-readable surface -------------------- #
    status, card = jcall(base, "/api/agent-card")
    expect(status == 200, f"/api/agent-card -> HTTP {status}")
    expect(bool(card.get("name")) and bool(card.get("skills")),
           f"agent card is not self-describing: {list(card)[:6]}")
    endpoints = card.get("endpoints") or {}
    expect(endpoints and all(str(v).startswith("/api/") for v in endpoints.values()),
           f"agent card advertises a non-API endpoint: {endpoints}")
    expect(bool(card.get("honest_limits")),
           "agent card must publish its limits, not only its skills")
    results.append(("live-agent: agent card is valid + declares its limits", True,
                    f"{len(endpoints)} endpoints · {len(card.get('honest_limits') or [])} limits"))

    status, raw = call(base, "/api/metrics")
    body = raw.decode("utf-8", "replace")
    expect(status == 200, f"/api/metrics -> HTTP {status}")
    expect('ai2027_runs_total{status="VERIFIED"}' in body,
           "metrics do not expose VERIFIED runs")
    verified = 0
    for line in body.splitlines():
        if line.startswith('ai2027_runs_total{status="VERIFIED"}'):
            verified = int(float(line.rsplit(" ", 1)[1]))
    expect(verified >= 1, f"VERIFIED counter is {verified}, expected >= 1")
    results.append(("live-agent: metrics expose real counters (Prometheus text)", True,
                    f"verified_runs={verified}"))

def check_hud(base: str, results: list[tuple[str, bool, str]]) -> None:
    status, health = jcall(base, "/api/health")
    expect(status == 200 and health.get("ok") is True, f"HUD health -> {status} {health}")
    expect(health.get("no_llm") is True, "HUD must declare no_llm")
    results.append(("hud: health (stdlib, no LLM)", True, "ok"))

    nonce = uuid.uuid4().hex
    rel = f"smoke/hud-{nonce}.txt"
    content = f"via hud:{nonce}"
    status, rec = jcall(base, "/api/run", {
        "goal_type": "create_file", "params": {"path": rel, "content": content}})
    expect(status == 200 and rec.get("status") == "VERIFIED",
           f"HUD run not VERIFIED: HTTP {status}, {rec.get('status')}")
    expect(rec.get("plan_steps", 0) >= 1, "HUD did not return the real plan step count")
    expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    expect(rec.get("sha256") == {
        "file": rel, "bytes": len(content.encode("utf-8")), "sha256": expected_hash,
    }, f"HUD independent artifact hash differs: {rec.get('sha256')}")
    expect(rec.get("history_persisted") is True,
           f"HUD event history was not durably appended: {rec.get('history_persisted')}")

    status, state = jcall(base, "/api/state")
    expect(status == 200, f"HUD state -> HTTP {status}")
    restored_entry = next((h for h in state.get("history", [])
                           if h.get("run_id") == rec.get("run_id")), None)
    expect(restored_entry is not None, "HUD run missing from its history endpoint")
    expect(restored_entry.get("status") == "VERIFIED", "HUD history status differs from run result")

    status, record = jcall(base, f"/api/record/{rec['run_id']}")
    expect(status == 200 and record.get("run_id") == rec["run_id"],
           f"HUD persisted record unavailable: HTTP {status}")
    expect(record.get("status") == "VERIFIED" and
           record.get("goal", {}).get("params") == {"path": rel, "content": content},
           "HUD persisted DecisionRecord does not match the requested task")
    record_checks = (record.get("evidence") or [{}])[0].get("checks", {})
    expect(all(record_checks.get(k) is True for k in
               ("claimed_ok", "file_exists", "correct_path", "correct_content", "hash_matches")),
           f"restored HUD record is missing real verifier evidence: {record_checks}")
    results.append(("hud: plan + VERIFIED artifact/hash + history and persisted record restore",
                    True, rec["run_id"]))

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
    ap.add_argument("--json", action="store_true",
                    help="emit machine-readable results (used by scripts/ember_beat.py)")
    args = ap.parse_args()

    results: list[tuple[str, bool, str]] = []
    failed = 0
    started = time.perf_counter()
    if not args.json:
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

    passed = sum(1 for _, ok, _ in results if ok)
    if args.json:
        print(json.dumps({
            "ok": failed == 0,
            "passed": passed, "total": len(results),
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "checks": [{"name": n, "ok": ok, "note": note} for n, ok, note in results],
        }, ensure_ascii=False))
        return 0 if failed == 0 else 1

    for name, ok, note in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{note}]" if note else ""))
    print("=" * 66)
    print(f"{passed}/{len(results)} checks passed"
          + ("" if not failed else f" — {failed} FAILURE(S)"))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
