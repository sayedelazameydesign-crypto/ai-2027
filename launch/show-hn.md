# Show HN — READY TO COPY (publish after the readiness gate below)

> **HN rule:** Show HN must be something people can **try** — not a landing page.
> No vote-rings, no "please upvote". Post from your own account, not an agent.
> Live demo: <https://sayedelazameydesign-crypto.github.io/ai-2027/demo.html>
> (deploys automatically from `website/` on every push to `main`).

---

## Title

```
Show HN: An agent kernel that can't say "done" without evidence — 3.5k LOC, zero deps, pure Python
```

## Body

```
I got tired of agent frameworks where "the LLM said so" counts as proof. So I
built a kernel with one rule: no claims without evidence.

Every run ends in one of three terminal states:
  VERIFIED — only after an independent verifier re-reads the world (sha256
             of the file, correct path, correct content);
  DENIED   — with a recorded reason (ask it to write ../../escape.txt and the
             policy wall rejects it before the executor runs, in 3.1 ms);
  FAILED   — honestly, with evidence filed, when a read finds nothing.

The executor's own report is never evidence. The verifier is a separate
component that re-opens the file and recomputes the hash. Every decision lands
in an append-only hash-chained JSONL ledger you can replay, resume and fsck.

Measured (Python 3.11, shared Linux container, n=50):
  full verified cycle        p50 7.5 ms   p95 11.1 ms   spread 103%
  policy denial              p50 3.1 ms
  live HTTP end-to-end       p50 10.0 ms
  3,526 LOC / 124 KB, 15 modules, zero third-party imports (AST-verified),
  86/86 unit tests, 40 KB wheel (CLI smoke-tested).

Jitter is published beside every timing because it matters: a re-run on a
loaded container gave 12.1 ms with 172% spread, so I am not calling 7.5 ms an
SLA. CI re-runs bench.py on every push and uploads the raw JSON.

Try it:
  - Browser demo (no signup/API key): https://sayedelazameydesign-crypto.github.io/ai-2027/demo.html
    Honest scope: a dependency-free JS port of the same loop, not the Python
    kernel and not an LLM — it exists so the discipline is inspectable.
  - The real kernel: git clone, then `cd projects/agi-system && python3 -m core doctor`
    and `python3 -m core run --goal-type create_file --param path=a.txt --param content=hi`
  - Reproduce the numbers: `cd live-agent && python3 bench.py`

The planner is deliberately deterministic — no model is called anywhere. My
opinion is that the verification discipline has to exist before an LLM gets
near the loop; otherwise "verified" just means the model was persuasive. Phase 2
will plug a model in as a proposer only, with the kernel still executing and
verifying.

Repo + benchmarks + case study: https://github.com/sayedelazameydesign-crypto/ai-2027

What I'd love from HN:
  1. Attack the verifier model — where does independent re-reading break down?
  2. Benchmark methodology critique: is n=50 with published jitter enough?
  3. Is a deterministic planner actually useful, or just a well-tested toy?

Built solo in Egypt, in public. Mixed per-project licenses (17 MIT, 4 Apache,
2 proprietary, 2 undecided, rest unlicensed) — the kernel's license is still
pending, which is why the wheel is not on PyPI yet.
```

---

## Readiness gate (as of 2026-11-17)

| Gate | Status |
|---|---|
| Live demo URL that a stranger can actually open | ⏳ **auto-deploys on merge of the Pages fix** (4 previous deploys failed at "Build with Jekyll"; replaced with a static `website/` deploy) |
| Every number links to code / raw artifact | ✅ [docs/BENCHMARKS.md](../docs/BENCHMARKS.md) · `live-agent/bench.py` · CI artifact |
| Every claim states its honest scope | ✅ port ≠ kernel ≠ LLM, jitter published, no SLA |
| License honesty | ✅ mixed licenses stated; kernel UNLICENSED pending decision |
| GitHub description / topics / Discussions | ⚠️ **manual, 403 re-verified today** — does not block HN, but nice to have |
| Video / Product Hunt | ⏳ separate, not needed for HN |

**Do not publish until the live URL returns HTTP 200.** Verify with:
`curl -I https://sayedelazameydesign-crypto.github.io/ai-2027/demo.html`
