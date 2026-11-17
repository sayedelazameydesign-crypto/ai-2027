# Case Study — READY TO PUBLISH (DEV.to / Medium / HN / personal blog)

> **Every number in this article was re-measured on 2026-11-17 at commit `9ad27fc`**
> by `python3 scripts/generate_launch_evidence.py --assert`. Python 3.11.2,
> Linux 6.1 x86_64, shared container. Regenerate before republishing — timings
> are measurements, not promises. Full machine-readable run: `launch/evidence/latest.json`.
>
> **Try it live:** <https://sayedelazameydesign-crypto.github.io/ai-2027/demo.html>
> **Repo:** <https://github.com/sayedelazameydesign-crypto/ai-2027>

---

## I built an AI agent core that is not allowed to say "done" — 3,526 lines, zero dependencies

Most agent frameworks answer one question badly: *how do you know the task actually happened?*

The model said so. The tool returned 200. The log looks fine.

I wanted the opposite contract: **a run reaches `VERIFIED` only after an independent verifier re-reads the world.** The executor's own report is never evidence. So I wrote a kernel with one rule: *no claims without evidence.*

This is a technical case study, not a launch post. It includes the numbers, the method, and — more importantly — the things I got wrong.

---

## 1. The loop

```
Goal → Planner → Policy → Executor → Verifier → Evidence → DecisionRecord → EventLedger
```

One responsibility per component. The **Policy** engine is the only component allowed to authorise an action; the executor never checks permissions and the verifier never checks policy.

- **Policy before execution.** Ask the kernel to write `../../escape.txt` and you get `DENIED` with a recorded reason, e.g. `"path escapes the sandbox: '../../escape.txt' resolves to … which is outside …/sandbox"`. Measured **p50 2.964 ms** (n=10, p95 3.241 ms) — cheaper than execution, which is the correct shape for a security boundary.
- **Independent verification.** A file write is only `VERIFIED` after the verifier re-opens the file from disk and checks `file_exists`, `correct_path`, `correct_content`, `hash_matches` (SHA-256 recomputed from the bytes, not from the executor's claim).
- **Honest failure.** Reading a missing file returns `FAILED` with evidence filed — measured **p50 5.598 ms**. The kernel structurally cannot dress a failure as success; every outcome is a terminal status plus a ledger event.
- **Tamper-evident ledger.** Every decision lands in an append-only JSONL ledger you can `replay`, `resume` and `verify` from the CLI.

Sources: [`core/policy.py`](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/main/projects/agi-system/core/policy.py), [`core/verifier.py`](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/main/projects/agi-system/core/verifier.py), [`core/cli.py`](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/main/projects/agi-system/core/cli.py).

## 2. The numbers (measured in the run that produced this article)

| What | n | p50 | p95 | max | spread |
|---|---:|---:|---:|---:|---:|
| Full `VERIFIED` cycle (plan → policy → execute → verify → evidence) | 50 | **7.416 ms** | 10.143 ms | 12.695 ms | 94.9% |
| `DENIED` at the policy wall | 10 | **2.964 ms** | 3.241 ms | 3.241 ms | 14.3% |
| `FAILED` read (honest failure) | 10 | **5.598 ms** | 7.132 ms | 7.132 ms | 40.3% |
| Live Agent HTTP end-to-end (`POST /api/run`) | 20 | **8.443 ms** | 10.649 ms | 10.649 ms | 31.5% |

Exact counts, not estimates:

| Metric | Value |
|---|---|
| Kernel source | **3,526 LOC / 124.2 KB**, 15 modules |
| Third-party imports | **NONE** — stdlib only, AST-verified in CI on every push |
| Cold import (fresh subprocess, n=5) | **40.56 ms** |
| Peak RSS growth over 70 consecutive runs | **416 KB** total |
| Installable wheel | **40,036 bytes (39.1 KB)** — CLI smoke-tested in CI |
| Test suites executed | **86 + 6 + 9 + 12 + 13 = 126 tests, all passing** |

### The honesty paragraph I refuse to delete

That 7.416 ms is **one run on one shared container**, with 94.9% spread between min and max. Re-running the identical script on the same machine at different load has produced **12.1 ms** and **8.1 ms** p50, and the published reference table in [`docs/BENCHMARKS.md`](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/main/docs/BENCHMARKS.md) says 7.5 ms from an earlier quiet run. The cold-import figure has ranged from **40.56 ms to 58.46 ms**; RSS growth from **416 KB to 636 KB**.

So: **"p50 < 10 ms" is a target on a defined reference machine, not an SLA and not a claim about your hardware.** CI deliberately does *not* hard-fail at 10 ms on shared runners — it records the raw JSON, prints the table in the job summary, and only fails above a 40 ms catastrophic-regression ceiling. A single number quoted without its spread is marketing, not a benchmark.

Reproduce everything in one command:

```bash
python3 scripts/generate_launch_evidence.py --assert
```

## 3. Why zero dependencies matters

Every dependency is a supply-chain decision you didn't make. The kernel imports nothing but the Python standard library, so auditing the trust surface means reading 3.5k lines in an afternoon. It runs anywhere Python 3.10+ runs — no build step, no GPU, no network.

CI enforces this with an AST scan (`scripts/ci_guard.py`): parse every module, collect every imported top-level name, fail if it isn't in `sys.stdlib_module_names`. A subtlety worth sharing: `core/config.py` lazily imports `tomllib` (stdlib from 3.11) inside a `try`, so the gate initially **failed on Python 3.10 only** — the runner's `stdlib_module_names` didn't list it. Fixed with an explicit `CONDITIONAL_STDLIB` set plus 9 self-tests for the gate itself. A gate that nobody tests is just a claim.

## 4. Attacks: 100 real inputs, and a result that is not a badge

I ran 100 adversarial inputs through the real policy wall, plus 10 benign controls:

| Result | Count |
|---|---:|
| `DENIED` by policy | **75** |
| Accepted as a **literal filename inside the sandbox** | **25** |
| Files written outside the sandbox (**breaches**) | **0** |
| Benign controls reaching `VERIFIED` | **10 / 10** |
| p50 per attack | **4.135 ms** · total 577 ms |

I am **not** going to put "100/100 DENIED" on a slide. Those 25 cases are Windows separators and percent-encoded dots (`..\escape.txt`, `C:\Windows\win.ini`, `%2e%2e/x`). On Linux they are literal filenames *inside* the sandbox, so containment held — but `core/policy.py` uses POSIX `Path.resolve()`, which means **cross-platform containment is NOT VERIFIED**. Deploying this kernel on Windows without a test would be irresponsible. The controls matter too: if all 100 inputs were denied, the suite would prove nothing about whether the wall can still let legitimate work through.

The full report, with the exact attack list and gates, is generated by `python3 command-hud/redteam.py --markdown --assert`.

## 5. What I got wrong (radical transparency section)

Publishing only the green numbers would make this an ad. Here is the actual state:

1. **Only 1 of the 48 archived projects is `PROVEN` in this sandbox.** A read-only audit classified 40 as `DECLARED` (documented, not executed), 6 as `SIMULATED` (README says so), 1 as `FAILED`. A README describing a feature is not proof it works. → [`docs/REPOSITORY-AUDIT.md`](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/main/docs/REPOSITORY-AUDIT.md)
2. **Windows containment gap** — above. Unfixed, explicitly labelled.
3. **One certification artifact is an empty file** (`12pro/certification/v1.0.0-raw/health-raw.json` fails JSON parse). Found by the audit; not silently repaired.
4. **One project's LICENSE is a 4-byte corrupt placeholder.** The kernel's own license is still undecided — which is exactly why `agi-kernel` is **not on PyPI**. No "open source" claim is made for the whole archive; licenses are mixed (17 MIT, 4 Apache, 2 proprietary, 23 without a LICENSE file).
5. **A false SHA-256 in my own dashboard.** The kernel writes `metrics.json` internally even on `DENIED` runs; my HUD hashed the first file it found and displayed a "proof" hash for a run that never executed. Its own end-to-end test caught it. Now only the intended target file of a `VERIFIED` run is hashed.
6. **A fake p50 of 0 ms.** Control-plane events had `elapsed_ms = 0` and dragged the latency distribution down. Fixed by excluding non-kernel events; a 13th regression test now guards it.
7. **GitHub Pages never actually deployed for 4 consecutive pushes** — an auto-generated Jekyll workflow built from the repo root instead of `website/`, and failed every time. Replaced with a static deploy. A redundant legacy Pages build still fails cosmetically on each push; it does not affect the live URL.
8. **The public browser demo is a JavaScript port, not the Python kernel**, and not an LLM. It says so on the page itself. Static hosting cannot run a Python process; pretending otherwise would be theatre.

## 6. Try it in 30 seconds (no signup, no API key)

```bash
# 1) The real Python kernel
git clone https://github.com/sayedelazameydesign-crypto/ai-2027.git
cd ai-2027/projects/agi-system
python3 -m unittest discover -s tests -t .        # 86 tests
python3 -m core doctor
python3 -m core run --goal-type create_file --param path=a.txt --param content=hi   # VERIFIED
python3 -m core run --goal-type create_file --param path=../../x --param content=x  # DENIED, exit 3

# 2) Reproduce every number
cd ../.. && python3 live-agent/bench.py
python3 scripts/generate_launch_evidence.py --assert

# 3) Or just open the browser demo
# https://sayedelazameydesign-crypto.github.io/ai-2027/demo.html
```

The wheel is built locally in one command (`./packaging/agi-kernel/build_wheel.sh`) — 39.1 KB, no `pip install` needed to read the source. It is deliberately **not** published to PyPI until the license decision is made.

## 7. What this deliberately is not (yet)

**There is no LLM in the loop.** The planner is deterministic and rule-based. My opinion: the verification discipline has to exist *before* a probabilistic brain is allowed anywhere near it, otherwise "verified" just means the model was persuasive. Phase 2 would connect a model as a **proposer only**, with the kernel still executing and verifying — and that decision (cloud API / local model / defer) is still open. I did not quietly ship it to make the demo look smarter.

I also did not write a "success story" about revenue, customers or production deployments for my commercial projects. I don't have measured data for those yet, and inventing it would contradict the entire premise of this article.

## 8. What I'd like from you

1. **Attack the verifier model.** It's a separate component in the same process, re-reading disk — where does that independence break down?
2. **Critique the benchmark method.** n=50 with published jitter: enough, or should I use a paired comparison against the previous commit?
3. **Test it on Windows.** The 25 literal-path cases are the most interesting open question in this repo.
4. **Tell me whether a deterministic planner is useful, or just a well-tested toy.**

Break it and tell me where — that's the point.

---

*Author & rights holder: [Sayed El-Azamey (سيد العزامي)](https://www.facebook.com/sayed.elazamy) — independent AI systems builder, Cairo, Egypt. Built in public. Every claim above links to code, a test, or a raw artifact you can re-run yourself.*

*Evidence chain: [`launch/PROOF-LEDGER.md`](PROOF-LEDGER.md) · [`docs/BENCHMARKS.md`](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/main/docs/BENCHMARKS.md) · [`docs/REPOSITORY-AUDIT.md`](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/main/docs/REPOSITORY-AUDIT.md)*
