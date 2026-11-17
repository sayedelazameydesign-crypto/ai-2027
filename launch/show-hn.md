# Show HN — DRAFT (do not publish before readiness gate)

> HN rules reminder: Show HN must be something people can **try** — not a landing page.
> Publish only when the live demo link works. No vote-rings, no "please upvote".

**Title:** Show HN: AI-2027 – Evidence-first AI agents that must prove their work

**Body:**
I built an ecosystem of agent systems around one rule: no claims without evidence.
An agent reaches VERIFIED only after an independent verifier checks the world — failures included.

What's in the hub (github.com/sayedelazameydesign-crypto/ai-2027):

- Cela 2027 — autonomous agent with a public plan → execute → verify loop; runs a labeled simulation mode with no API key so you can inspect the whole pipeline
- agi-system — pure-stdlib Python agent kernel (no pip install): Goal→Planner→Policy→Executor→Verifier→Evidence→Ledger, 86 unit tests, `python3 -m core doctor` health check
- CeliaOS (12pro) — 12-layer agent OS control plane with certification gates G0–G15 and raw benchmark artifacts committed to the repo
- The consolidation itself is audited: all 48 projects diffed byte-for-byte against upstream; one exposed API key found and redacted (rotation documented)

What I'd love from HN:
1. Attack the verifier model — where does independent verification break down?
2. Try the simulation mode and tell me where the UX lies to you.
3. Benchmark methodology critique: certification/ has raw artifacts — are the gates measuring the right things?

Built solo in Egypt, in public. MIT/Apache where marked (per-project licenses — two proprietary projects are excluded from open-source claims).
