# Scientific Intelligence — AI-2027 (P4)

This document describes the **scientific-intelligence** stage of the AI-2027
stack. It builds on the existing **Research Radar** rather than creating a
parallel research system, and adds the one thing the radar was missing: a way
to *run* the experiment a candidate paper implies, in isolation, and turn the
result into durable, reviewable evidence.

## 1. What already existed (read-only audit)

Before adding anything, the research stack already covered most of the
scientific-intelligence scope. This is important: P4 is a **gap-filler**, not a
rebuild.

| Scope | Status before P4 | Where |
| --- | --- | --- |
| Collect papers from allowed sources; verify DOI / arXiv ID | ✅ present | `ResearchRadar` + `ArxivAdapter` + `OpenAlexAdapter` + `OpenAcademicGateway`; `ResearchPaper` validates provenance and metadata digest |
| Separate scientific evidence from engineering conclusions | ✅ present | `QuotedEvidence` (verbatim source quotes) vs `EngineeringAssessment` (inference); `save_evaluation` enforces each quote occurs in the fetched title/abstract |
| Convert a paper into a hypothesis and a reproducible experiment | ⚠️ partial | `EngineeringAssessment.proposed_experiment` described the experiment, but there was no first-class `Hypothesis` and no executor |
| Run the experiment in isolation; measure accuracy/perf/resources/edge cases | ⚠️ contracts only | `VerificationGates` and `TestEvidence` defined the schema, but **no component produced them** — `decide()` accepted evidence from a caller |
| Persist results in a durable ledger; no adoption before human review | ✅ present | `ResearchStore` (SQLite, WAL) + `ResearchAuditLedger` (hash-chained, tamper-evident); `decide()` requires `actor="human"` |

**The single real gap:** an isolated, reproducible **experiment runner** that
produces `TestEvidence`.

## 2. What P4 adds: `research_experiment.py`

One new module, three new types, and a runner that reuses the existing
contracts and the repo's existing subprocess-execution pattern.

### `Hypothesis`
A first-class, falsifiable claim kept separate from engineering inference:
`statement`, `falsification_criterion`, and `expected_measurements` (bounded,
finite). This makes the proposed hypothesis an explicit object rather than a
line inside an assessment.

### `ExperimentSpec`
A bounded, reproducible experiment: the `Hypothesis`, the experiment `code`
(a bounded Python snippet), the `environment`, a hard `timeout_s`, the
`VerificationThresholds` (`min_accuracy`, `max_latency_ms`,
`max_peak_memory_bytes`), and the `edge_cases` it must exercise.

### `ExperimentRunner`
Runs an `ExperimentSpec` in isolation and returns a measured `TestEvidence`:

- **Isolated, bounded, local execution.** The experiment runs in a subprocess
  (`python -I -B`) with a sanitized environment (no secrets), a hard timeout,
  and bounded output. This aligns with the repo's existing sandbox pattern in
  `tools_exec.py` rather than reinventing it.
- **Real measurement.** The runner measures wall-time and reads the experiment's
  reported `accuracy` and `peak_memory_bytes`, then evaluates the four gates:
  `accuracy`, `latency`, `resource`, `edge_cases`.
- **Reproducibility.** `artifact_sha256` is content-addressed over the
  experiment code, so re-running the same spec reproduces the same evidence
  hash.
- **Failure is data.** A crashing, timing-out, or malformed experiment yields a
  `TestEvidence` whose gates fail — it does not raise and does not get adopted.

## 3. The rules P4 enforces

- **No automatic adoption.** The runner emits `TestEvidence` only. Deciding a
  candidate is `tested` stays a human call through `ResearchStore.decide`. A
  failing experiment cannot be used to adopt: `decide(status=TESTED)` rejects
  evidence whose gates did not all pass.
- **Reproducibility.** The artifact hash is content-addressed; the same spec
  reproduces the same hash.
- **Bounded, local execution.** Subprocess isolation, timeout, and output caps.
  No paid or remote call is involved (consistent with `MAX_SPEND=0`).
- **No public Python-execution endpoint.** The runner is a **library
  component**. There is deliberately no HTTP route that executes arbitrary
  Python: the Research API's write paths are not authenticated, and adding one
  would create a remote code-execution risk.

## 4. Wiring to the existing store and ledger

The runner produces a `TestEvidence` compatible with the existing contract; the
existing decision path does the rest — no store changes are required:

```
ExperimentSpec ──ExperimentRunner.run──▶ TestEvidence
                                              │
                                              ▼
                      ResearchStore.decide(status=TESTED, evidence=..., actor="human")
                                              │
                                              ▼
                        ResearchStore + ResearchAuditLedger (durable, tamper-evident)
```

The integration test proves the full path: a candidate paper → an experiment →
`TestEvidence` → a human decision → the evidence persists across a restart with
the audit chain intact.

## 5. What P4 does not claim (limits)

- **Isolation is local, not OS-level.** The guarantee is the subprocess
  boundary plus time/output caps plus a sanitized environment. OS-level
  sandboxing (containers) is a deployment concern, not part of this module.
- **The experiment is bounded local code.** No model-weight training, no
  remote provider calls.
- **Gate truth depends on the experiment.** The runner trusts the experiment's
  reported accuracy/memory and its own edge-case assertions; independent
  verification of a specific artifact remains a human-review responsibility.

## 6. Acceptance standard

P4 is complete when the platform can take a verified paper, turn it into a
falsifiable hypothesis and a bounded experiment, run that experiment in
isolation, measure the four gates, persist the resulting evidence durably
across restarts, and refuse to adopt anything without a human decision — all
without a public code-execution endpoint and without touching the kernel,
providers, or `main`.

A stage is **not** complete just because its files exist: it requires a real
execution, durable persistence, and reproducible measurement. The test suite
exercises a real bounded execution, gate evaluation, artifact reproducibility,
timeout/crash handling, and the full persist-and-reopen path.
