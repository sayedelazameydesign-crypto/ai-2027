# Research Radar 2027 — verified research-to-system protocol

**Status:** v1 foundation implemented in `python-2027`; human-triggered and read-only at the source boundary.

**Decision:** use the free arXiv Atom API for topic-constrained discovery and OpenAlex for DOI lookup. Search results are never silently saved, and a paper is never a production change.
**Cost target:** $0 in API fees. The only external traffic is an explicit HTTPS read to a public metadata API; there is no paid key, model call, or scheduled polling in v1.

## Purpose and trust boundary

The radar is an engineering evidence pipeline, not a link collector. It supports the existing AI-2027 interests in agent evaluation, exact optimization, local retrieval, inference efficiency, and systems security. A model may help interpret a verified title/abstract and draft an experiment card. It cannot invent an identifier, choose a fetch URL, or turn a paper into an implementation by itself.

```text
configured topic
      │
      ├── explicit search ──> arXiv (bounded HTTPS, fixed host)
      │                        └── recent + category filter; not persisted
      │
      └── selected DOI/arXiv ID ──> arXiv / OpenAlex metadata lookup
                                   └── deterministic ID/title/date checks
                                          │
                                    local SQLite candidate
                                    + SHA-256 metadata digest
                                    + hash-chained audit event
                                          │
                                 source quotes ≠ engineering inference
                                          │
                          isolated experiment + four passing gates
                                          │
                            reviewed PR required for production adoption
```

## v1 contract and operation

### Sources and verification

- **arXiv:** discovery and exact-ID resolution use `https://export.arxiv.org/api/query`. The Atom entry ID must resolve to an arXiv abstract path; the title, publication date, categories, and ID must be present and valid. The canonical URL is constructed from the validated ID, not copied from an agent-provided URL.
- **OpenAlex:** DOI lookup uses the fixed `https://api.openalex.org/works` endpoint. The returned DOI must exactly match the requested DOI; a single unambiguous work ID, title, and publication date are required.
- **No arbitrary URLs:** ingestion accepts a DOI, arXiv ID, or canonical `https://doi.org/<DOI>` link. It does not accept an arbitrary host or a caller-supplied metadata payload.
- **No unbounded upstream reads:** HTTPS only, redirects disabled, environment proxies ignored, 2-second connect / 6-second overall timeout, response capped at 512 KB, and no automatic retry loop.
- **No unsupported claims:** metadata is attributed to the source adapter and bound to a SHA-256 digest. OpenAlex is an index, not a publisher; arXiv preprints are not implicitly peer-reviewed. A digest proves local consistency, not truth of the paper's experiments.
- Semantic Scholar and full-text/PDF retrieval are deliberately deferred. v1 reads public metadata/abstracts only and never downloads or executes research code.

### Local contracts and state

The topic allow-list is `research/topics.toml`. Each record contains canonical identifiers, source, title, abstract when available, publication date, categories/authors, canonical source URL, fetch time, and metadata digest. DOI/arXiv aliases deduplicate records. A conflicting title/date or a changed response for the same source identifier is logged and refused rather than overwriting an existing candidate.

Triage states are **Candidate → Tested** or **Candidate → Rejected**. A tested record requires an artifact SHA-256, environment, measurements, and passing checks for accuracy, latency, resource use, and edge cases. Rejected records require a reason. Neither state is production adoption; production changes require a normal reviewed code change and the repository's CI gates.

Agent-created evaluation data is explicitly divided into:

1. `source_evidence`: exact quotes from the fetched title/abstract, checked deterministically before saving.
2. `engineering_assessment`: problem/method interpretation, constraints, system fit, cost, likely breakage, and a proposed PoC. This is inference, not a source fact.

SQLite changes and audit events are transactional. The event chain binds metadata/state hashes, source checks, identifier aliases, actor, and timestamp. Reads and writes fail closed on a broken chain or a state/hash mismatch. The local database is stored under `.ai2027_studio/research.sqlite3` (not in Git) with restrictive file permissions on POSIX.

### API workflow

All requests are local to the Agent Studio API; external source URLs are server constants.

1. `GET /v1/research/topics` — inspect configured fields and priorities.
2. `POST /v1/research/search` with `{ "topic_id": "agent-evaluation", "window_days": 180, "limit": 10 }` — returns source-fetched, recent/category-matched results without saving them.
3. `POST /v1/research/ingest` with a selected identifier and topic ID — re-fetches the metadata and stores a Candidate. This explicit second lookup prevents a search result or model memory from being treated as authoritative.
4. `POST /v1/research/evaluation` — save exact source quotes separately from engineering assessment.
5. `POST /v1/research/decision` — record `tested` only with all four gates and hashed evidence, or `rejected` with a reason.
6. `GET /v1/research/papers` and `GET /v1/research/audit` — inspect the registry; audit reads verify the complete chain first.

`AI2027_READ_ONLY=1` allows topic listing, search, and reads but blocks ingestion, evaluation writes, and decisions. The search endpoint is user-triggered; v1 has no hidden weekly job, surprise corpus download, or automatic LLM spend.

## 2027 delivery plan

### Q1 — Research Radar and stable schema

- Maintain the topic allow-list and strict normalized paper/evaluation/test-evidence contracts.
- Verify identifiers, title/date, canonical source URL, and bounded source responses before storing any candidate.
- Persist SQLite records and a tamper-evident provenance/audit chain; add duplicate/conflict behavior.
- Acceptance: mocked-source tests prove valid IDs pass; unknown/mismatched IDs, invalid dates, duplicate/conflicting metadata, malformed payloads, oversized responses, and audit tampering fail closed. No live source is required by CI.

### Q2 — Automated triage and sandbox PoCs

- Add an opt-in weekly or fortnightly workflow only after rate limits, topic relevance, and an operator kill switch are set. Start with a manual schedule and a strict per-run cap; never ingest search results automatically.
- Let an agent prepare an engineering card with source excerpts, stated limits, system fit, incremental cost, likely breakage, and a small falsifiable experiment.
- Run candidate experiments only in a no-secret, resource-bounded sandbox. No paper code receives workspace, deployment, or connector credentials.
- Acceptance: every proposed PoC has reproducible inputs, a time/resource budget, a baseline, and a cleanup path; running discovery costs $0 and creates no write side effects outside the local registry.

### Q3 — Independent gates and controlled adoption

- Require separate checks for correctness/accuracy, latency, memory/CPU or other resource ceiling, and adversarial/edge cases.
- Keep `Tested` separate from `Adopted`. Only a human-reviewed PR can apply an idea to production paths; link its ADR/change to the canonical DOI/arXiv identifier and evidence artifact hash.
- Record rejected ideas and reasons to avoid rediscovery. A changed upstream record does not silently replace a previously reviewed record.
- Acceptance: each adoption can be reverted independently, has tests on the repository's real workloads, and passes the existing security/CI gates.

### Q4 — Annual audit and 2028 calibration

- Review searched, ingested, evaluated, tested, rejected, and adopted counts; measure saved engineering effort and actual stability/performance effect.
- Remove experiments and dependencies with no measured return. Do not preserve a technique solely because a paper reported a benchmark win.
- Update topics from observed system gaps and publish a 2028 research agenda with owners and limits.
- Acceptance: every adopted change has a source/evidence link; unverified claims are removed; no unused collector or scheduler remains enabled by default.

## Security and governance invariants

- Fail closed on missing/ambiguous identifiers, source errors, invalid metadata, future/unparseable dates, duplicate conflicts, and integrity-check failures.
- Fetch only the fixed allow-listed hosts with HTTPS; no redirects, user-controlled URLs, arbitrary tool calls, or live credentials in research sandboxes.
- Label source statements separately from model interpretation. Store exact excerpts; do not cite a source based on model memory.
- Never treat a benchmark from a paper as a system result. Reproduce the relevant metric on repository workloads and record the environment.
- Do not auto-adopt, auto-deploy, or grant research code access to production files/data.

## Honest limits of the first version

The implemented search surface queries arXiv by configured terms/categories and filters the returned metadata by date and categories. DOI resolution uses OpenAlex. It does not crawl general web pages, verify peer review, parse full PDFs, check author identity, reproduce claims automatically, or call an LLM. Search relevance remains a review task; the topic filter reduces noise but cannot guarantee relevance. A source response is evidence of what that source indexed, not independent scientific validation.
