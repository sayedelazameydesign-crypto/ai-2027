# Show HN — READY TO COPY (publish after the readiness gate below)

> **HN rule:** Show HN must be something people can **try** — not a landing page.
> No vote-rings, no "please upvote". Post from your own account, not an agent.
> Live demo: <https://sayedelazameydesign-crypto.github.io/ai-2027/demo.html>
> (deploys automatically from `website/` on every push to `main`).

---

## Title (recommended — technical, no adjectives)

```
Show HN: An agent kernel that can't say "done" without evidence — 3.5k LOC, zero deps, pure Python
```

**Alternative title (the pure-technical angle):**
```
Show HN: A pure-Python, zero-dependency deterministic agent core with 100% test pass rate
```
⚠️ If you use it, be precise in the first comment: "100% test pass rate" means
**86/86 kernel tests pass on this run** (126 tests across the whole harness),
not "100% correct". HN will ask. Answer from
[`PROOF-LEDGER.md`](PROOF-LEDGER.md) §B4/B5, and see Q2 in
[`hn-comments-playbook.md`](hn-comments-playbook.md). My recommendation is the
first title: it makes the unusual claim ("can't say done") rather than a pass
rate every project claims.

**Evidence for every number in the body:** [`PROOF-LEDGER.md`](PROOF-LEDGER.md) ·
long-form technical article: [`case-study-deterministic-core.md`](case-study-deterministic-core.md) ·
how to answer comments: [`hn-comments-playbook.md`](hn-comments-playbook.md).

## Body

```
I got tired of agent frameworks where "the LLM said so" counts as proof. So I
built a kernel with one rule: no claims without evidence.

Every run ends in one of three terminal states:
  VERIFIED — only after an independent verifier re-reads the world (sha256
             of the file, correct path, correct content);
  DENIED   — with a recorded reason (ask it to write ../../escape.txt and the
             policy wall rejects it before the executor runs);
  FAILED   — honestly, with evidence filed, when a read finds nothing.

The executor's own report is never evidence. The verifier is a separate
component that re-opens the file and recomputes the hash. Every decision lands
in an append-only hash-chained JSONL ledger you can replay, resume and fsck.

Measured TODAY (Python 3.11, shared Linux container, commit 9ad27fc):
  full verified cycle        p50 7.416 ms   p95 10.143 ms   spread 94.9% (n=50)
  policy denial              p50 2.964 ms   p95 3.241 ms
  honest FAILED read         p50 5.598 ms
  live HTTP end-to-end       p50 8.443 ms
  3,526 LOC / 124.2 KB, 15 modules, zero third-party imports (AST-verified),
  86/86 kernel tests (126 across the harness), 40,036-byte wheel (39.1 KB),
  cold import 40.56 ms.
  100 real adversarial inputs: 75 DENIED, 25 accepted as literal filenames
  inside the sandbox, 0 breaches, 10/10 benign controls VERIFIED.

Jitter is published beside every timing because it matters: re-runs of the
IDENTICAL script on the same machine gave 12.1 ms and 8.1 ms p50 with spread up
to 280%, so I am not calling 7.416 ms an SLA. One command regenerates every
number: `python3 scripts/generate_launch_evidence.py --assert`.

Try it:
  - Browser demo (no signup/API key): https://sayedelazameydesign-crypto.github.io/ai-2027/demo.html
    Honest scope: a dependency-free JS port of the same loop, not the Python
    kernel and not an LLM — it exists so the discipline is inspectable.
  - The real kernel: git clone, then `cd projects/agi-system && python3 -m core doctor`
    and `python3 -m core run --goal-type create_file --param path=a.txt --param content=hi`
  - Reproduce every number: `python3 scripts/generate_launch_evidence.py --assert`
    (or `cd live-agent && python3 bench.py`)

The planner is deliberately deterministic — no model is called anywhere. My
opinion is that the verification discipline has to exist before an LLM gets
near the loop; otherwise "verified" just means the model was persuasive. Phase 2
would plug a model in as a proposer only — that's an open decision, not a shipped feature.

Known limits, stated up front (full list in the case study):
  - The "independent" verifier is a separate component in the same process that
    re-reads disk — not a boundary against a malicious in-process executor.
  - Cross-platform containment is NOT VERIFIED: 25 Windows-separator/encoded
    paths became literal filenames on Linux. 0 escapes, but don't deploy on Windows.
  - Only 1 of the 48 archived projects is PROVEN in my own audit; the rest are
    documented/simulated/unverified. Licenses are mixed; the kernel's is pending.

Repo + benchmarks + case study: https://github.com/sayedelazameydesign-crypto/ai-2027

What I'd love from HN:
  1. Attack the verifier model — where does independent re-reading break down?
  2. Benchmark methodology critique: is n=50 with published jitter enough?
  3. Is a deterministic planner actually useful, or just a well-tested toy?
  4. If you run Windows: please test those 25 paths and tell me what happens.

Built solo in Egypt, in public. Mixed per-project licenses (17 MIT, 4 Apache,
2 proprietary, 2 undecided, rest unlicensed) — the kernel's license is still
pending, which is why the wheel is not on PyPI yet.
```

---

## Readiness gate (as of 2026-11-17)

| Gate | Status |
|---|---|
| Live demo URL that a stranger can actually open | ✅ **VERIFIED LIVE 2026-11-17 11:25 UTC** — `pages/builds` → `status: "built"`, `error: null`; both `demo.html` (title *Try the Kernel — AI-2027 Evidence-First Loop*) and `kernel-demo.js` fetched successfully over HTTPS. Pages workflow run `44073888821` = success (previous Jekyll runs failed 4/4). |
| Every number links to code / raw artifact | ✅ [docs/BENCHMARKS.md](../docs/BENCHMARKS.md) · `live-agent/bench.py` · CI artifact |
| Every claim states its honest scope | ✅ port ≠ kernel ≠ LLM, jitter published, no SLA |
| License honesty | ✅ mixed licenses stated; kernel UNLICENSED pending decision |
| GitHub description / topics / Discussions | ⚠️ **manual, 403 re-verified today** — does not block HN, but nice to have |
| Video / Product Hunt | ⏳ separate, not needed for HN |

> Note: GitHub also runs a redundant *legacy* Jekyll build on each push to `main`
> that fails cosmetically in the Actions tab. It does **not** affect the live URL —
> verified by re-fetching the site after both merges. One-click cleanup (owner
> only, 403 for the integration) is documented in `manual-github-settings.md` §6.
> Do not let a red icon in someone else's workflow delay the post.

**✅ GATE OPEN.** The live URL was fetched successfully after the deploy; re-check
anytime with `curl -I https://sayedelazameydesign-crypto.github.io/ai-2027/demo.html`.
The remaining unchecked items (GitHub metadata, license decision) are nice-to-have,
**not** blockers for HN — do not invent a reason to delay the launch.

> Post from your own account, in your own voice. The draft above is a starting
> point, not a script to be pasted by an agent.
