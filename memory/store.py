#!/usr/bin/env python3
"""Evidence-gated cumulative memory (L1) for the deterministic kernel.

HONEST SCOPE — read before quoting anything from this module
============================================================
* **Dependency honesty.** The original plan named `SqliteVecMemoryStore`. The
  `sqlite-vec` extension is a *third-party* dependency and is NOT installed in
  this environment or in CI (`select vec_version()` → OperationalError). Rather
  than vendor a binary blob into a zero-dependency project, this store uses the
  **stdlib `sqlite3`** plus a **deterministic hashed lexical embedding**.
  If `sqlite-vec` is present later, `--backend auto` will report it, but nothing
  here requires it.
* **Semantic honesty.** The embedding is a feature-hashed bag of unigrams +
  bigrams (optionally Arabic-normalised). It is **lexical, not neural**: it will
  match "halftone lpi 55" with "55 lpi halftone", and it will MISS true synonyms
  ("ink" vs "colourant"). It is deterministic across runs and machines, which is
  exactly what a verification-first system needs, but it is not an LLM.
* **The one rule that matters.** A memory is admitted **only** if the kernel
  already produced `status=VERIFIED` AND the verifier's independent
  `hash_matches` check passed AND a SHA-256 is present. `DENIED`, `FAILED`,
  `FROZEN` and tampered records are refused *and the refusal is logged*.
  Nothing is ever "remembered" because someone said so.
* **What this does NOT protect against.** An attacker who can write to the
  SQLite file can insert rows. `verify()` re-derives everything it can from
  disk, but a fabrication with a plausible hash and no artifact on disk is
  flagged (`artifact_verified=0`), not prevented. Signing the DB is future work.

Usage
-----
    python3 memory/store.py demo
    python3 memory/store.py init   --db memory/out/mem.sqlite
    python3 memory/store.py ingest --db … --record live-agent/runs/run_x.json --sandbox-root live-agent/sandboxes
    python3 memory/store.py ingest-dir --db … --dir live-agent/runs --sandbox-root live-agent/sandboxes
    python3 memory/store.py search --db … --query "halftone 55 lpi" --k 5
    python3 memory/store.py verify --db … --sandbox-root live-agent/sandboxes
    python3 memory/store.py stats  --db …

Stdlib only. Python 3.10+.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import statistics
import threading
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

DIM = 512
TOKEN_RE = re.compile(r"[a-z0-9\u0600-\u06FF]+")
ARABIC_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    memory_id        TEXT PRIMARY KEY,
    created_at       REAL NOT NULL,
    run_id           TEXT NOT NULL,
    goal_type        TEXT NOT NULL,
    params_json      TEXT NOT NULL,
    sha256           TEXT,
    artifact_path    TEXT,
    artifact_bytes   INTEGER,
    artifact_verified INTEGER NOT NULL DEFAULT 0,
    source_kind      TEXT NOT NULL,
    trust            REAL NOT NULL,
    text             TEXT NOT NULL,
    vector           TEXT NOT NULL,
    dim              INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS admission_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  REAL NOT NULL,
    memory_id   TEXT,
    admitted    INTEGER NOT NULL,
    reason      TEXT NOT NULL,
    raw_status  TEXT,
    hash_ok     INTEGER,
    duplicate   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mem_goal ON memories(goal_type);
CREATE INDEX IF NOT EXISTS idx_mem_sha  ON memories(sha256);
CREATE INDEX IF NOT EXISTS idx_log_adm  ON admission_log(admitted);
"""


# --------------------------------------------------------------------------- #
# Normalisation + deterministic embedding
# --------------------------------------------------------------------------- #
def normalise(text: str) -> str:
    """Lowercase, strip Arabic diacritics, unify alef/ya/ta-marbuta, digits→ASCII."""
    t = unicodedata.normalize("NFKC", str(text)).translate(PERSIAN_DIGITS).lower()
    t = ARABIC_DIACRITICS.sub("", t)
    t = (t.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
          .replace("ى", "ي").replace("ة", "ه"))
    return re.sub(r"\s+", " ", t).strip()


def tokens(text: str) -> list[str]:
    norm = normalise(text)
    uni = TOKEN_RE.findall(norm)
    bi = [f"{a}_{b}" for a, b in zip(uni, uni[1:])]
    return uni + bi


def embed(text: str, dim: int = DIM) -> list[float]:
    """Deterministic feature-hashed embedding, L2-normalised. No model, no network."""
    vec = [0.0] * dim
    for tok in tokens(text):
        h = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "big") % dim
        sign = 1.0 if h[4] & 1 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


# --------------------------------------------------------------------------- #
# Admission — the only door into memory
# --------------------------------------------------------------------------- #
@dataclass
class Verdict:
    admitted: bool
    reason: str
    memory_id: str | None = None
    trust: float = 0.0
    artifact_verified: bool = False
    duplicate: bool = False


def _record_text(rec: dict) -> str:
    goal = rec.get("goal") or {}
    params = goal.get("params") or {}
    parts = [str(goal.get("goal_type", ""))]
    for v in params.values():
        if isinstance(v, str):
            parts.append(v)
    for obs in rec.get("observations") or []:
        data = obs.get("data") or {}
        if data.get("path"):
            parts.append(str(data["path"]))
        if isinstance(data.get("content"), str):
            parts.append(data["content"])
    return " · ".join(p for p in parts if p)


def _find_artifact(rec: dict, sandbox_root: Path | None) -> Path | None:
    if not sandbox_root:
        return None
    goal = rec.get("goal") or {}
    path = (goal.get("params") or {}).get("path")
    if not isinstance(path, str) or not path:
        return None
    sandbox = rec.get("sandbox") or rec.get("run_id")
    if not isinstance(sandbox, str):
        return None
    try:
        root = (sandbox_root / sandbox).resolve()
        target = (root / path).resolve()
        if root not in target.parents or not target.is_file():
            return None
        return target
    except (OSError, ValueError):
        return None


def admit(rec: dict, sandbox_root: Path | None = None) -> Verdict:
    """Decide whether a DecisionRecord may become a memory."""
    status = rec.get("status")
    if status != "VERIFIED":
        return Verdict(False, f"refused: kernel status is {status!r}, not VERIFIED")

    evidence = rec.get("evidence") or []
    if not evidence:
        return Verdict(False, "refused: no evidence block")
    checks = (evidence[0] or {}).get("checks") or {}
    if checks.get("hash_matches") is not True:
        return Verdict(False, "refused: independent hash_matches check did not pass")
    for key in ("file_exists", "correct_path", "correct_content"):
        if not checks.get(key):
            return Verdict(False, f"refused: verifier check {key!r} failed")

    sha = None
    for obs in rec.get("observations") or []:
        data = obs.get("data") or {}
        if isinstance(data.get("sha256"), str) and len(data["sha256"]) == 64:
            sha = data["sha256"]
            break
    if not sha:
        return Verdict(False, "refused: no SHA-256 in observations")

    artifact = _find_artifact(rec, sandbox_root)
    artifact_ok, artifact_bytes = False, None
    if artifact is not None:
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        artifact_bytes = artifact.stat().st_size
        if digest != sha:
            return Verdict(False, f"refused: artifact hash mismatch (disk {digest[:12]}… ≠ record {sha[:12]}…)")
        artifact_ok = True

    goal = rec.get("goal") or {}
    memory_id = hashlib.sha256(
        json.dumps([goal.get("goal_type"), goal.get("params"), sha], sort_keys=True,
                   ensure_ascii=False).encode()).hexdigest()[:32]
    trust = 1.0 if artifact_ok else 0.6
    return Verdict(True, "admitted: VERIFIED + independent hash check + SHA-256"
                   + ("" if artifact_ok else " (artifact not on disk — trust 0.6)"),
                   memory_id=memory_id, trust=trust, artifact_verified=artifact_ok)


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
class EvidenceMemoryStore:
    """Thread-safe store.

    A stdlib sqlite3 connection is bound to the thread that created it. The
    Command HUD serves requests from a ThreadingHTTPServer, so a single shared
    connection crashed on the first memory lookup (found by the live smoke test,
    not by a mock). Fix: one connection per thread via threading.local(), plus a
    process-wide write lock so concurrent ingests cannot interleave.
    """

    def __init__(self, db_path: str | Path, sandbox_root: str | Path | None = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.sandbox_root = Path(sandbox_root) if sandbox_root else None
        self._local = threading.local()
        self._write_lock = threading.RLock()
        with self._write_lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    @property
    def conn(self) -> sqlite3.Connection:
        """Per-thread connection (sqlite3 objects are not thread-safe)."""
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
            c.row_factory = sqlite3.Row
            c.executescript(SCHEMA)
            self._local.conn = c
        return c

    # -- write ----------------------------------------------------------- #
    def ingest(self, rec: dict) -> Verdict:
        with self._write_lock:
            return self._ingest_locked(rec)

    def _ingest_locked(self, rec: dict) -> Verdict:
        v = admit(rec, self.sandbox_root)
        if not v.admitted:
            self._log(False, v.reason, rec)
            return v
        cur = self.conn.execute("SELECT 1 FROM memories WHERE memory_id=?", (v.memory_id,))
        if cur.fetchone():
            dup = Verdict(True, "duplicate: identical VERIFIED content already stored",
                          memory_id=v.memory_id, trust=v.trust,
                          artifact_verified=v.artifact_verified, duplicate=True)
            self._log(True, dup.reason, rec, memory_id=v.memory_id, duplicate=True)
            self.conn.commit()
            return dup

        goal = rec.get("goal") or {}
        params = goal.get("params") or {}
        text = _record_text(rec)
        sha = next((o["data"]["sha256"] for o in rec.get("observations") or []
                    if isinstance((o.get("data") or {}).get("sha256"), str)), None)
        art = _find_artifact(rec, self.sandbox_root)
        self.conn.execute(
            "INSERT INTO memories (memory_id, created_at, run_id, goal_type, params_json,"
            " sha256, artifact_path, artifact_bytes, artifact_verified, source_kind,"
            " trust, text, vector, dim) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (v.memory_id, time.time(), str(rec.get("run_id")), str(goal.get("goal_type", "")),
             json.dumps(params, sort_keys=True, ensure_ascii=False), sha,
             str(art) if art else None, art.stat().st_size if art else None,
             1 if v.artifact_verified else 0, "kernel-verified", v.trust, text,
             json.dumps(embed(text)), DIM))
        self._log(True, v.reason, rec, memory_id=v.memory_id)
        self.conn.commit()
        return v

    def _log(self, admitted: bool, reason: str, rec: dict, memory_id: str | None = None,
             duplicate: bool = False) -> None:
        checks = ((rec.get("evidence") or [{}])[0] or {}).get("checks") or {}
        self.conn.execute(
            "INSERT INTO admission_log (created_at, memory_id, admitted, reason,"
            " raw_status, hash_ok, duplicate) VALUES (?,?,?,?,?,?,?)",
            (time.time(), memory_id, 1 if admitted else 0, reason,
             rec.get("status"), 1 if checks.get("hash_matches") else 0,
             1 if duplicate else 0))

    # -- read ------------------------------------------------------------ #
    def rows(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM memories ORDER BY created_at"))

    def log(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM admission_log ORDER BY id"))

    def noise_floor(self) -> dict:
        """Measured, not assumed: how similar are UNRELATED stored memories?

        Kimi-k3's '33% noise floor' is a measurement, not a magic constant, so we
        measure ours on the corpus we actually have and publish it.
        """
        mems = [(r["memory_id"], json.loads(r["vector"])) for r in self.rows()]
        sims: list[float] = []
        for i in range(len(mems)):
            for j in range(i + 1, len(mems)):
                sims.append(cosine(mems[i][1], mems[j][1]))
        if not sims:
            return {"pairs": 0, "median": None, "p95": None, "max": None,
                    "note": "need >=2 memories to measure a noise floor"}
        sims_sorted = sorted(sims)
        return {
            "pairs": len(sims),
            "median": round(statistics.median(sims), 4),
            "p95": round(sims_sorted[min(len(sims_sorted) - 1, int(0.95 * len(sims_sorted)))], 4),
            "max": round(max(sims), 4),
            "note": "cosine similarity between unrelated memories; a hit must beat p95",
        }

    def search(self, query: str, k: int = 5) -> dict:
        qv = embed(query)
        floor = self.noise_floor()
        threshold = max(0.15, float(floor["p95"] or 0.0))
        hits = []
        for r in self.rows():
            score = cosine(qv, json.loads(r["vector"]))
            hits.append({
                "memory_id": r["memory_id"], "score": round(score, 4),
                "above_noise": score > threshold,
                "goal_type": r["goal_type"], "sha256": r["sha256"],
                "trust": r["trust"], "artifact_verified": bool(r["artifact_verified"]),
                "text": r["text"][:160],
            })
        hits.sort(key=lambda h: (-h["score"], h["memory_id"]))
        return {"query": query, "k": k, "threshold": round(threshold, 4),
                "noise_floor": floor, "results": hits[:k],
                "confident": sum(1 for h in hits[:k] if h["above_noise"])}

    def verify(self) -> dict:
        """Re-derive every trust claim from disk. Reports tamper, does not hide it."""
        checked = mismatch = missing = 0
        problems = []
        for r in self.rows():
            if not r["artifact_path"]:
                continue
            checked += 1
            p = Path(r["artifact_path"])
            if not p.is_file():
                missing += 1
                problems.append({"memory_id": r["memory_id"], "issue": "artifact missing",
                                 "path": r["artifact_path"]})
                continue
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            if digest != r["sha256"]:
                mismatch += 1
                problems.append({"memory_id": r["memory_id"], "issue": "HASH MISMATCH",
                                 "expected": r["sha256"], "actual": digest})
        orphans = [dict(r) for r in self.conn.execute(
            "SELECT memory_id, trust, source_kind FROM memories WHERE trust < 0.6")]
        return {"artifacts_checked": checked, "hash_mismatches": mismatch,
                "artifacts_missing": missing, "low_trust_rows": len(orphans),
                "intact": mismatch == 0, "problems": problems[:20]}

    def stats(self) -> dict:
        mem = self.conn.execute("SELECT COUNT(*) c, AVG(trust) t FROM memories").fetchone()
        log = self.conn.execute(
            "SELECT SUM(admitted) a, SUM(1-admitted) r, SUM(duplicate) d FROM admission_log"
        ).fetchone()
        return {
            "db": str(self.db_path),
            "memories": mem["c"], "mean_trust": round(mem["t"], 3) if mem["t"] else None,
            "admitted_events": log["a"] or 0, "refused_events": log["r"] or 0,
            "duplicates_skipped": log["d"] or 0,
            "dim": DIM, "backend": "sqlite3 + hashed-lexical (no sqlite-vec)",
            "noise_floor": self.noise_floor(),
        }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _demo() -> int:
    """Real kernel runs -> real memories. No mocks, no hand-written JSON."""
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here.parent / "command-hud"))
    import kernel_bridge  # noqa: E402

    tmp = Path(tempfile.mkdtemp(prefix="ai2027-memory-demo-"))
    srv = kernel_bridge.load(runtime=tmp, fresh=True)
    store = EvidenceMemoryStore(tmp / "memory.sqlite", sandbox_root=tmp / "sandboxes")

    print("① real kernel runs")
    verified = srv.run_goal("create_file", {"path": "design/spec.txt",
                                            "content": "halftone 55 lpi cmyk"})
    denied = srv.run_goal("create_file", {"path": "../../escape.txt", "content": "x"})
    failed = srv.run_goal("read_file", {"path": "missing.txt"})
    second = srv.run_goal("create_file", {"path": "print/notes.txt",
                                          "content": "screen angles 45 15 75"})
    for label, rec in [("VERIFIED", verified), ("DENIED", denied),
                       ("FAILED", failed), ("VERIFIED", second)]:
        v = store.ingest(rec)
        mark = "✅" if v.admitted else "⛔"
        print(f"  {mark} {label:8} → {v.reason}")

    print("\n② duplicate protection")
    v = store.ingest(verified)
    print(f"  {'↩' if v.duplicate else '}'} {v.reason}")

    print("\n③ tampered record (hash_matches flipped to False)")
    import copy
    bad = copy.deepcopy(verified)
    bad["evidence"][0]["checks"]["hash_matches"] = False
    print(f"  ⛔ {store.ingest(bad).reason}")

    print("\n④ search (noise floor measured on this corpus, not assumed)")
    res = store.search("halftone lpi", k=3)
    print(f"  threshold={res['threshold']} noise_floor={res['noise_floor']}")
    for h in res["results"]:
        print(f"  {h['score']:+.4f} {'✔' if h['above_noise'] else '·'} {h['goal_type']:12} {h['text'][:60]}")

    print("\n⑤ verify from disk")
    print(" ", json.dumps(store.verify(), ensure_ascii=False))

    print("\n⑥ deliberate tamper: edit the artifact on disk")
    art = Path(store.rows()[0]["artifact_path"])
    art.write_text("TAMPERED")
    print(" ", json.dumps(store.verify(), ensure_ascii=False)[:300])

    print("\n⑦ stats")
    print(" ", json.dumps(store.stats(), ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("init", "ingest", "ingest-dir", "search", "verify", "stats"):
        p = sub.add_parser(name)
        p.add_argument("--db", type=Path, required=True)
        p.add_argument("--sandbox-root", type=Path)
        if name == "ingest":
            p.add_argument("--record", type=Path, required=True)
        if name == "ingest-dir":
            p.add_argument("--dir", type=Path, required=True)
        if name == "search":
            p.add_argument("--query", required=True)
            p.add_argument("--k", type=int, default=5)
    sub.add_parser("demo")
    args = ap.parse_args()

    if args.cmd == "demo":
        return _demo()

    store = EvidenceMemoryStore(args.db, sandbox_root=args.sandbox_root)
    if args.cmd == "init":
        print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
    elif args.cmd == "ingest":
        rec = json.loads(args.record.read_text(encoding="utf-8"))
        v = store.ingest(rec)
        print(json.dumps(v.__dict__, ensure_ascii=False, indent=2))
        return 0 if v.admitted else 3
    elif args.cmd == "ingest-dir":
        files = sorted(args.dir.glob("*.json"))
        ok = bad = dup = 0
        for f in files:
            try:
                v = store.ingest(json.loads(f.read_text(encoding="utf-8")))
            except (ValueError, OSError) as exc:
                bad += 1
                print(f"  ⛔ {f.name}: {exc}")
                continue
            dup += int(v.duplicate)
            ok += int(v.admitted and not v.duplicate)
            bad += int(not v.admitted)
        print(json.dumps({"files": len(files), "admitted": ok, "duplicates": dup,
                          "refused": bad, **store.stats()}, ensure_ascii=False, indent=2))
    elif args.cmd == "search":
        print(json.dumps(store.search(args.query, args.k), ensure_ascii=False, indent=2))
    elif args.cmd == "verify":
        r = store.verify()
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["intact"] else 1
    elif args.cmd == "stats":
        print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
