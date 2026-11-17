# Case Study (DRAFT — EN) — for DEV.to / Medium / HN
> Gate: publish only after the live demo URL exists. Every number below was measured
> this week and is reproducible from the repo (`live-agent/bench.py`).

---

## I built an AI agent core that is not allowed to say "done" — in 3.5k lines of dependency-free Python

Most agent frameworks answer a simple question badly: *how do you know the task actually happened?*
The LLM said so. The tool returned 200. The log looks fine.

I wanted the opposite contract: **a run reaches VERIFIED only after an independent verifier
re-checks the world** — the executor's own claim is never evidence. So I wrote a kernel with
one rule: *no claims without evidence.*

### The loop

```
Goal → Planner → Policy → Executor → Verifier → Evidence → DecisionRecord → EventLedger
```

- **Policy before execution.** Ask the kernel to write `../../escape.txt` and you get
  `DENIED` with a recorded reason ("path escapes the sandbox") — in **3.1 ms**, because the
  wall fires *before* any work. That's the correct shape for a security boundary.
- **Independent verification.** A file write is only VERIFIED after the verifier re-reads
  the filesystem: `file_exists`, `correct_path`, `correct_content`, `hash_matches` (sha256).
- **Honest failure.** A read of a missing file returns FAILED with evidence — the kernel
  structurally cannot dress a failure as a success; every failure is a terminal status
  plus a ledger event.
- **A tamper-evident ledger.** Every decision lands in an append-only JSONL ledger you can
  `replay`, `resume`, and `verify` from the CLI.

### The numbers (measured, reproducible)

| What | Result |
|---|---|
| Full verified cycle (plan→verify→evidence) | **p50 7.5 ms** (n=50, shared container) |
| Denial at the policy wall | **p50 3.1 ms** |
| Source size | **3,526 LOC / 124 KB**, 15 modules |
| Third-party dependencies | **zero** — stdlib only, AST-verified |
| Cold import | **42.5 ms** (fresh subprocess) |
| Test suite | **86/86 passing** |
| Installable wheel | **40 KB**, CLI smoke-tested |

Spread/jitter is published next to every timing — a measurement you can't reproduce is a
marketing claim, not a benchmark.

### Why zero dependencies matters

Every dependency is a supply-chain decision you didn't make. The kernel imports nothing but
the Python standard library, so auditing the trust surface means auditing 3.5k lines you
can read in an afternoon. It also runs anywhere Python 3.10+ runs — no build step, no GPU,
no network.

### What it deliberately is not (yet)

The planner is **deterministic and rule-based — no LLM in the loop**. That's a feature of
this stage: the execution discipline had to be proven before a probabilistic brain is
allowed anywhere near it. Phase 2 connects a real model (free-tier API or a local
disk-streamed engine) *behind the same evidence contract* — the verifier doesn't care what
produced the plan.

Everything is open for inspection: kernel, tests, the live web demo wrapper, and the raw
benchmark JSON. Break it and tell me where — that's the point.

---

*Author & rights holder: [Sayed El-Azamey (سيد العزامي)](https://www.facebook.com/sayed.elazamy) — independent AI systems builder, Egypt. Building in public.*
