# Contributing to AI-2027

**The one rule:** no claims without evidence. A PR that says "fixed" should show
the test, the command, or the artifact that proves it. This applies to the
kernel, the benchmarks, the website and the docs.

Built in public from Egypt — external contributions are welcome.

## Start here (60 seconds, zero dependencies)

```bash
git clone https://github.com/sayedelazameydesign-crypto/ai-2027.git
cd ai-2027

python3 -m unittest discover -s projects/agi-system/tests -t projects/agi-system   # 86 tests
python3 live-agent/test_server.py                                                   # 6 tests
node website/kernel-demo.test.js                                                    # 12 tests
python3 scripts/ci_guard.py imports                                                 # zero-deps proof
cd projects/agi-system && python3 -m core doctor
```

Everything above uses only the Python/Node standard library. **No `pip install`,
no `npm install`, no API key, no network.** If you need one, that's a signal to
open an issue first.

## Good first issues

- [#5 — translate live-agent UI strings](https://github.com/sayedelazameydesign-crypto/ai-2027/issues/5)
- [#6 — run `bench.py` on your hardware and share the raw JSON](https://github.com/sayedelazameydesign-crypto/ai-2027/issues/6)
- [#7 — propose a new kernel capability](https://github.com/sayedelazameydesign-crypto/ai-2027/issues/7)

Issue #6 is genuinely useful: our timings come from shared containers with
published jitter (7.5 / 12.1 / 8.1 ms p50 across re-runs). A measurement on
real hardware with the full `bench.json` attached is a real contribution.

## Where to work

| Area | Path | Rule |
|---|---|---|
| Canonical kernel | `projects/agi-system/core` | Must stay **zero third-party imports** (AST gate in CI). Planner stays deterministic until Phase 2 is decided. |
| Kernel tests | `projects/agi-system/tests` | Every new rule needs a test for VERIFIED **and** DENIED/FAILED. |
| Live web wrapper | `live-agent/` | Stdlib only; honest labels for simulation vs real execution. |
| Public site + demo | `website/` | Static, zero external scripts; the demo must keep the three honest terminal states (12 contract tests). |
| Numbers | `docs/BENCHMARKS.md`, `live-agent/bench.py` | Publish n, p50, p95, max **and spread**. Never quote a single timing as an SLA. |
| Launch copy | `launch/` | Every claim links to code, a test, or a raw artifact. |

## PR checklist

- [ ] `python3 -m unittest discover -s projects/agi-system/tests -t projects/agi-system` passes
- [ ] `node website/kernel-demo.test.js` passes (if you touched `website/`)
- [ ] `python3 scripts/ci_guard.py imports` passes
- [ ] If you changed timing-sensitive code, attach a fresh `bench.py` JSON — don't just assert "faster"
- [ ] If you added a claim to docs/copy, link the evidence next to it
- [ ] If you added a dependency anywhere in the kernel path: **don't** — open an issue instead

CI runs all of this on every push and pull request across Python 3.10/3.11/3.12;
the benchmark job uploads its raw JSON as an artifact and prints the numbers in
the job summary.

## Honest scope, so nobody wastes time

- The planner is **deterministic, not an LLM**. Phase 2 (cloud API / local model /
  defer) is an open architectural decision — don't pre-empt it in a drive-by PR.
- `website/demo.html` is a **JavaScript port** for static Pages, not the Python
  kernel. Keep that label intact.
- **Licenses are mixed.** Each project under `projects/` keeps its own license;
  `WEB` and `celia-pro-deployment` are proprietary, and `agi-system`'s license is
  pending (which is why `agi-kernel` is not on PyPI). Check before reuse.
- This is not an "all-MIT archive" — see [`docs/PHASE-0-READINESS.md`](docs/PHASE-0-READINESS.md).

## Community

Discussions may need to be enabled by the repository owner (the integration token
used for merges has no admin-settings permission — verified 403). Until then,
use Issues. Real technical critique is welcome; "please upvote" rings are not.

---

<div dir="rtl">

**المساهمات مرحّب بها.** القاعدة واحدة: لا ادعاء بلا دليل. ارفق الأمر أو الاختبار أو
الملف الخام الذي يثبت ما تقوله. الأولويات الحالية: [#٥ الترجمة](https://github.com/sayedelazameydesign-crypto/ai-2027/issues/5)،
[#٦ قياس على عتادك](https://github.com/sayedelazameydesign-crypto/ai-2027/issues/6)،
[#٧ قدرة جديدة للنواة](https://github.com/sayedelazameydesign-crypto/ai-2027/issues/7).

</div>
