#!/usr/bin/env python3
"""Regenerate ALL public claims from real measurements — one command.

"Build in public & proof-first" only works if the numbers in the article can
be reproduced by a stranger in one command. This script runs the actual
benchmarks, tests, red-team suite, audit and wheel build, then emits:

    launch/evidence/latest.json     (machine-readable, gitignored)
    launch/evidence/REPORT.md       (human-readable, gitignored)

The monitoring suites (watchdog 44 + phase2b 9) are mandatory gates, not
optional extras: the alert path is part of the evidence, not a side project.

Usage:
    python3 scripts/generate_launch_evidence.py            # regenerate + print summary
    python3 scripts/generate_launch_evidence.py --assert  # exit 1 if any gate fails
    python3 scripts/generate_launch_evidence.py --json    # print only the JSON

Stdlib only. Nothing under projects/ is modified.
"""
from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "launch" / "evidence"
PY = sys.executable

TESTS = [
    ("kernel", [PY, "-m", "unittest", "discover", "-s", "projects/agi-system/tests",
                "-t", "projects/agi-system"], r"Ran (\d+) tests"),
    ("live_agent_wrapper", [PY, "live-agent/test_server.py"], r"Ran (\d+) tests"),
    ("ci_guard_self_tests", [PY, "scripts/test_ci_guard.py"], r"Ran (\d+) tests"),
    ("public_demo_contract", ["node", "website/kernel-demo.test.js"],
     r"(\d+) demo contract tests passed"),
    ("command_hud_e2e", [PY, "command-hud/test_hud.py"], r"Ran (\d+) tests"),
    ("l1_memory", [PY, "memory/test_memory.py"], r"Ran (\d+) tests"),
    # Monitoring is NOT optional evidence: Phase 2A owns the hash chain and
    # Phase 2B owns the alerts derived from it. If either suite is broken, a
    # launch pack that only reports kernel/HUD numbers would still be green.
    ("watchdog", [PY, "monitoring/test_watchdog.py"], r"Ran (\d+) tests"),
    ("phase2b", [PY, "monitoring/test_phase2b.py"], r"Ran (\d+) tests"),
]


def run(cmd: list[str], timeout: int = 180) -> tuple[int, str, str, float]:
    """Return (exit_code, stdout, stderr, ms).

    Streams are kept SEPARATE on purpose: bench.py logs HTTP requests to
    stderr, so concatenating them makes its JSON unparseable. Discovered by
    the --assert gate on the first run.
    """
    t0 = time.perf_counter()
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout or "", r.stderr or "", round(
            (time.perf_counter() - t0) * 1000, 1)
    except FileNotFoundError as exc:
        return 127, "", str(exc), 0.0
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout {timeout}s", 0.0


def git(*args: str) -> str:
    rc, out, _, _ = run(["git", *args], timeout=20)
    return out.strip() if rc == 0 else "unknown"


def documented_kernel_test_count() -> int:
    """Read the published unittest count so evidence cannot drift from the README."""
    readme = ROOT / "projects" / "agi-system" / "README.md"
    try:
        text = readme.read_text(encoding="utf-8")
    except OSError:
        return 0
    match = re.search(
        r"python3\s+-m\s+unittest discover[^#\n]*#\s*(\d+)\s+اختبار وحدة",
        text,
    )
    return int(match.group(1)) if match else 0


def build() -> dict:
    d: dict = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_head": git("rev-parse", "--short", "HEAD"),
        "git_branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "environment": {"python": platform.python_version(),
                        "platform": platform.platform()},
        "method": "every number below was produced by an executed command in this run",
    }

    # 1) benchmarks
    rc, out, err, ms = run([PY, "live-agent/bench.py"])
    try:
        b = json.loads(out)
        k, f = b["kernel"], b["footprint"]
        d["kernel"] = {
            "verified": k["verified_create_file"],
            "denied": k["denied_traversal"],
            "failed": k["failed_read"],
            "http": b["http"]["run_goal_end_to_end"],
            "cold_import_ms": f["kernel_cold_import_ms"],
            "loc": f["core_source_loc"], "source_kb": f["core_source_kb"],
            "third_party_imports": f["third_party_imports"],
            "rss_growth_kb_over_70_runs": k["peak_rss_growth_kb_over_70_runs"],
            "bench_env": b["environment"],
        }
        d["bench_ms"] = ms
    except (ValueError, KeyError) as exc:
        d["kernel"] = {"error": f"bench.py parse failed: {exc}"}
    d["bench_exit"] = rc

    # 2) test suites
    tests = {}
    for name, cmd, pattern in TESTS:
        rc, out, err, ms = run(cmd)
        combined = out + err
        m = re.search(pattern, combined)
        tests[name] = {"count": int(m.group(1)) if m else 0,
                       "ok": rc == 0 and "OK" in combined.upper(),
                       "exit": rc, "ms": ms,
                       "evidence": (m.group(0) if m else combined.strip().splitlines()[-1][:120]
                                    if combined.strip() else "(no output)")}
    d["tests"] = tests

    # 3) zero-dependency AST gate
    rc, out, err, _ = run([PY, "scripts/ci_guard.py", "imports"])
    gate_out = out + err
    d["zero_deps"] = {"ok": rc == 0 and "PASS" in gate_out,
                      "evidence": next((l for l in gate_out.splitlines() if "PASS" in l),
                                       gate_out.strip()[:120])}

    # 4) red-team (real kernel, 100 attacks)
    rc, out, err, _ = run([PY, "command-hud/redteam.py"])
    try:
        r = json.loads(out)
        d["redteam"] = {k: r[k] for k in ("attacks", "expected_met", "denied",
                                          "contained", "breaches", "controls",
                                          "timing", "all_gates_pass",
                                          "portability_warning")}
    except ValueError:
        d["redteam"] = {"error": "redteam.py did not return JSON"}
    d["redteam_exit"] = rc

    # 5) repository audit (full, with 5 real probes)
    rc, out, err, _ = run([PY, "command-hud/audit.py"])
    try:
        a = json.loads(out)
        d["audit"] = {"summary": a["summary"], "repo_checks": a["repo_checks"],
                      "probes": [{"name": p["name"], "verdict": p["verdict"],
                                  "evidence": p["evidence"]} for p in a["probes"]]}
    except ValueError:
        d["audit"] = {"error": "audit.py did not return JSON"}
    d["audit_exit"] = rc

    # 6) wheel
    rc, out, err, _ = run(["bash", "packaging/agi-kernel/build_wheel.sh"])
    wheels = sorted((ROOT / "packaging/agi-kernel/dist").glob("*.whl"))
    d["wheel"] = {"build_ok": rc == 0 and bool(wheels),
                  "bytes": wheels[-1].stat().st_size if wheels else None,
                  "kb": round(wheels[-1].stat().st_size / 1024, 1) if wheels else None,
                  "name": wheels[-1].name if wheels else None}

    # 7) gates
    documented_test_count = documented_kernel_test_count()
    d["gates"] = {
        "bench_ran": d.get("bench_exit") == 0 and "error" not in d["kernel"],
        "all_test_suites_pass": all(t["ok"] and t["count"] > 0 for t in tests.values()),
        "kernel_tests_match_documented_count": (
            documented_test_count > 0
            and tests["kernel"]["count"] == documented_test_count
            and tests["kernel"]["ok"]
        ),
        "watchdog_44_tests": tests["watchdog"]["count"] == 44 and tests["watchdog"]["ok"],
        "phase2b_9_tests": tests["phase2b"]["count"] == 9 and tests["phase2b"]["ok"],
        "zero_third_party_imports": d["zero_deps"]["ok"],
        "redteam_no_breach": d.get("redteam", {}).get("breaches") == 0
                             and d.get("redteam", {}).get("all_gates_pass") is True,
        "redteam_controls_verified": d.get("redteam", {}).get("controls", {}).get("verified") == 10,
        "audit_probes_pass": d.get("audit", {}).get("summary", {}).get("probes_failed") == 0,
        "wheel_built": d["wheel"]["build_ok"],
        "wheel_under_64kb": bool(d["wheel"]["bytes"] and d["wheel"]["bytes"] < 64 * 1024),
    }
    d["all_gates_pass"] = all(d["gates"].values())
    return d


def markdown(d: dict) -> str:
    k, t, r = d["kernel"], d["tests"], d.get("redteam", {})
    L = ["# 📊 Launch Evidence Pack — regenerated from executed commands", "",
         f"**Generated:** {d['generated_at']} · **commit:** `{d['git_head']}` "
         f"(`{d['git_branch']}`) · **Python:** {d['environment']['python']}", "",
         "> " + d["method"] + ". No number here was typed by hand. "
         "Regenerate: `python3 scripts/generate_launch_evidence.py`", "",
         "## Measured kernel numbers", "", "| Measurement | n | p50 | p95 | max | spread |",
         "|---|---:|---:|---:|---:|---:|"]
    for label, s in [("VERIFIED create_file (full loop)", k.get("verified", {})),
                     ("DENIED path-traversal (policy wall)", k.get("denied", {})),
                     ("FAILED read (honest failure)", k.get("failed", {})),
                     ("Live Agent HTTP end-to-end", k.get("http", {}))]:
        if s:
            L.append(f"| {label} | {s['n']} | **{s['p50_ms']} ms** | {s['p95_ms']} ms | "
                     f"{s['max_ms']} ms | {s['spread_pct']}% |")
    L += ["",
          f"- Cold import: **{k.get('cold_import_ms')} ms** · "
          f"source: **{k.get('loc')} LOC / {k.get('source_kb')} KB** · "
          f"third-party imports: **{k.get('third_party_imports')}**",
          f"- Peak RSS growth over 70 consecutive runs: **{k.get('rss_growth_kb_over_70_runs')} KB**",
          f"- Wheel: **{d['wheel']['bytes']} bytes ({d['wheel']['kb']} KB)** — `{d['wheel']['name']}`",
          "", "## Executed test suites", "", "| Suite | Tests | Result | Time |",
          "|---|---:|---|---:|"]
    for name, v in t.items():
        L.append(f"| `{name}` | {v['count']} | {'✅ OK' if v['ok'] else '🔴 FAIL'} | {v['ms']} ms |")
    L += ["", "## Red-team (100 real adversarial inputs)", "",
          f"- DENIED: **{r.get('denied')}** · CONTAINED-as-literal: **{r.get('contained')}** · "
          f"**breaches: {r.get('breaches')}** · benign controls VERIFIED: "
          f"**{r.get('controls', {}).get('verified')}/{r.get('controls', {}).get('total')}**",
          f"- p50/attack: {r.get('timing', {}).get('p50_ms')} ms · "
          f"total: {r.get('timing', {}).get('total_ms')} ms", "",
          "> " + str(r.get("portability_warning", "")), "",
          "## Gates", ""]
    for name, ok in d["gates"].items():
        L.append(f"- {'✅' if ok else '🔴'} `{name}`")
    L += ["", f"**ALL GATES: {'PASS' if d['all_gates_pass'] else 'FAIL'}**", "",
          "## Reproduce", "", "```bash",
          "python3 scripts/generate_launch_evidence.py --assert",
          "python3 live-agent/bench.py",
          "python3 command-hud/redteam.py --markdown --assert",
          "python3 command-hud/audit.py --report docs/REPOSITORY-AUDIT.md --assert-probes",
          "python3 monitoring/test_watchdog.py -v",
          "python3 monitoring/test_phase2b.py -v",
          "```", ""]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--assert", dest="assert_gates", action="store_true")
    ap.add_argument("--json", action="store_true", help="print JSON only")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()
    d = build()
    if not args.no_write:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        (EVIDENCE / "latest.json").write_text(json.dumps(d, indent=2, ensure_ascii=False),
                                              encoding="utf-8")
        (EVIDENCE / "REPORT.md").write_text(markdown(d), encoding="utf-8")
    if args.json:
        print(json.dumps(d, indent=2, ensure_ascii=False))
    else:
        print(markdown(d))
    if args.assert_gates and not d["all_gates_pass"]:
        print("FAIL: one or more evidence gates did not pass", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
