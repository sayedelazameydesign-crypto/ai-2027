#!/usr/bin/env python3
"""CI gates for the AI-2027 evidence-first kernel.

Stdlib only, no third-party imports — the guard must be as dependency-free as
the thing it guards. Run it locally before pushing:

    python3 scripts/ci_guard.py imports
    python3 live-agent/bench.py > /tmp/bench.json
    python3 scripts/ci_guard.py bench --input /tmp/bench.json

Subcommands
-----------
imports : AST scan of the kernel + live-agent. Fails on ANY third-party import.
bench   : validates a bench.py JSON payload, enforces a *catastrophic*
          regression ceiling, and writes a Markdown table to
          $GITHUB_STEP_SUMMARY when running inside GitHub Actions.

Why the ceiling is 40 ms and not the 10 ms KPI
----------------------------------------------
The published p50 (7.5 ms) was measured on one shared sandbox container and
jitter is deliberately published beside every timing: a re-run on a loaded
container produced 12.1 ms with 172% spread. GitHub-hosted runners are a
different machine entirely. A hard 10 ms gate would therefore be a flaky,
dishonest test. This gate catches orders-of-magnitude regressions only; the
real numbers are recorded as an artifact and printed in the job summary on
every run so a human can compare them.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KERNEL_DIR = ROOT / "projects" / "agi-system" / "core"
LIVE_DIR = ROOT / "live-agent"
FIRST_PARTY = {"core", "server", "agi_kernel"}
DEFAULT_CEILING_MS = 40.0
EXPECTED_IMPORT_PROOF = "NONE (stdlib only)"


def scan_imports() -> int:
    stdlib = set(sys.stdlib_module_names)
    files = sorted(KERNEL_DIR.glob("*.py")) + [LIVE_DIR / "server.py", LIVE_DIR / "bench.py"]
    bad: dict[str, set[str]] = {}
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name not in stdlib and name not in FIRST_PARTY:
                    bad.setdefault(str(path.relative_to(ROOT)), set()).add(name)

    print(f"AST-scanned {len(files)} Python files for third-party imports.")
    if bad:
        for file, modules in sorted(bad.items()):
            print(f"  THIRD-PARTY  {file}: {', '.join(sorted(modules))}", file=sys.stderr)
        print("FAIL: the kernel must stay zero-dependency.", file=sys.stderr)
        return 1
    print("PASS: stdlib only (zero third-party imports).")
    return 0


def _table(data: dict) -> str:
    k = data["kernel"]
    rows = [
        ("VERIFIED create_file (full loop)", k["verified_create_file"]),
        ("DENIED path-traversal (policy wall)", k["denied_traversal"]),
        ("FAILED read (honest failure)", k["failed_read"]),
        ("Live Agent HTTP end-to-end", data["http"]["run_goal_end_to_end"]),
    ]
    lines = [
        "## 📊 Measured on this runner (indicative, not an SLA)",
        "",
        "| Measurement | n | p50 | p95 | max | spread |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, s in rows:
        lines.append(
            f"| {label} | {s['n']} | **{s['p50_ms']} ms** | {s['p95_ms']} ms | "
            f"{s['max_ms']} ms | {s['spread_pct']}% |"
        )
    fp = data["footprint"]
    lines += [
        "",
        f"- Environment: Python `{data['environment']['python']}` · `{data['environment']['platform']}`",
        f"- Kernel source: **{fp['core_source_loc']} LOC / {fp['core_source_kb']} KB**",
        f"- Third-party imports: **{fp['third_party_imports']}**",
        f"- Cold import: {fp['kernel_cold_import_ms']} ms · "
        f"peak RSS growth over 70 runs: {k['peak_rss_growth_kb_over_70_runs']} KB",
        f"- Catastrophic-regression ceiling: p50 < {DEFAULT_CEILING_MS} ms "
        "(the 10 ms KPI is measured on the reference machine, not enforced on shared CI runners)",
        "",
        "Reference numbers (different machine): [docs/BENCHMARKS.md](docs/BENCHMARKS.md). "
        "Jitter is published beside every timing — never quote a single number as an SLA.",
    ]
    return "\n".join(lines)


def check_benchmarks(input_path: Path, ceiling_ms: float) -> int:
    data = json.loads(input_path.read_text(encoding="utf-8"))
    errors: list[str] = []

    try:
        k = data["kernel"]
        fp = data["footprint"]
        http = data["http"]["run_goal_end_to_end"]
        p50 = k["verified_create_file"]["p50_ms"]
        n = k["verified_create_file"]["n"]
    except (KeyError, TypeError) as exc:
        print(f"FAIL: bench.py payload is missing a key: {exc}", file=sys.stderr)
        return 1

    if fp.get("third_party_imports") != EXPECTED_IMPORT_PROOF:
        errors.append(f"third_party_imports = {fp.get('third_party_imports')!r}, expected {EXPECTED_IMPORT_PROOF!r}")
    if not isinstance(fp.get("core_source_loc"), int) or fp["core_source_loc"] <= 0:
        errors.append("core_source_loc must be a positive exact count")
    if n < 50:
        errors.append(f"benchmark sample size n={n} is below the 50-run method")
    for label, sample in (("verified_create_file", k["verified_create_file"]),
                          ("denied_traversal", k["denied_traversal"]),
                          ("failed_read", k["failed_read"]),
                          ("http", http)):
        if not (0 < sample["p50_ms"] <= sample["max_ms"]):
            errors.append(f"{label}: implausible stats (p50/max out of order)")
    if not (0 < p50 <= ceiling_ms):
        errors.append(f"VERIFIED p50 = {p50} ms exceeds the catastrophic ceiling {ceiling_ms} ms")

    summary = _table(data)
    print(summary)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(summary + "\n")

    if errors:
        for err in errors:
            print(f"FAIL: {err}", file=sys.stderr)
        return 1
    print(f"PASS: measured p50 {p50} ms (n={n}), zero deps, ceiling {ceiling_ms} ms.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("imports", help="AST zero-dependency gate")
    bench = sub.add_parser("bench", help="benchmark payload + regression gate")
    bench.add_argument("--input", type=Path, required=True, help="bench.py JSON output")
    bench.add_argument("--ceiling-ms", type=float, default=DEFAULT_CEILING_MS)
    args = parser.parse_args()
    if args.command == "imports":
        return scan_imports()
    return check_benchmarks(args.input, args.ceiling_ms)


if __name__ == "__main__":
    raise SystemExit(main())
