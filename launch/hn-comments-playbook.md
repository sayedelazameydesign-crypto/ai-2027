# 💬 Show HN Comment Playbook — radical transparency, no spin

> Post from **your own account**, in your own voice. Never paste these as a bot,
> never ask for upvotes, never argue with a vote. The goal is respect, not rank.
> Every answer below points at code, a test, or a raw artifact. If you don't
> know, say "I don't know — let me measure it" and come back with a command.
>
> Companion files: [`show-hn.md`](show-hn.md) (the post) ·
> [`PROOF-LEDGER.md`](PROOF-LEDGER.md) (claim → command) ·
> [`case-study-deterministic-core.md`](case-study-deterministic-core.md) (long form).

---

## Ground rules

1. **Answer every technical comment**, including the rude ones. Ignore only pure trolling.
2. **Lead with the concession**, then the evidence. "You're right, and here's the exact file" beats defensiveness.
3. **Never quote a number without its spread** and the command that produced it.
4. **If a commenter finds a real bug**: thank them, open an issue with their name/handle credited, and link the fix. That single interaction is worth more than the front page.
5. **Never say "we"** if it's one person. Say "I".

---

## The 14 questions you will get

### 1. "This is just a rule engine / a state machine. Where's the AI?"
> Correct — and that's deliberate. There is no model in the loop. I built the verification discipline first because if "verified" means "the LLM was persuasive", the word is worthless. Phase 2 would connect a model as a *proposer only*, with the kernel still executing and verifying. That decision is still open. [`core/planner.py`](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/main/projects/agi-system/core/planner.py)

### 2. "86 tests prove nothing. What about property-based / fuzz testing?"
> Agreed, unit tests prove the cases I thought of. That's why I added a 100-input adversarial suite against the real policy wall and 10 benign controls so you can see the wall isn't just rejecting everything. It's `[75 DENIED, 25 CONTAINED, 0 breaches]`. Fuzzing/property-based testing is a genuinely good next step — if you have a favourite harness that stays zero-dependency, I'd take the PR. `python3 command-hud/redteam.py --markdown --assert`

### 3. "The 'independent verifier' is in the same process. That's not independent."
> You're right about the limit: it's a separate *component* that re-opens the file from disk and recomputes SHA-256 from bytes, ignoring the executor's report — not a security boundary against a malicious in-process executor. It defends against a *buggy or optimistic* executor, not a compromised one. For real isolation you'd need a separate process/container. I should have phrased it that way in the post. [`core/verifier.py`](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/main/projects/agi-system/core/verifier.py)

### 4. "p50 7.5 ms on a shared container is meaningless."
> It is indicative, not an SLA — I said so in the case study and published the spread. That specific number has come out as 12.1 ms, 8.1 ms and 7.416 ms on different runs of the identical script, with spread up to 280%. Regenerate: `python3 scripts/generate_launch_evidence.py --assert`. If you have a better methodology for a shared-CPU microbenchmark, I'd genuinely like it. [`docs/BENCHMARKS.md`](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/main/docs/BENCHMARKS.md)

### 5. "How is this different from [LangGraph / Temporal / durable execution / a CI pipeline]?"
> Those solve orchestration, retries and durable state. This solves a narrower question: *may the system claim success, and what is the evidence?* The verifier is a first-class stage, `DENIED`/`FAILED` are terminal recorded states, and the ledger is append-only and replayable. It's 3.5k lines of stdlib with no runtime, so it's closer to a library than a framework. Happy to be told I've reinvented something — please name it and I'll read it.

### 6. "What about Windows paths? `..\escape.txt` looks like a bypass."
> **This is the best open finding in the repo and I published it rather than hiding it.** On Linux those 25 inputs are literal filenames *inside* the sandbox (0 files escaped). But `core/policy.py` uses POSIX `Path.resolve()`, so on Windows several would be real separators. Cross-platform containment is **NOT VERIFIED** — I would not deploy there without a test. If you run Windows, please try it and tell me. `command-hud/redteam.py` has the exact case list.

### 7. "Why is there a 48-project archive attached to a kernel demo?"
> It's a consolidation of my older repos, byte-audited 48/48 against upstream. It is **not** evidence that any of them work: my own read-only audit classes only 1 project as `PROVEN` here, 40 as `DECLARED`, 6 as `SIMULATED`, 1 as `FAILED`. I keep it because deleting history would be dishonest, not because it's a feature. [`docs/REPOSITORY-AUDIT.md`](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/main/docs/REPOSITORY-AUDIT.md)

### 8. "No LICENSE on the kernel? So it's not open source."
> Correct, and I won't call it that. `agi-system`'s licence is undecided, which is precisely why the wheel is **not on PyPI**. The archive is mixed: 17 MIT, 4 Apache-2.0, 2 proprietary, 23 without a LICENSE file, one corrupt. Please don't reuse anything before checking its folder. This is a real blocker I'm asking the owner to decide.

### 9. "The browser demo is a JS reimplementation, isn't it?"
> Yes — clearly labelled on the page itself. GitHub Pages is static and can't run a Python process, so I wrote a dependency-free JS *port* of the same rules with real SHA-256 and a real policy wall, plus 12 contract tests. The published p50 numbers belong to the Python kernel, not to the browser. Clone and run `python3 -m core doctor` for the real thing. [`website/kernel-demo.js`](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/main/website/kernel-demo.js)

### 10. "SHA-256 of what exactly? And why did a DENIED run show a hash in your dashboard?"
> Good catch — that was a real bug in my HUD. The kernel writes an internal `metrics.json` even on a denied run; my dashboard hashed the first file it found and displayed a "proof" hash for a run that never executed. Its own end-to-end test caught it. Now only the intended target file of a `VERIFIED` run is hashed, and `DENIED` returns `sha256: null`. `command-hud/test_hud.py::test_03/04`.

### 11. "Is the ledger actually tamper-evident, or just append-only?"
> Append-only JSONL with a hash chain you can `verify` from the CLI; the browser demo also implements a small chained ledger with a tamper button. It detects accidental/edit-based tampering, **not** an attacker who can rewrite the whole file and recompute the chain — that needs external anchoring or signing. I should state that boundary more clearly. [`core/event_ledger.py`](https://github.com/sayedelazameydesign-crypto/ai-2027/blob/main/projects/agi-system/core/event_ledger.py)

### 12. "3,526 LOC for six file operations seems like a lot."
> Fair. It includes config, logging, metrics, errors, CLI (run/replay/resume/verify/doctor/snapshot/rotate), a ledger, a risk model and tests. The *loop* is small; the discipline around it is the bulk. If you think a piece is overhead, open an issue — I'd rather delete code than defend it.

### 13. "You claim 'no dependencies' but `pyproject.toml` uses setuptools."
> Setuptools is build-time tooling for the optional wheel; the kernel itself imports only the stdlib, and CI enforces that with an AST scan on every push across Python 3.10/3.11/3.12. `python3 scripts/ci_guard.py imports`. (That gate itself initially failed on 3.10 because of `tomllib` — fixed and now covered by 9 self-tests.)

### 14. "Where's the real-world use / customers / revenue?"
> I don't have measured production data yet, so I'm not going to invent a success story. The commercial side (Pro-Print colour separation, CELIA Fashion, Celia TV) is real work but unmeasured in this repo. I've left a deliberately **unfilled, NOT PUBLISHABLE** template with the exact fields I'd need to fill before making any claim: [`REAL-WORLD-CASE-STUDY.md`](REAL-WORLD-CASE-STUDY.md). If you've shipped something like this in production, I'd rather hear your numbers than make up mine.

---

## If someone finds a bug

Reply template (adapt, don't paste blindly):

> You're right — that's a real bug, thank you. Reproduced with `<exact command>`.
> I've opened issue #N crediting @handle, and the fix is in `<PR/branch>` with a
> regression test. Here's the failing output and the passing output after the fix.

Then actually do it. This is the entire strategy.

## If the post flops

That's data too. Publish the numbers anyway in the next article in the series.
The series outline and its evidence gates are in [`build-in-public.md`](build-in-public.md).
