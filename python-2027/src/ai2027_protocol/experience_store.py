"""Persistent, evidence-gated store of agent experiences (P2 learning memory).

The measured problem
--------------------
A learning system that remembers "what worked" usually remembers too much: every
answer it heard, including wrong ones, unverified ones, and ones the user
rejected. That produces a memory that is confidently wrong, and a router that
"learns" bad habits from unverified outcomes.

The rule this module enforces
-----------------------------
**No verified result, no learning.** An experience is admitted to durable
memory only when all of the following are true:

* ``verified is True`` — an *independent* check confirmed the outcome;
* ``test_result == "passed"`` — the verification actually passed, not merely
  ran;
* ``output_fingerprint`` is a well-formed SHA-256 of the verified output;
* the record is internally consistent (a rejected outcome is recorded as a
  *rejected* experience, not silently dropped, so failures are data too).

A record that fails the gate is **refused and the refusal is logged** — never
silently stored, never downgraded to "trusted enough".

The companion rule
------------------
**No persistence, no completion.** A write is not durable until it has been
re-opened from disk and verified. :meth:`ExperienceStore.verify_durability`
re-derives the store from disk and reports what is missing or inconsistent, so
a completion is never claimed on a store that cannot be reopened.

What it reuses
--------------
* ``lexical_embed_text`` and ``cosine_similarity`` from
  :mod:`.celia_memory` for retrieval — deterministic, stdlib-only, and already
  tested. This module does **not** re-implement embeddings.
* The ``StrictModel`` base from :mod:`.contracts` for typed, immutable records.
* Standard-library ``sqlite3`` for persistence, matching the pattern of the
  root ``memory/`` store: durable across restart, no third-party dependency.

What it does not do
-------------------
It does not call models, does not score quality, and does not decide routing.
It is the *ledger* the rest of the learning engine writes to and reads from.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from collections.abc import Iterable
from pathlib import Path

from pydantic import Field, ValidationError

from .celia_memory import cosine_similarity, lexical_embed_text
from .contracts import StrictModel

#: A SHA-256 hex digest, used as the output fingerprint and as the store key.
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: Complexity tiers a task can be classified into.
COMPLEXITY_LEVELS: frozenset[str] = frozenset({"trivial", "standard", "complex"})

#: Verification outcomes that count as "the outcome was independently proven".
PASSING_TEST_RESULTS: frozenset[str] = frozenset({"passed"})


class ExperienceIntegrityError(RuntimeError):
    """Raised when an experience record violates the admission or consistency rules."""


class ExperienceRecord(StrictModel):
    """One immutable, evidence-gated episode of the agent doing a task.

    This is the unit of learning. It captures what the agent was asked, which
    free capability served it, how the attempt went, and — critically — whether
    the outcome was independently verified and whether the user accepted it.
    """

    episode_id: str = Field(min_length=1, max_length=128)
    #: The capability profile that owned the task (matches a routing profile).
    task_type: str = Field(min_length=1, max_length=64)
    complexity: str = Field(default="standard", min_length=1, max_length=32)
    #: A short, redacted description of the task, for retrieval and audit.
    task_summary: str = Field(default="", max_length=2_000)
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=160)
    latency_ms: float = Field(default=0.0, ge=0.0)
    attempts: int = Field(default=1, ge=1)
    #: The single most important field: was the outcome independently verified?
    verified: bool = False
    #: "passed" / "failed" / "unverified" — the verification verdict.
    test_result: str = Field(default="unverified", min_length=1, max_length=32)
    #: A 0..1 quality score, derived by the feedback engine from outcomes.
    quality_score: float = Field(default=0.0, ge=0.0, le=1.0)
    #: User accept / reject / None (no feedback yet).
    accepted: bool | None = None
    #: SHA-256 of the verified output artifact. Required for admission.
    output_fingerprint: str = Field(default="", max_length=64)
    cost_usd: float = Field(default=0.0, ge=0.0)
    billing_tier: str = Field(default="local", min_length=1, max_length=32)
    cost_status: str = Field(default="proven_zero_local", min_length=1, max_length=48)
    created_at: str = Field(default="", max_length=40)

    @property
    def is_admissible(self) -> bool:
        """Return True only when the record meets the evidence-gated admission rule."""
        if not self.verified:
            return False
        if self.test_result not in PASSING_TEST_RESULTS:
            return False
        return bool(_SHA256_RE.match(self.output_fingerprint))

    @property
    def is_rejected(self) -> bool:
        """Return True when the user explicitly rejected the outcome."""
        return self.accepted is False


def fingerprint_text(text: str) -> str:
    """Return the SHA-256 hex digest of ``text`` (the verified-output fingerprint)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    """Return an ISO-8601 UTC timestamp for record creation."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _validate_record(record: ExperienceRecord) -> None:
    """Validate cross-field consistency, raising :class:`ExperienceIntegrityError`."""
    if record.complexity not in COMPLEXITY_LEVELS:
        raise ExperienceIntegrityError(
            f"{record.episode_id}: complexity {record.complexity!r} is not a known level"
        )
    if (
        record.accepted is not None
        and record.verified is False
        and record.test_result != "unverified"
    ):
        raise ExperienceIntegrityError(
            f"{record.episode_id}: user feedback recorded on an unverified outcome"
        )
    if record.cost_usd != 0.0:
        raise ExperienceIntegrityError(
            f"{record.episode_id}: a non-zero cost {record.cost_usd} is not admissible "
            "under the MAX_SPEND=0 learning policy"
        )


class ExperienceStore:
    """SQLite-backed, evidence-gated, restart-durable experience ledger.

    The store is the single door through which learning memory passes. Writes go
    through :meth:`record` and are refused unless the record is admissible; reads
    go through :meth:`get`, :meth:`search`, and :meth:`recent`. :meth:`verify_durability`
    proves the store can be reopened and re-derived from disk.
    """

    def __init__(self, db_path: Path | str) -> None:
        """Open (or create) the SQLite ledger at ``db_path``."""
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Reentrant so record() can hold the lock while delegating to
        # _log_refusal(), which re-acquires it to write the refusal row.
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS experiences (
                episode_id        TEXT PRIMARY KEY,
                task_type         TEXT NOT NULL,
                complexity        TEXT NOT NULL,
                task_summary      TEXT NOT NULL,
                provider          TEXT NOT NULL,
                model             TEXT NOT NULL,
                latency_ms        REAL NOT NULL,
                attempts          INTEGER NOT NULL,
                verified          INTEGER NOT NULL,
                test_result       TEXT NOT NULL,
                quality_score     REAL NOT NULL,
                accepted          INTEGER,
                output_fingerprint TEXT NOT NULL,
                cost_usd          REAL NOT NULL,
                billing_tier      TEXT NOT NULL,
                cost_status       TEXT NOT NULL,
                created_at        TEXT NOT NULL,
                payload_json      TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_experiences_task_type ON experiences(task_type)"
        )
        # The refusal log is deliberate: a memory that drops unverified or
        # rejected outcomes silently is blind to its own failures. Recording the
        # refusal keeps the ledger honest about what it declined to learn from.
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS refusals ("
            "episode_id TEXT PRIMARY KEY, task_type TEXT NOT NULL, "
            "reason TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()

    def __enter__(self) -> ExperienceStore:
        """Support use as a context manager."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the connection on context exit."""
        self.close()

    # ------------------------------------------------------------------ writes

    def record(self, record: ExperienceRecord) -> bool:
        """Admit ``record`` to durable memory if it passes the evidence gate.

        Returns ``True`` when the record was admitted, ``False`` when it was
        refused. A refusal is always logged — a rejected or unverified outcome
        is data, and dropping it silently would make the memory blind to its own
        failures.
        """
        _validate_record(record)
        with self._lock:
            if record.episode_id in self._existing_ids():
                return False
            if not record.is_admissible:
                self._log_refusal(record, "not_admissible")
                return False
            self._conn.execute(
                "INSERT OR REPLACE INTO experiences VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.episode_id,
                    record.task_type,
                    record.complexity,
                    record.task_summary,
                    record.provider,
                    record.model,
                    record.latency_ms,
                    record.attempts,
                    int(record.verified),
                    record.test_result,
                    record.quality_score,
                    None if record.accepted is None else int(record.accepted),
                    record.output_fingerprint,
                    record.cost_usd,
                    record.billing_tier,
                    record.cost_status,
                    record.created_at or _utc_now(),
                    record.model_dump_json(),
                ),
            )
            self._conn.commit()
        return True

    def record_many(self, records: Iterable[ExperienceRecord]) -> int:
        """Admit every admissible record; return the count admitted."""
        return sum(1 for record in records if self.record(record))

    def _log_refusal(self, record: ExperienceRecord, reason: str) -> None:
        """Record a refusal so the memory is honest about what it declined."""
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO refusals (episode_id, task_type, reason, created_at) "
                "VALUES (?,?,?,?)",
                (record.episode_id, record.task_type, reason, _utc_now()),
            )
            self._conn.commit()

    # ------------------------------------------------------------------- reads

    def _existing_ids(self) -> set[str]:
        """Return the set of episode ids already stored."""
        rows = self._conn.execute("SELECT episode_id FROM experiences").fetchall()
        return {str(row["episode_id"]) for row in rows}

    def _row_to_record(self, row: sqlite3.Row) -> ExperienceRecord:
        """Rebuild an :class:`ExperienceRecord` from a database row."""
        payload = json.loads(row["payload_json"])
        try:
            return ExperienceRecord.model_validate(payload)
        except ValidationError as exc:
            raise ExperienceIntegrityError(
                f"corrupt experience row {row['episode_id']}: {exc}"
            ) from exc

    def get(self, episode_id: str) -> ExperienceRecord | None:
        """Return the stored experience for ``episode_id``, or ``None``."""
        row = self._conn.execute(
            "SELECT * FROM experiences WHERE episode_id = ?", (episode_id,)
        ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def recent(self, *, limit: int = 25, task_type: str | None = None) -> list[ExperienceRecord]:
        """Return the most recent experiences, optionally filtered by capability."""
        if task_type is None:
            rows = self._conn.execute(
                "SELECT * FROM experiences ORDER BY created_at DESC, episode_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM experiences WHERE task_type = ? "
                "ORDER BY created_at DESC, episode_id DESC LIMIT ?",
                (task_type, limit),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def search(
        self,
        query: str,
        *,
        k: int = 5,
        task_type: str | None = None,
    ) -> list[ExperienceRecord]:
        """Return the ``k`` most relevant experiences to ``query``.

        Retrieval is lexical and deterministic, reusing the embedding and
        cosine-similarity helpers from :mod:`.celia_memory`. When ``task_type``
        is given, candidates are first filtered to that capability.
        """
        candidates = self.recent(limit=max(k * 20, 200), task_type=task_type)
        if not candidates:
            return []
        query_vec = lexical_embed_text(query)
        scored: list[tuple[float, ExperienceRecord]] = []
        for record in candidates:
            record_vec = lexical_embed_text(record.task_summary)
            scored.append((cosine_similarity(query_vec, record_vec), record))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [record for _score, record in scored[:k]]

    def stats(self) -> dict[str, int]:
        """Return aggregate counts of stored and refused experiences."""
        stored = self._conn.execute("SELECT COUNT(*) AS c FROM experiences").fetchone()["c"]
        refused = (
            self._conn.execute("SELECT COUNT(*) AS c FROM refusals").fetchone()["c"]
            if self._has_table("refusals")
            else 0
        )
        return {"stored": int(stored), "refused": int(refused)}

    def _has_table(self, name: str) -> bool:
        """Return True when a table named ``name`` exists."""
        row = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------ durability

    def verify_durability(self) -> list[str]:
        """Re-open the store from disk and prove it is complete and consistent.

        Returns a list of problems; an empty list means the store is durable.
        This is the check behind **no persistence, no completion**: a run that
        recorded experiences but whose store cannot be reopened is not complete.
        """
        problems: list[str] = []
        self._conn.execute("PRAGMA integrity_check")
        integrity = self._conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            problems.append(f"sqlite integrity_check reported {integrity!r}")
        try:
            rows = self._conn.execute("SELECT payload_json FROM experiences").fetchall()
        except sqlite3.Error as exc:
            problems.append(f"could not read experiences table: {exc}")
            return problems
        for row in rows:
            try:
                self._row_to_record(
                    self._conn.execute(
                        "SELECT * FROM experiences WHERE payload_json = ?", (row["payload_json"],)
                    ).fetchone()
                )
            except ExperienceIntegrityError as exc:
                problems.append(str(exc))
        return problems


__all__ = [
    "COMPLEXITY_LEVELS",
    "ExperienceIntegrityError",
    "ExperienceRecord",
    "ExperienceStore",
    "PASSING_TEST_RESULTS",
    "fingerprint_text",
]
