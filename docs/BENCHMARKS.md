# 📊 Benchmarks — Evidence-First Kernel & Live Agent

**Date:** 2026-11-17 · **Method:** `live-agent/bench.py` (stdlib, reproducible) · **Raw data:** produced live, regenerated on every run

> **Honesty first (kimi-k3 rule):** timings were measured in a shared sandbox container
> (Linux 6.1, x86_64, Python 3.11.2). They are indicative, not an SLA — the spread is
> reported next to every number. Counts and byte sizes are exact, not estimates.

## Environment

| Key | Value |
|---|---|
| Python | 3.11.2 |
| Platform | Linux 6.1.158+ x86_64 (shared container) |
| Dependencies | **zero — Python stdlib only** (AST-verified import scan) |

## Results (measured, not claimed)

| Measurement | n | p50 | p95 | max | spread |
|---|---|---|---|---|---|
| Kernel: VERIFIED `create_file` run (full loop: plan→policy→execute→verify→evidence) | 50 | **7.5 ms** | 11.1 ms | 13.4 ms | 103% |
| Kernel: DENIED path-traversal (policy rejects before execution) | 10 | **3.1 ms** | 4.2 ms | 4.2 ms | 52% |
| Kernel: FAILED read (honest failure, evidence filed) | 10 | **6.2 ms** | 7.9 ms | 7.9 ms | 41% |
| Live Agent HTTP end-to-end (`POST /api/run`) | 20 | **10.0 ms** | 14.8 ms | 14.8 ms | 71% |

## Footprint (exact)

| Metric | Value |
|---|---|
| Kernel source | **3,526 LOC / 124.2 KB** (15 modules) |
| Third-party imports | **NONE** (AST scan: stdlib only) |
| Cold import (fresh subprocess, n=5) | **42.5 ms** |
| Peak RSS growth over 70 consecutive runs | **636 KB** total |
| Installable wheel | **40 KB** (`packaging/agi-kernel`, CLI smoke-tested: `doctor` + VERIFIED run) |

## Reading these numbers

- A full **verified** cognitive cycle — including independent re-check of the world and
  evidence persistence — costs single-digit milliseconds on a shared container CPU.
- **Denial is cheaper than execution** (3.1 ms): the policy wall fires before any work —
  the correct shape for a security boundary.
- 70 consecutive runs grow peak RSS by 636 KB total — no per-run leak pattern observed.
- Spread (jitter) is reported beside every timing; in a shared container it is dominated
  by CPU scheduling, not by the kernel.

## Re-run variance — published on purpose (2026-11-17)

The table above is the **quiet-container reference run**. Re-running the exact
same script on the same shared container at different load levels gave:

| Run | VERIFIED p50 | p95 | spread | DENIED p50 | HTTP p50 |
|---|---:|---:|---:|---:|---:|
| Reference (published above) | **7.5 ms** | 11.1 ms | 103% | 3.1 ms | 10.0 ms |
| Re-run A (loaded) | **12.1 ms** | 18.8 ms | 172% | 3.7 ms | 16.2 ms |
| Re-run B (later) | **8.1 ms** | 21.2 ms | 280% | 3.4 ms | 10.7 ms |
| Re-run C (evidence generator, `9ad27fc`) | **7.416 ms** | 10.143 ms | 94.9% | 2.964 ms | 8.443 ms |

Re-run C is the measurement used in [`launch/case-study-deterministic-core.md`](../launch/case-study-deterministic-core.md)
and [`launch/show-hn.md`](../launch/show-hn.md), produced by
`python3 scripts/generate_launch_evidence.py --assert` on 2026-11-17. Its cold
import was 40.56 ms and peak RSS growth 416 KB / 70 runs, against 42.5 ms and
636 KB in the reference table — same caveat, same code.

**This is the point, not a defect.** A single number quoted without its spread
would be marketing. The KPI "p50 < 10 ms" is a target measured on a defined
reference machine, **not an SLA** and not a claim about your hardware. CI
therefore does *not* hard-fail at 10 ms on shared runners — it records the raw
numbers on every push, uploads the JSON, and only fails above a 40 ms
catastrophic-regression ceiling (`scripts/ci_guard.py`).

Counts and byte sizes (3,526 LOC, 124.2 KB, 40 KB wheel, 86 tests) are exact and
do not vary. Only timings carry the jitter caveat.

## Reproduce

```bash
cd live-agent && python3 bench.py > /tmp/bench.json   # also rewrites benchmarks/latest.json
cd .. && python3 scripts/ci_guard.py imports
python3 scripts/ci_guard.py bench --input /tmp/bench.json
```

CI runs the same guard on every push to `main` and attaches the raw JSON as a
workflow artifact; the job summary prints the table so regressions are visible
without downloading anything.
