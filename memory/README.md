# 🧠 L1 — Evidence-gated cumulative memory

> **One rule:** a memory exists only if the kernel already **proved** it happened.
> `DENIED`, `FAILED`, `FROZEN` and tampered records are refused **and the refusal
> is logged**. Nothing is remembered because someone said so.

## Run it

```bash
python3 memory/store.py demo                              # admit / refuse / tamper / noise floor
python3 memory/evaluate.py --markdown                     # measured recall + false positives
python3 memory/test_memory.py -v                           # 16 tests against the REAL kernel

# real use
python3 memory/store.py init   --db memory/out/mem.sqlite
python3 memory/store.py ingest --db memory/out/mem.sqlite \
        --record live-agent/runs/run_x.json --sandbox-root live-agent/sandboxes
python3 memory/store.py search --db memory/out/mem.sqlite --query "halftone 55 lpi"
python3 memory/store.py verify --db memory/out/mem.sqlite --sandbox-root live-agent/sandboxes
```

It is also wired into the Command HUD: every `VERIFIED` run is offered to memory
automatically, and the panel exposes `GET /api/memory` + `POST /api/memory/search`.

## The admission gate (the only door)

| Check | Required |
|---|---|
| Kernel status | `VERIFIED` exactly — `DENIED`/`FAILED`/`FROZEN` refused |
| Verifier checks | `hash_matches`, `file_exists`, `correct_path`, `correct_content` all true |
| SHA-256 | present in `observations` |
| Artifact on disk | re-hashed with `hashlib.sha256` and must match the record |
| Duplicate | identical `(goal_type, params, sha256)` stored once; repeats logged |

If the artifact is not on disk, the row is admitted at **trust 0.6** and flagged
`artifact_verified = 0` — visible, not silently trusted.

`verify()` re-derives everything from disk and reports `hash_mismatches`,
`artifacts_missing` and low-trust rows. Tamper is **detected**, not prevented.

## Backend honesty: why not `sqlite-vec`?

The original plan named `SqliteVecMemoryStore`. `sqlite-vec` is a **third-party
extension**, absent here and in CI:

```console
$ python3 -c "import sqlite3; sqlite3.connect(':memory:').execute('select vec_version()')"
OperationalError: no such function: vec_version
```

Vendoring a binary blob into a project whose entire claim is *zero third-party
imports* would break the claim. So this uses the **stdlib `sqlite3`** plus a
**deterministic feature-hashed lexical embedding** (512 dims, unigrams+bigrams,
Arabic normalisation: diacritics stripped, آ/أ/إ→ا, ى→ي, ة→ه, Persian/Arabic
digits→ASCII). If `sqlite-vec` appears later, the schema can add a vector column
without touching the admission gate.

## Measured quality — including the bad number

From `python3 memory/evaluate.py` on a 20-run corpus (real kernel runs):

| Metric | Value |
|---|---:|
| Noise floor p95 (unrelated pairs) | 0.20 |
| Recall@3 — **easy** set (reuses corpus vocabulary) | 16/16 = 1.000 |
| Recall@3 — **hard** paraphrase set | **2/16 = 0.125** |
| False positives — unrelated queries | 0/5 = 0.000 |
| False positives — near-miss queries | 0/7 = 0.000 |

**The honest headline is 0.125, not 1.000.** The easy set only proves the wiring
works; the hard set deliberately avoids the stored tokens, and a lexical model
misses true synonyms ("dot pattern resolution" ≠ "halftone 55 lpi"). If you want
semantic recall, that requires an embedding model — which is **L4**, an undecided
Phase 2 decision, not something quietly smuggled in here.

## Known limits (do not overclaim)

1. **Lexical, not neural.** Synonyms are missed; quantified above and asserted by `test_11`.
2. **Small corpus.** 20 memories: recall@3 is a smoke test, not a benchmark.
3. **Thresholds are corpus-derived** (`max(0.15, p95)`), so they must be re-measured as the corpus grows.
4. **Tamper-evident, not tamper-proof.** Anyone who can write the SQLite file can insert rows; `verify()` exposes mismatches, and signing is future work.
5. **Arabic works on normalisation, not morphology** — "الوان" vs "ألوان" matches, but root-based stemming does not exist here.
6. **Single-writer assumption.** Writes serialise on a lock; this is a local component, not a distributed store.

## Files

| File | Role |
|---|---|
| `store.py` | Schema, admission gate, embedding, search, `verify()`, CLI, `demo` |
| `evaluate.py` | Builds a 20-run corpus, reports recall/FP/noise, lists its own misses |
| `test_memory.py` | 16 tests incl. refusal paths, tamper detection, concurrency, and a test that asserts a *limitation* |
| `README.md` | This file |
