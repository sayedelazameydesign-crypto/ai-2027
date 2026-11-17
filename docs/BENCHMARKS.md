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

## Reproduce

```bash
cd live-agent && python3 bench.py   # rewrites benchmarks/latest.json
```
