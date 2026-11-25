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
| Persist results in a durable ledger; no adoption before human review | ✅ present | `ResearchStore` (SQLite, WAL) + `ResearchAuditLedger` (hash-chained, tamper-evident); `decide()` requires an authenticated `actor="human"` proof |

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
`EvaluationSpec` (host-owned test `inputs` and ground-truth `reference_results`),
the `VerificationThresholds` (`min_accuracy`, `max_latency_ms`,
`max_peak_memory_bytes`), and the `edge_cases` it must exercise.

### `EvaluationSpec`
Host-owned test data: a tuple of `inputs` and a matching tuple of
`reference_results`. The host writes only the `inputs` to the sandbox as
`inputs.json`; the experiment reads them and prints **predictions**. The
`reference_results` are never exposed to the experiment — the host alone
performs the comparison, so the tested code cannot fabricate the accuracy
metric.

### `ExperimentRunner`
Runs an `ExperimentSpec` in isolation and returns a measured `TestEvidence`:

- **Bounded, local, resource-capped execution.** The experiment runs in a
  subprocess (`python -I -B`) with a sanitized environment (no secrets), a hard
  wall-clock timeout, hard `RLIMIT_*` caps (CPU, address space, file size,
  process count), streamed output with a hard byte cap, and process-group
  termination. This aligns with the repo's existing subprocess pattern in
  `tools_exec.py` rather than reinventing it. **It is a confined subprocess,
  not a security sandbox:** it does not block file, network, or syscall access;
  the guarantee is resource bounding and reliable termination.
- **Independent measurement.** Wall-time is measured on the **host**
  (`perf_counter`). Peak memory is measured on the **host** from a per-experiment
  **cgroup v2** subtree's `memory.peak` (the cumulative peak of the whole
  process tree, including grandchildren); when the host denies cgroup-create
  permission the runner falls back honestly to
  `getrusage(RUSAGE_CHILDREN).ru_maxrss`, which reports only the largest single
  child and may understate a multi-process tree. **Accuracy is computed on the
  host** by comparing the experiment's **predictions** against the host-owned
  `reference_results`; the experiment never sees the reference results and any
  self-declared `accuracy`/`passed` value is ignored. The four gates are
  `accuracy`, `latency`, `resource`, `edge_cases`.
- **Descendant reap without TOCTOU.** The process-group id is captured at spawn
  and the whole group is terminated *before* the parent is reaped, so lingering
  background children are reaped while the captured pgid is still valid (not yet
  recycled). This reaps descendants without the TOCTOU risk of killing by a
  recycled id after `wait()`.
- **Full-spec reproducibility.** `artifact_sha256` is content-addressed over the
  **entire spec** (hypothesis, code, environment, timeout, evaluation,
  thresholds, edge-cases), so any change to conditions changes the fingerprint.
- **Failure is data.** A crashing, timing-out, or malformed experiment yields a
  `TestEvidence` whose gates fail — it does not raise and does not get adopted.
  Every result (including failures) can be persisted durably via
  `ResearchStore.record_experiment_result`.

## 3. The rules P4 enforces

- **No automatic adoption.** The runner emits `TestEvidence` only. Deciding a
  candidate is `tested` stays a human call through `ResearchStore.decide`. A
  failing experiment cannot be used to adopt: `decide(status=TESTED)` rejects
  evidence whose gates did not all pass.
- **Human decisions are authenticated.** `actor="human"` is never granted
  automatically: `decide()` requires a non-empty `proof` token, and the
  `/v1/research/decision` route verifies the proof against an optional
  configured secret. An unauthenticated or proxied request cannot mint a human
  decision into the audit chain.
- **Failures are durable.** `ResearchStore.record_experiment_result` persists
  every experiment result (including failures and timeouts) in a durable,
  auditable ledger without changing the paper's status.
- **Reproducibility.** The artifact hash is content-addressed over the full
  spec; the same spec reproduces the same hash.
- **Bounded, local execution.** Subprocess isolation, resource caps, timeout,
  and streamed output bounds. No paid or remote call is involved (consistent
  with `MAX_SPEND=0`).
- **No public Python-execution endpoint.** The runner is a **library
  component**. There is deliberately no HTTP route that executes arbitrary
  Python: the Research API's write paths are not authenticated, and adding one
  would create a remote code-execution risk.

## 4. Wiring to the existing store and ledger

The runner produces a `TestEvidence` compatible with the existing contract; the
existing decision path does the rest:

```
ExperimentSpec ──ExperimentRunner.run──▶ TestEvidence
                                              │
                                              ├──▶ ResearchStore.record_experiment_result(...)  [durable, incl. failures]
                                              │
                                              ▼
                      ResearchStore.decide(status=TESTED, evidence=..., actor="human", proof=...)
                                              │
                                              ▼
                        ResearchStore + ResearchAuditLedger (durable, tamper-evident)
```

The integration test proves the full path: a candidate paper → an experiment →
`TestEvidence` → a human decision (with proof) → the evidence persists across a
restart with the audit chain intact. A failing experiment is recorded
durably but does not change the paper's status.

## 5. What P4 does not claim (limits)

- **Subprocess, not a security sandbox.** The guarantee is a confined
  subprocess with resource caps, streamed output, and process-group termination.
  It does **not** block file, network, or syscall access; a host that must
  prevent those requires OS-level isolation (containers/namespaces) at
  deployment time.
- **The experiment is bounded local code.** No model-weight training, no
  remote provider calls.
- **Edge-case truth is experiment-reported.** Accuracy, latency, and memory are
  measured on the host, but the edge-case results are the experiment's own
  assertions (scoped honestly); independent verification of a specific
  artifact remains a human-review responsibility.

## 6. Acceptance standard

P4 is complete when the platform can take a verified paper, turn it into a
falsifiable hypothesis and a bounded experiment, run that experiment in a
resource-capped subprocess, measure the four gates independently on the host,
persist the resulting evidence durably (including failures) across restarts,
and refuse to adopt anything without an authenticated human decision — all
without a public code-execution endpoint and without touching the kernel,
providers, or `main`.

A stage is **not** complete just because its files exist: it requires a real
execution, durable persistence, reproducible measurement, and authenticated
human decisions. The test suite exercises a real bounded execution, gate
evaluation, artifact reproducibility, timeout/crash/flood handling, failed-
experiment durability, and the full persist-and-reopen path.
