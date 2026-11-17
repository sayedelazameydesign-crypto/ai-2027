#!/usr/bin/env python3
"""Reproducible micro-benchmarks for the AI-2027 evidence-first kernel + live server.

Methodology (adopted from the kimi-k3-in-c playbook):
  * measure the NOISE FLOOR first: N repetitions, report min/p50/p95/max + spread
  * counts and byte sizes are exact; only timings carry the jitter caveat
  * environment is printed so numbers are never quoted without context

Run:  python3 bench.py            # writes benchmarks/latest.json + prints summary
Stdlib only.
"""
from __future__ import annotations

import ast
import json
import os
import platform
import resource
import statistics
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "projects" / "agi-system"))

N_KERNEL = 50
N_HTTP = 20


def _stats(samples: list[float]) -> dict:
    s = sorted(samples)
    n = len(s)
    p50 = statistics_median = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    p95 = s[min(n - 1, int(0.95 * n))]
    return {
        "n": n,
        "min_ms": round(min(s) * 1000, 3),
        "p50_ms": round(p50 * 1000, 3),
        "p95_ms": round(p95 * 1000, 3),
        "max_ms": round(max(s) * 1000, 3),
        "mean_ms": round(statistics.mean(s) * 1000, 3),
        "spread_pct": round((max(s) - min(s)) / min(s) * 100, 1),
    }


def measure_kernel() -> dict:
    from core.kernel import Kernel, KernelConfig
    from core.models import Goal

    tmp = tempfile.mkdtemp(prefix="k3bench-")
    ok_lat, denied_lat, failed_lat = [], [], []
    peak_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    for i in range(N_KERNEL):
        sb = Path(tmp) / f"r{i}"
        k = Kernel(sandbox_dir=sb, ledger_path=sb / "l.jsonl", config=KernelConfig())
        t0 = time.perf_counter()
        k.run(Goal("create_file", {"path": "f.txt", "content": "bench"}))
        ok_lat.append(time.perf_counter() - t0)
    for i in range(10):
        sb = Path(tmp) / f"d{i}"
        k = Kernel(sandbox_dir=sb, ledger_path=sb / "l.jsonl", config=KernelConfig())
        t0 = time.perf_counter()
        k.run(Goal("create_file", {"path": "../escape", "content": "x"}))
        denied_lat.append(time.perf_counter() - t0)
    for i in range(10):
        sb = Path(tmp) / f"f{i}"
        k = Kernel(sandbox_dir=sb, ledger_path=sb / "l.jsonl", config=KernelConfig())
        t0 = time.perf_counter()
        k.run(Goal("read_file", {"path": "no/such.txt"}))
        failed_lat.append(time.perf_counter() - t0)
    peak_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "verified_create_file": _stats(ok_lat),
        "denied_traversal": _stats(denied_lat),
        "failed_read": _stats(failed_lat),
        "peak_rss_growth_kb_over_70_runs": int(peak_after - peak_before),
    }


def measure_import_and_footprint() -> dict:
    # COLD import: measured in a fresh subprocess, never in-process (warm = lying)
    import subprocess
    code = ("import sys,time;sys.path.insert(0,sys.argv[1]);t0=time.perf_counter();"
            "import core.kernel;print((time.perf_counter()-t0)*1000)")
    samples = [float(subprocess.check_output(
        [sys.executable, "-c", code, str(HERE.parent / "projects" / "agi-system")],
        text=True).strip()) for _ in range(5)]
    import_ms = round(statistics.mean(samples), 2)
    src = HERE.parent / "projects" / "agi-system" / "core"
    loc = sum(len(p.read_text(encoding="utf-8").splitlines()) for p in src.glob("*.py"))
    size_kb = round(sum(p.stat().st_size for p in src.glob("*.py")) / 1024, 1)
    # zero-dependency proof: collect every imported top-level module, check stdlib
    stdlib = set(sys.stdlib_module_names)
    third_party = set()
    for p in list(src.glob("*.py")) + [HERE / "server.py"]:
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            name = None
            if isinstance(node, ast.Import):
                for a in node.names:
                    name = a.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                name = node.module.split(".")[0]
            # "memory" is repo-local (memory/store.py), not a dependency.
            if name and name not in stdlib and not name.startswith(("core", "server", "memory")):
                third_party.add(name)
    return {
        "kernel_cold_import_ms": import_ms,
        "core_source_loc": loc,
        "core_source_kb": size_kb,
        "third_party_imports": sorted(third_party) or "NONE (stdlib only)",
    }


def measure_http() -> dict:
    import server as srv

    srv._fresh_state()  # ensure runs/ + sandboxes/ exist (main() does this normally)
    httpd = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    lat = []
    body = json.dumps({"goal_type": "create_file",
                       "params": {"path": "b.txt", "content": "x"}}).encode()
    for _ in range(N_HTTP):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/run", data=body,
            headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        with urllib.request.urlopen(req) as r:
            r.read()
        lat.append(time.perf_counter() - t0)
    httpd.shutdown()
    return {"run_goal_end_to_end": _stats(lat)}


def main() -> None:
    lat: list[float]
    out = {
        "benchmark": "ai-2027 evidence-first kernel + live-agent",
        "date": time.strftime("%Y-%m-%d"),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "note": "shared sandbox container; timings are indicative, counts are exact",
        },
        "kernel": measure_kernel(),
        "footprint": measure_import_and_footprint(),
        "http": measure_http(),
    }
    dest = HERE / "benchmarks"
    dest.mkdir(exist_ok=True)
    (dest / "latest.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
