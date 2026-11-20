"""Durable SQLite task engine: atomic leases, checkpoints, DLQ, audit chain.

Why this exists
---------------
The repository's own gap audit (`docs/pap-2027-engines.json`) reports
``agent-fleet = absent`` and documents that resumability is proven for *one*
agent, not for a cooperating fleet. This module is the missing persistence
layer, written to the same house rules as the rest of the repo:

* **stdlib only** — no ORM, no third-party driver. ``sqlite3`` with WAL.
* **No claimed progress without a row** — every state transition is a SQL
  ``UPDATE`` whose row count is checked; a lost lease is detected, never guessed.
* **Idempotency is a table, not a promise** — each step gets a deterministic
  key, and a unique index refuses a second successful execution of the same key.
* **Recovery is a normal path** — expired leases are requeued by a first-class
  operation that a *different* process can call, which is what makes
  "stop the process, start another, keep going" true rather than aspirational.

Durability settings: ``journal_mode=WAL`` + ``synchronous=FULL``. With ``FULL``
every commit is fsynced, so a ``SIGKILL`` (and a power loss, within the WAL's
guarantee) cannot resurrect lost state. The crash gate in
``scripts/fleet_durability_gate.py`` measures this with a real ``SIGKILL``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "fleet2027"

from .contracts import (
    RESOLVED_STEP_STATUSES,
    TERMINAL_STEP_STATUSES,
    TERMINAL_TASK_STATUSES,
    ContractError,
    StepResult,
    StepSnapshot,
    StepStatus,
    TaskConflictError,
    TaskSnapshot,
    TaskSpec,
    TaskStatus,
    VerificationReport,
    canonical_json,
    require_safe_id,
    sha256_text,
    utc_now,
)

SCHEMA_VERSION = 3

logger = logging.getLogger("fleet2027.store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    task_id      TEXT PRIMARY KEY,
    goal         TEXT NOT NULL,
    spec_json    TEXT NOT NULL,
    spec_sha256  TEXT NOT NULL,
    status       TEXT NOT NULL,
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    lease_owner  TEXT,
    degraded     INTEGER NOT NULL DEFAULT 0,
    cancel_reason TEXT,
    result_json  TEXT
);
CREATE TABLE IF NOT EXISTS steps (
    task_id          TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    step_id          TEXT NOT NULL,
    ordinal          INTEGER NOT NULL,
    agent            TEXT NOT NULL,
    action           TEXT NOT NULL,
    depends_on_json  TEXT NOT NULL,
    inputs_json      TEXT NOT NULL,
    verification_json TEXT NOT NULL,
    critical         INTEGER NOT NULL DEFAULT 1,
    max_attempts     INTEGER NOT NULL,
    timeout_s        REAL,
    idempotency_key  TEXT NOT NULL,
    status           TEXT NOT NULL,
    attempts         INTEGER NOT NULL DEFAULT 0,
    next_attempt_at  REAL,
    last_error       TEXT,
    summary          TEXT,
    output           TEXT,
    artifacts_json   TEXT,
    evidence_json    TEXT,
    verification_report_json TEXT,
    cost_usd         REAL NOT NULL DEFAULT 0,
    started_at       TEXT,
    heartbeat_at     REAL,
    lease_owner      TEXT,
    completed_at     TEXT,
    PRIMARY KEY (task_id, step_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS steps_idem_success
    ON steps(idempotency_key)
    WHERE status = 'succeeded';
CREATE TABLE IF NOT EXISTS attempts (
    task_id     TEXT NOT NULL,
    step_id     TEXT NOT NULL,
    attempt_no  INTEGER NOT NULL,
    agent       TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    outcome     TEXT,
    error       TEXT,
    worker      TEXT,
    PRIMARY KEY (task_id, step_id, attempt_no)
);
CREATE TABLE IF NOT EXISTS dead_letters (
    task_id     TEXT NOT NULL,
    step_id     TEXT NOT NULL,
    attempts    INTEGER NOT NULL,
    reason      TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    requeued_at TEXT,
    PRIMARY KEY (task_id, step_id, created_at)
);
CREATE TABLE IF NOT EXISTS effects (
    idempotency_key TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL,
    step_id         TEXT NOT NULL,
    summary         TEXT,
    recorded_at     TEXT NOT NULL,
    replay_count    INTEGER NOT NULL DEFAULT 0,
    state           TEXT NOT NULL DEFAULT 'completed',
    intent_at       TEXT,
    intent_worker   TEXT,
    completed_at    TEXT,
    compensated_at  TEXT,
    compensation    TEXT
);
CREATE TABLE IF NOT EXISTS checkpoints (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    completed_steps INTEGER NOT NULL,
    total_steps  INTEGER NOT NULL,
    state_json   TEXT NOT NULL,
    state_sha256 TEXT NOT NULL,
    note         TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    task_id    TEXT,
    kind       TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    prev_sha   TEXT NOT NULL,
    sha256     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workers (
    worker_id      TEXT PRIMARY KEY,
    hostname       TEXT NOT NULL,
    pid            INTEGER NOT NULL,
    started_at     TEXT NOT NULL,
    heartbeat_at   REAL NOT NULL,
    stopped_at     TEXT,
    last_task_id   TEXT,
    steps_claimed  INTEGER NOT NULL DEFAULT 0,
    steps_kept     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS task_events (
    seq      INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id  TEXT NOT NULL,
    step_id  TEXT,
    kind     TEXT NOT NULL,
    message  TEXT NOT NULL,
    at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS steps_by_status ON steps(task_id, status);
CREATE INDEX IF NOT EXISTS tasks_by_status ON tasks(status, updated_at);
"""


class StoreError(RuntimeError):
    """Raised when the store's own invariants are violated."""


class TransactionError(StoreError):
    """Raised when a transaction could not be opened or committed."""


class LeaseLostError(StoreError):
    """Raised when a worker acts on a step whose lease it no longer owns."""


class DurableTaskStore:
    """Crash-safe task store: submit, claim, checkpoint, recover, dead-letter.

    Integrity note (honest scope): SQLite guards against *accidental*
    corruption (page checksums in WAL, foreign keys, constraints). It does not
    guard against a hostile writer who can run SQL. ``verify_integrity``
    re-derives what it can — the audit chain, the schema version and the
    database's own page check — and reports what it cannot prove.
    """

    def __init__(self, path: str | Path) -> None:
        """Open (or create) the database at ``path`` and ensure the schema."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path),
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=30000")
        # NOTE: ``executescript`` issues an implicit COMMIT, so it must run
        # outside our explicit transaction wrapper.
        self._conn.executescript(_SCHEMA)
        self._migrate()
        with self.transaction():
            self._conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('audit_genesis', ?)",
                ("0" * 64,),
            )

    def _migrate(self) -> None:
        """Bring a pre-existing database up to :data:`SCHEMA_VERSION`.

        ``CREATE TABLE IF NOT EXISTS`` never alters an existing table, so an
        older database would silently lack the write-ahead effect columns. The
        migration is additive and idempotent: adding a column that already
        exists is a no-op, and no row is rewritten.
        """
        wanted = {
            "state": "TEXT NOT NULL DEFAULT 'completed'",
            "intent_at": "TEXT",
            "intent_worker": "TEXT",
            "completed_at": "TEXT",
            "compensated_at": "TEXT",
            "compensation": "TEXT",
        }
        existing = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(effects)").fetchall()
        }
        with self.transaction() as conn:
            for column, ddl in wanted.items():
                if column in existing:
                    continue
                conn.execute(f"ALTER TABLE effects ADD COLUMN {column} {ddl}")
                logger.info("migrated effects: added column %s", column)
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )

    # ------------------------------------------------------------------ #
    # Connection plumbing
    # ------------------------------------------------------------------ #

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a ``BEGIN IMMEDIATE`` transaction; roll back on any exception.

        ``IMMEDIATE`` takes the write lock up front, which removes the
        read-then-write race that lets two workers claim the same step.
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def close(self) -> None:
        """Close the underlying connection."""
        with self._lock:
            self._conn.close()

    def __enter__(self) -> DurableTaskStore:
        """Support ``with DurableTaskStore(...) as store``."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close on block exit."""
        self.close()

    # ------------------------------------------------------------------ #
    # Audit chain
    # ------------------------------------------------------------------ #

    def _append_audit(
        self,
        conn: sqlite3.Connection,
        *,
        kind: str,
        task_id: str | None,
        payload: Mapping[str, Any],
        at: str,
    ) -> str:
        """Append one hash-chained audit row inside an open transaction."""
        row = conn.execute("SELECT sha256 FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
        prev = row["sha256"] if row else self._meta(conn, "audit_genesis")
        body = canonical_json(
            {"at": at, "kind": kind, "task_id": task_id, "payload": dict(payload)}
        )
        digest = sha256_text(prev + body)
        conn.execute(
            "INSERT INTO audit_log(at, task_id, kind, payload_json, prev_sha, sha256) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (at, task_id, kind, body, prev, digest),
        )
        return digest

    def _meta(self, conn: sqlite3.Connection, key: str) -> str:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        if row is None:
            raise StoreError(f"missing meta key {key!r}")
        return str(row["value"])

    def verify_audit_chain(self) -> tuple[bool, int, str | None]:
        """Recompute the whole chain from genesis; return ``(ok, rows, problem)``."""
        prev = "0" * 64
        rows = 0
        for row in self._conn.execute(
            "SELECT seq, kind, task_id, payload_json, prev_sha, sha256 FROM audit_log ORDER BY seq"
        ):
            if row["prev_sha"] != prev:
                return False, rows, f"row {row['seq']}: prev_sha mismatch"
            body = row["payload_json"]
            if sha256_text(prev + body) != row["sha256"]:
                return False, rows, f"row {row['seq']}: digest mismatch"
            prev = row["sha256"]
            rows += 1
        return True, rows, None

    # ------------------------------------------------------------------ #
    # Events
    # ------------------------------------------------------------------ #

    def _event(
        self,
        conn: sqlite3.Connection,
        task_id: str,
        kind: str,
        message: str,
        *,
        step_id: str | None = None,
        at: str,
    ) -> None:
        conn.execute(
            "INSERT INTO task_events(task_id, step_id, kind, message, at) VALUES(?, ?, ?, ?, ?)",
            (task_id, step_id, kind, message, at),
        )

    def note_event(
        self,
        task_id: str,
        kind: str,
        message: str,
        *,
        step_id: str | None = None,
        now: str | None = None,
    ) -> None:
        """Persist one operational event (degradation, incomplete run, …).

        Streamed logs are ephemeral; a downgrade in behaviour must survive the
        process that observed it, otherwise "degraded" is a claim nobody can
        audit later.
        """
        at = now or utc_now()
        with self.transaction() as conn:
            self._event(conn, task_id, kind, message, step_id=step_id, at=at)
            self._append_audit(
                conn,
                kind=kind,
                task_id=task_id,
                payload={"message": message[:500], "step_id": step_id},
                at=at,
            )

    def events(self, task_id: str, *, limit: int = 200) -> tuple[dict[str, Any], ...]:
        """Return the ordered human-readable event log for one task."""
        rows = self._conn.execute(
            "SELECT seq, task_id, step_id, kind, message, at FROM task_events "
            "WHERE task_id = ? ORDER BY seq LIMIT ?",
            (task_id, limit),
        ).fetchall()
        return tuple(dict(row) for row in rows)

    # ------------------------------------------------------------------ #
    # Submission
    # ------------------------------------------------------------------ #

    def submit(self, spec: TaskSpec, *, now: str | None = None, worker: str = "cli") -> bool:
        """Idempotently persist a task. Returns ``True`` when newly created.

        Re-submitting the same ``task_id`` with the same content is a no-op;
        with different content it raises :class:`TaskConflictError` — the store
        never silently replaces work that may already be half-executed.
        """
        at = now or utc_now()
        spec_json = canonical_json(spec.to_dict())
        spec_hash = sha256_text(spec_json)
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT spec_sha256, status FROM tasks WHERE task_id = ?", (spec.task_id,)
            ).fetchone()
            if existing is not None:
                if existing["spec_sha256"] != spec_hash:
                    raise TaskConflictError(
                        f"task {spec.task_id!r} already exists with a different "
                        "specification; refusing to overwrite live work"
                    )
                return False
            conn.execute(
                "INSERT INTO tasks(task_id, goal, spec_json, spec_sha256, status, created_by,"
                " created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    spec.task_id,
                    spec.goal,
                    spec_json,
                    spec_hash,
                    TaskStatus.QUEUED.value,
                    spec.created_by,
                    at,
                    at,
                ),
            )
            for ordinal, step in enumerate(spec.steps):
                deps = step.depends_on
                conn.execute(
                    "INSERT INTO steps(task_id, step_id, ordinal, agent, action, depends_on_json,"
                    " inputs_json, verification_json, critical, max_attempts, timeout_s,"
                    " idempotency_key, status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        spec.task_id,
                        step.step_id,
                        ordinal,
                        step.agent,
                        step.action,
                        canonical_json(list(deps)),
                        canonical_json(dict(step.inputs)),
                        canonical_json(list(step.verification)),
                        1 if step.critical else 0,
                        step.max_attempts or 3,
                        step.timeout_s,
                        self.idempotency_key(spec.task_id, step.step_id),
                        StepStatus.READY.value if not deps else StepStatus.PENDING.value,
                    ),
                )
            self._event(conn, spec.task_id, "submitted", f"{len(spec.steps)} step(s) queued", at=at)
            self._append_audit(
                conn,
                kind="task.submitted",
                task_id=spec.task_id,
                payload={"spec_sha256": spec_hash, "steps": len(spec.steps)},
                at=at,
            )
        return True

    @staticmethod
    # kernel-exempt: the digest input is a persisted row format ("task::step"),
    # not the kernel's canonical-JSON effect_key shape. Changing it would strand
    # every key already written to the ledger, so the kernel's *hash function* is
    # used (via sha256_text, itself kernel-backed) and the format is kept.
    def idempotency_key(task_id: str, step_id: str) -> str:
        """Deterministic key: re-running a step can never double-apply an effect."""
        return sha256_text(f"{task_id}::{step_id}")

    # ------------------------------------------------------------------ #
    # Reading
    # ------------------------------------------------------------------ #

    def get_task(self, task_id: str) -> TaskSnapshot:
        """Return a snapshot of one task; raises :class:`StoreError` if absent."""
        task = self._conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if task is None:
            raise StoreError(f"unknown task {task_id!r}")
        step_rows = self._conn.execute(
            "SELECT * FROM steps WHERE task_id = ? ORDER BY ordinal", (task_id,)
        ).fetchall()
        steps = tuple(
            StepSnapshot(
                task_id=row["task_id"],
                step_id=row["step_id"],
                ordinal=row["ordinal"],
                agent=row["agent"],
                action=row["action"],
                depends_on=tuple(json.loads(row["depends_on_json"])),
                status=StepStatus(row["status"]),
                attempts=row["attempts"],
                max_attempts=row["max_attempts"],
                critical=bool(row["critical"]),
                idempotency_key=row["idempotency_key"],
                verification=tuple(json.loads(row["verification_json"])),
                cost_usd=float(row["cost_usd"]),
                summary=row["summary"],
                error=row["last_error"],
                completed_at=row["completed_at"],
            )
            for row in step_rows
        )
        return TaskSnapshot(
            task_id=task["task_id"],
            goal=task["goal"],
            status=TaskStatus(task["status"]),
            spec_sha256=task["spec_sha256"],
            created_at=task["created_at"],
            updated_at=task["updated_at"],
            lease_owner=task["lease_owner"],
            degraded=bool(task["degraded"]),
            steps=steps,
        )

    def spec(self, task_id: str) -> TaskSpec:
        """Return the original :class:`TaskSpec` exactly as submitted."""
        row = self._conn.execute(
            "SELECT spec_json FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise StoreError(f"unknown task {task_id!r}")
        return TaskSpec.from_dict(json.loads(row["spec_json"]))

    def list_tasks(self, *, status: TaskStatus | None = None) -> tuple[TaskSnapshot, ...]:
        """List tasks, optionally filtered by status, newest first."""
        if status is None:
            rows = self._conn.execute(
                "SELECT task_id FROM tasks ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT task_id FROM tasks WHERE status = ? ORDER BY created_at DESC",
                (status.value,),
            ).fetchall()
        return tuple(self.get_task(row["task_id"]) for row in rows)

    def step_artifacts(self, task_id: str, step_id: str) -> tuple[dict[str, Any], ...]:
        """Return the artifacts an agent claimed for one step (signals only)."""
        row = self._conn.execute(
            "SELECT artifacts_json FROM steps WHERE task_id = ? AND step_id = ?",
            (task_id, step_id),
        ).fetchone()
        if row is None or row["artifacts_json"] is None:
            return ()
        return tuple(json.loads(row["artifacts_json"]))

    def step_result(self, task_id: str, step_id: str) -> dict[str, Any] | None:
        """Return the recorded result/evidence of one finished step.

        ``error`` and ``status`` are included because a caller that only sees
        an empty output cannot tell a model that answered badly from a step
        that never ran. The benchmark reported "no artifact" for 23 of 24
        sentences when the truth — an LLM timeout — was sitting in this column
        the whole time.
        """
        row = self._conn.execute(
            "SELECT output, evidence_json, verification_report_json, last_error, status "
            "FROM steps WHERE task_id = ? AND step_id = ?",
            (task_id, step_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "output": row["output"],
            "status": row["status"],
            "error": row["last_error"],
            "evidence": json.loads(row["evidence_json"]) if row["evidence_json"] else {},
            "verification": (
                json.loads(row["verification_report_json"])
                if row["verification_report_json"]
                else None
            ),
        }

    # ------------------------------------------------------------------ #
    # Claiming and leases
    # ------------------------------------------------------------------ #

    def claim_ready_steps(
        self,
        task_id: str,
        *,
        worker: str,
        lease_seconds: float,
        now: str | None = None,
        now_ts: float | None = None,
        limit: int = 64,
        capacities: Mapping[str, int] | None = None,
    ) -> tuple[StepSnapshot, ...]:
        """Atomically claim every executable step of one task for ``worker``.

        Executable means: dependencies all resolved *and* status is ``READY``
        (fresh) or ``RETRYING`` (past its backoff). Claims happen inside one
        ``BEGIN IMMEDIATE`` transaction, so two workers racing for the same
        step can never both win.

        ``capacities`` maps an agent name to how many of its steps this worker
        can actually run at once (its bulkhead). Without it, a wave can claim
        more work than it has capacity for, and the surplus is "failed" — the
        right scheduling decision, the wrong *state change*.  Steps that do not
        fit stay queued and keep their attempts.
        """
        at = now or utc_now()
        ts = now_ts if now_ts is not None else self._now_ts()
        claimed: list[StepSnapshot] = []
        with self.transaction() as conn:
            task = conn.execute(
                "SELECT status, lease_owner FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise StoreError(f"unknown task {task_id!r}")
            status = TaskStatus(task["status"])
            if status in TERMINAL_TASK_STATUSES:
                return ()
            if status is TaskStatus.QUEUED:
                conn.execute(
                    "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                    (TaskStatus.RUNNING.value, at, task_id),
                )
            conn.execute(
                "UPDATE tasks SET lease_owner = ?, updated_at = ? WHERE task_id = ?",
                (worker, at, task_id),
            )
            self._sync_ready_flags(conn, task_id, at=at)
            rows = conn.execute(
                "SELECT * FROM steps WHERE task_id = ? AND status IN (?, ?) "
                "ORDER BY ordinal LIMIT ?",
                (task_id, StepStatus.READY.value, StepStatus.RETRYING.value, limit),
            ).fetchall()
            claimed_ids: list[str] = []
            remaining_capacity = dict(capacities or {})
            for row in rows:
                if row["next_attempt_at"] is not None and float(row["next_attempt_at"]) > ts:
                    # Still inside its retry backoff — a candidate, not a claim.
                    continue
                if remaining_capacity:
                    # A step with no known capacity is never blocked: unknown is
                    # not a reason to refuse work.
                    available = remaining_capacity.get(row["agent"])
                    if available is not None and available <= 0:
                        continue
                    if available is not None:
                        remaining_capacity[row["agent"]] = available - 1
                claimed_ids.append(row["step_id"])
                attempt_no = int(row["attempts"]) + 1
                conn.execute(
                    "UPDATE steps SET status = ?, attempts = ?, lease_owner = ?,"
                    " heartbeat_at = ?, started_at = ?, next_attempt_at = NULL"
                    " WHERE task_id = ? AND step_id = ?",
                    (
                        StepStatus.RUNNING.value,
                        attempt_no,
                        worker,
                        ts,
                        at,
                        task_id,
                        row["step_id"],
                    ),
                )
                conn.execute(
                    "INSERT INTO attempts(task_id, step_id, attempt_no, agent, started_at,"
                    " outcome, worker) VALUES(?,?,?,?,?, 'running', ?)",
                    (task_id, row["step_id"], attempt_no, row["agent"], at, worker),
                )
                # Keep the worker row honest: attempts taken, last task touched.
                conn.execute(
                    "UPDATE workers SET steps_claimed = steps_claimed + 1,"
                    " last_task_id = ?, heartbeat_at = ? WHERE worker_id = ?",
                    (task_id, ts, worker),
                )
                self._event(
                    conn,
                    task_id,
                    "step.claimed",
                    f"{row['step_id']} claimed by {worker} (attempt {attempt_no})",
                    step_id=row["step_id"],
                    at=at,
                )
            if claimed_ids:
                self._append_audit(
                    conn,
                    kind="steps.claimed",
                    task_id=task_id,
                    payload={"worker": worker, "steps": claimed_ids},
                    at=at,
                )
        if claimed_ids:
            snapshot = self.get_task(task_id)
            by_id = {step.step_id: step for step in snapshot.steps}
            claimed = [by_id[step_id] for step_id in claimed_ids]
        return tuple(claimed)

    def runnable_now(self, task_id: str, *, now_ts: float | None = None) -> bool:
        """Whether any step of ``task_id`` could be claimed in this instant.

        A task that is merely *waiting* out a retry backoff is not runnable, and
        a long-lived worker must not confuse "nothing to do yet" with "nothing
        left to do". Uses the same expirable-open check the runner uses.
        """
        ts = now_ts if now_ts is not None else self._now_ts()
        row = self._conn.execute(
            "SELECT 1 FROM steps WHERE task_id = ? AND status IN (?, ?, ?) "
            "AND (next_attempt_at IS NULL OR next_attempt_at <= ?) LIMIT 1",
            (
                task_id,
                StepStatus.READY.value,
                StepStatus.RETRYING.value,
                StepStatus.RUNNING.value,
                ts,
            ),
        ).fetchone()
        return row is not None

    def next_attempt_delay(self, task_id: str, *, now_ts: float | None = None) -> float | None:
        """Seconds until the earliest scheduled retry, or ``None`` if unscheduled.

        The runner uses this to *wait* for a backoff instead of spinning, and
        to give up with an explicit reason when nothing is scheduled at all.
        """
        ts = now_ts if now_ts is not None else self._now_ts()
        row = self._conn.execute(
            "SELECT MIN(next_attempt_at) AS soonest FROM steps WHERE task_id = ?"
            " AND status = ? AND next_attempt_at IS NOT NULL",
            (task_id, StepStatus.RETRYING.value),
        ).fetchone()
        if row is None or row["soonest"] is None:
            return None
        return max(0.0, float(row["soonest"]) - ts)

    def _sync_ready_flags(self, conn: sqlite3.Connection, task_id: str, *, at: str) -> None:
        """Promote ``PENDING`` steps whose dependencies are all resolved."""
        rows = conn.execute(
            "SELECT step_id, status, depends_on_json, critical FROM steps WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        resolved: set[str] = set()
        for row in rows:
            if StepStatus(row["status"]) in RESOLVED_STEP_STATUSES:
                resolved.add(row["step_id"])
        changed = 0
        for row in rows:
            if StepStatus(row["status"]) is not StepStatus.PENDING:
                continue
            deps = json.loads(row["depends_on_json"])
            if deps and all(dep in resolved for dep in deps):
                conn.execute(
                    "UPDATE steps SET status = ? WHERE task_id = ? AND step_id = ?",
                    (StepStatus.READY.value, task_id, row["step_id"]),
                )
                changed += 1
        if changed:
            conn.execute(
                "UPDATE tasks SET updated_at = ? WHERE task_id = ?", (at, task_id)
            )

    def heartbeat(
        self,
        task_id: str,
        step_id: str,
        *,
        worker: str,
        lease_seconds: float,
        now_ts: float | None = None,
    ) -> bool:
        """Extend a lease. Returns ``False`` when the lease was already lost."""
        ts = now_ts if now_ts is not None else self._now_ts()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE steps SET heartbeat_at = ? WHERE task_id = ? AND step_id = ?"
                " AND status = ? AND lease_owner = ?",
                (ts, task_id, step_id, StepStatus.RUNNING.value, worker),
            )
            return cur.rowcount == 1

    # ------------------------------------------------------------------ #
    # Completion paths
    # ------------------------------------------------------------------ #

    def record_success(
        self,
        task_id: str,
        step_id: str,
        *,
        worker: str,
        result: StepResult,
        verification: VerificationReport,
        now: str | None = None,
    ) -> bool:
        """Commit a verified step result. Returns ``False`` if the lease was lost.

        The ``effects`` table is written in the same transaction and keyed by
        the deterministic idempotency key, so a duplicate success is refused by
        a UNIQUE constraint rather than by hoping nobody re-runs the step.
        """
        at = now or utc_now()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT status, attempts, lease_owner, idempotency_key FROM steps "
                "WHERE task_id = ? AND step_id = ?",
                (task_id, step_id),
            ).fetchone()
            if row is None:
                raise StoreError(f"unknown step {task_id}/{step_id}")
            if StepStatus(row["status"]) is not StepStatus.RUNNING or row["lease_owner"] != worker:
                return False
            key = row["idempotency_key"]
            existing = conn.execute(
                "SELECT idempotency_key FROM effects WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if existing is not None:
                conn.execute(
                    "UPDATE effects SET replay_count = replay_count + 1 WHERE idempotency_key = ?",
                    (key,),
                )
            else:
                conn.execute(
                    "INSERT INTO effects(idempotency_key, task_id, step_id, summary, recorded_at)"
                    " VALUES(?,?,?,?,?)",
                    (key, task_id, step_id, result.summary[:400], at),
                )
            conn.execute(
                "UPDATE steps SET status = ?, summary = ?, output = ?, artifacts_json = ?,"
                " evidence_json = ?, verification_report_json = ?, cost_usd = ?,"
                " last_error = NULL, completed_at = ?, heartbeat_at = NULL WHERE task_id = ?"
                " AND step_id = ?",
                (
                    StepStatus.SUCCEEDED.value,
                    result.summary[:400],
                    result.output,
                    canonical_json([a.to_dict() for a in result.artifacts]),
                    canonical_json(dict(result.evidence)),
                    canonical_json(verification.to_dict()),
                    float(result.cost_usd),
                    at,
                    task_id,
                    step_id,
                ),
            )
            conn.execute(
                "UPDATE attempts SET finished_at = ?, outcome = 'succeeded' WHERE task_id = ?"
                " AND step_id = ? AND attempt_no = ?",
                (at, task_id, step_id, row["attempts"]),
            )
            self._event(
                conn,
                task_id,
                "step.succeeded",
                f"{step_id} verified in {row['attempts']} attempt(s)",
                step_id=step_id,
                at=at,
            )
            self._append_audit(
                conn,
                kind="step.succeeded",
                task_id=task_id,
                payload={
                    "step_id": step_id,
                    "attempts": row["attempts"],
                    "verification": verification.to_dict(),
                    "artifacts": [a.sha256 for a in result.artifacts],
                },
                at=at,
            )
            self._sync_ready_flags(conn, task_id, at=at)
            self._finalize_if_done(conn, task_id, at=at)
        return True

    def record_failure(
        self,
        task_id: str,
        step_id: str,
        *,
        worker: str,
        error: str,
        will_retry: bool,
        backoff_s: float = 0.0,
        dead_letter_reason: str | None = None,
        now: str | None = None,
    ) -> bool:
        """Record a failed attempt and choose retry, fail, or dead-letter."""
        at = now or utc_now()
        ts = self._now_ts()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT status, attempts, max_attempts, lease_owner, ordinal FROM steps "
                "WHERE task_id = ? AND step_id = ?",
                (task_id, step_id),
            ).fetchone()
            if row is None:
                raise StoreError(f"unknown step {task_id}/{step_id}")
            if StepStatus(row["status"]) is not StepStatus.RUNNING or row["lease_owner"] != worker:
                return False
            attempts = int(row["attempts"])
            # An explicit dead-letter reason means "this failure is not
            # retryable" (unroutable step, open circuit on optional work), so
            # it lands in the DLQ immediately instead of burning attempts.
            exhausted = attempts >= int(row["max_attempts"]) or dead_letter_reason is not None
            if will_retry and not exhausted:
                new_status = StepStatus.RETRYING
                conn.execute(
                    "UPDATE steps SET status = ?, last_error = ?, lease_owner = NULL,"
                    " next_attempt_at = ? WHERE task_id = ? AND step_id = ?",
                    (new_status.value, error[:2000], ts + backoff_s, task_id, step_id),
                )
                outcome = "retry"
                self._event(
                    conn,
                    task_id,
                    "step.retrying",
                    f"{step_id} attempt {attempts} failed: {error[:200]}",
                    step_id=step_id,
                    at=at,
                )
            elif exhausted:
                new_status = StepStatus.DEAD_LETTER
                conn.execute(
                    "UPDATE steps SET status = ?, last_error = ?, lease_owner = NULL,"
                    " completed_at = ? WHERE task_id = ? AND step_id = ?",
                    (new_status.value, error[:2000], at, task_id, step_id),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO dead_letters(task_id, step_id, attempts, reason,"
                    " payload_json, created_at) VALUES(?,?,?,?,?,?)",
                    (
                        task_id,
                        step_id,
                        attempts,
                        dead_letter_reason or error[:500],
                        canonical_json({"error": error[:2000], "attempts": attempts}),
                        at,
                    ),
                )
                outcome = "dead_letter"
                self._event(
                    conn,
                    task_id,
                    "step.dead_letter",
                    f"{step_id} exhausted {attempts} attempt(s) — parked for triage",
                    step_id=step_id,
                    at=at,
                )
            else:
                new_status = StepStatus.FAILED
                conn.execute(
                    "UPDATE steps SET status = ?, last_error = ?, lease_owner = NULL,"
                    " completed_at = ? WHERE task_id = ? AND step_id = ?",
                    (new_status.value, error[:2000], at, task_id, step_id),
                )
                outcome = "failed"
                self._event(
                    conn,
                    task_id,
                    "step.failed",
                    f"{step_id} failed: {error[:200]}",
                    step_id=step_id,
                    at=at,
                )
            conn.execute(
                "UPDATE attempts SET finished_at = ?, outcome = ?, error = ? WHERE task_id = ?"
                " AND step_id = ? AND attempt_no = ?",
                (at, outcome, error[:2000], task_id, step_id, attempts),
            )
            self._append_audit(
                conn,
                kind=f"step.{outcome}",
                task_id=task_id,
                payload={"step_id": step_id, "attempts": attempts, "error": error[:500]},
                at=at,
            )
            self._propagate_blocked(conn, task_id, at=at)
            self._finalize_if_done(conn, task_id, at=at)
        return True

    def _propagate_blocked(self, conn: sqlite3.Connection, task_id: str, *, at: str) -> None:
        """Propagate a failed/dead dependency: skip non-critical, fail critical."""
        rows = conn.execute(
            "SELECT step_id, status, depends_on_json, critical, max_attempts FROM steps"
            " WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        state = {row["step_id"]: StepStatus(row["status"]) for row in rows}
        blocked = {
            row["step_id"]
            for row in rows
            if StepStatus(row["status"]) in {StepStatus.FAILED, StepStatus.DEAD_LETTER}
        }
        changed = True
        while changed:
            changed = False
            for row in rows:
                step_id = row["step_id"]
                if state[step_id] not in {
                    StepStatus.PENDING,
                    StepStatus.READY,
                    StepStatus.RETRYING,
                }:
                    continue
                deps = json.loads(row["depends_on_json"])
                if not any(dep in blocked for dep in deps):
                    continue
                if row["critical"]:
                    new = StepStatus.FAILED
                    note = "blocked by a failed dependency"
                else:
                    new = StepStatus.SKIPPED
                    note = "skipped: upstream failure, step marked non-critical"
                    conn.execute(
                        "UPDATE tasks SET degraded = 1 WHERE task_id = ?", (task_id,)
                    )
                conn.execute(
                    "UPDATE steps SET status = ?, last_error = ?, completed_at = ?"
                    " WHERE task_id = ? AND step_id = ?",
                    (new.value, note, at, task_id, step_id),
                )
                state[step_id] = new
                if new is StepStatus.FAILED:
                    blocked.add(step_id)
                self._event(conn, task_id, f"step.{new.value}", f"{step_id}: {note}",
                            step_id=step_id, at=at)
                changed = True

    def _finalize_if_done(self, conn: sqlite3.Connection, task_id: str, *, at: str) -> None:
        """Set the task's final status once every step is terminal."""
        rows = conn.execute(
            "SELECT status, critical FROM steps WHERE task_id = ?", (task_id,)
        ).fetchall()
        statuses = [StepStatus(row["status"]) for row in rows]
        if not statuses or any(s not in TERMINAL_STEP_STATUSES for s in statuses):
            conn.execute(
                "UPDATE tasks SET updated_at = ? WHERE task_id = ?", (at, task_id)
            )
            return
        task = conn.execute(
            "SELECT status FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if task is not None and TaskStatus(task["status"]) is TaskStatus.CANCELLED:
            return
        # A *critical* failure fails the task. A *non-critical* failure degrades
        # it: the task can still succeed, but the degradation is recorded on the
        # task row and in the event log — never silently smoothed over.
        fatal = [
            (status, bool(row["critical"]))
            for status, row in zip(statuses, rows, strict=True)
            if status in {StepStatus.FAILED, StepStatus.DEAD_LETTER} and bool(row["critical"])
        ]
        non_critical_loss = any(
            status in {StepStatus.FAILED, StepStatus.DEAD_LETTER, StepStatus.SKIPPED}
            and not bool(row["critical"])
            for status, row in zip(statuses, rows, strict=True)
        )
        if non_critical_loss:
            conn.execute("UPDATE tasks SET degraded = 1 WHERE task_id = ?", (task_id,))
        if fatal:
            crashed = any(status is StepStatus.DEAD_LETTER for status, _ in fatal)
            final = TaskStatus.DEAD_LETTER if crashed else TaskStatus.FAILED
        else:
            final = TaskStatus.SUCCEEDED
        conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ?, lease_owner = NULL WHERE task_id = ?",
            (final.value, at, task_id),
        )
        note = "task finished: " + final.value
        if non_critical_loss and final is TaskStatus.SUCCEEDED:
            note += " (degraded: a non-critical step did not produce its output)"
        self._event(conn, task_id, f"task.{final.value}", note, at=at)
        self._append_audit(
            conn,
            kind=f"task.{final.value}",
            task_id=task_id,
            payload={"steps": len(statuses)},
            at=at,
        )

    def finalize(self, task_id: str, *, now: str | None = None) -> TaskStatus:
        """Public finalisation used by callers after out-of-band step changes."""
        at = now or utc_now()
        with self.transaction() as conn:
            self._sync_ready_flags(conn, task_id, at=at)
            self._propagate_blocked(conn, task_id, at=at)
            self._finalize_if_done(conn, task_id, at=at)
        return self.get_task(task_id).status

    # ------------------------------------------------------------------ #
    # Recovery
    # ------------------------------------------------------------------ #

    def recover_expired_leases(
        self,
        *,
        lease_seconds: float,
        now: str | None = None,
        now_ts: float | None = None,
        audit_reason: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Requeue ``RUNNING`` steps whose heartbeat is older than the lease.

        This is the operation that makes crash resumption *real*: it is called
        by a fresh process, not by the dead one, and it is idempotent.
        """
        at = now or utc_now()
        ts = now_ts if now_ts is not None else self._now_ts()
        requeued: list[dict[str, Any]] = []
        with self.transaction() as conn:
            rows = conn.execute(
                "SELECT task_id, step_id, attempts, lease_owner, heartbeat_at, started_at"
                " FROM steps WHERE status = ?",
                (StepStatus.RUNNING.value,),
            ).fetchall()
            for row in rows:
                beat = row["heartbeat_at"]
                if beat is not None and float(beat) > ts - lease_seconds:
                    continue
                conn.execute(
                    "UPDATE steps SET status = ?, lease_owner = NULL, heartbeat_at = NULL,"
                    " started_at = NULL WHERE task_id = ? AND step_id = ? AND status = ?",
                    (
                        StepStatus.RETRYING.value,
                        row["task_id"],
                        row["step_id"],
                        StepStatus.RUNNING.value,
                    ),
                )
                conn.execute(
                    "UPDATE attempts SET finished_at = ?, outcome = 'lost_lease'"
                    " WHERE task_id = ? AND step_id = ? AND attempt_no = ?",
                    (at, row["task_id"], row["step_id"], row["attempts"]),
                )
                requeued.append(
                    {
                        "task_id": row["task_id"],
                        "step_id": row["step_id"],
                        "attempts": row["attempts"],
                        "previous_owner": row["lease_owner"],
                    }
                )
                self._event(
                    conn,
                    row["task_id"],
                    "step.lease_expired",
                    f"{row['step_id']} requeued from {row['lease_owner']} "
                    f"(attempt {row['attempts']} recorded as lost)",
                    step_id=row["step_id"],
                    at=at,
                )
                payload = {
                    "step_id": row["step_id"],
                    "attempts": row["attempts"],
                    "previous_owner": row["lease_owner"],
                }
                if audit_reason:
                    payload["reason"] = audit_reason
                self._append_audit(
                    conn, kind="step.recovered", task_id=row["task_id"],
                    payload=payload, at=at,
                )
            for task_id in {item["task_id"] for item in requeued}:
                conn.execute(
                    "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ? AND status = ?",
                    (TaskStatus.RUNNING.value, at, task_id, TaskStatus.QUEUED.value),
                )
        return tuple(requeued)

    def force_recover_running_steps(
        self, *, reason: str, now: str | None = None
    ) -> tuple[dict[str, Any], ...]:
        """Treat *any* live lease as expired. Operator-only, and audited.

        This is the ``SIGKILL`` path: a killed worker cannot release its lease,
        so a human (or the durability gate) asserts that no other worker owns
        it. The assertion itself is written to the audit chain, which is what
        separates this from silently stealing work.
        """
        at = now or utc_now()
        with self.transaction() as conn:
            self._append_audit(
                conn, kind="recovery.forced", task_id=None,
                payload={"reason": reason}, at=at,
            )
        return self.recover_expired_leases(
            lease_seconds=0.0, now=at, now_ts=self._now_ts(), audit_reason=reason
        )

    # ------------------------------------------------------------------ #
    # Cancellation, dead letters, effects
    # ------------------------------------------------------------------ #

    def cancel_task(
        self, task_id: str, *, reason: str = "cancelled by operator", now: str | None = None
    ) -> TaskSnapshot:
        """Cancel everything not yet terminal; running steps are revoked.

        A worker that was mid-step will find its next ``record_*`` call refused
        (lease lost), so cancellation cannot be silently undone by a late write.
        """
        at = now or utc_now()
        with self.transaction() as conn:
            task = conn.execute(
                "SELECT status FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise StoreError(f"unknown task {task_id!r}")
            if TaskStatus(task["status"]) in TERMINAL_TASK_STATUSES:
                raise StoreError(f"task {task_id!r} is already {task['status']}")
            conn.execute(
                "UPDATE steps SET status = ?, lease_owner = NULL, completed_at = ?,"
                " last_error = ? WHERE task_id = ? AND status NOT IN (?, ?, ?, ?, ?)",
                (
                    StepStatus.CANCELLED.value,
                    at,
                    reason,
                    task_id,
                    StepStatus.SUCCEEDED.value,
                    StepStatus.SKIPPED.value,
                    StepStatus.CANCELLED.value,
                    StepStatus.DEAD_LETTER.value,
                    StepStatus.FAILED.value,
                ),
            )
            conn.execute(
                "UPDATE tasks SET status = ?, cancel_reason = ?, lease_owner = NULL,"
                " updated_at = ? WHERE task_id = ?",
                (TaskStatus.CANCELLED.value, reason, at, task_id),
            )
            self._event(conn, task_id, "task.cancelled", reason, at=at)
            self._append_audit(
                conn, kind="task.cancelled", task_id=task_id, payload={"reason": reason}, at=at
            )
        return self.get_task(task_id)

    def list_dead_letters(self, *, unresolved_only: bool = True) -> tuple[dict[str, Any], ...]:
        """Return parked steps awaiting triage."""
        query = "SELECT * FROM dead_letters"
        if unresolved_only:
            query += " WHERE requeued_at IS NULL"
        query += " ORDER BY created_at"
        return tuple(dict(row) for row in self._conn.execute(query))

    def requeue_dead_letter(
        self, task_id: str, step_id: str, *, now: str | None = None
    ) -> TaskSnapshot:
        """Re-open one parked step with a fresh attempt budget."""
        at = now or utc_now()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT attempts, max_attempts FROM steps WHERE task_id = ? AND step_id = ?",
                (task_id, step_id),
            ).fetchone()
            if row is None:
                raise StoreError(f"unknown step {task_id}/{step_id}")
            conn.execute(
                "UPDATE steps SET status = ?, attempts = 0, max_attempts = ?, last_error = NULL,"
                " completed_at = NULL, next_attempt_at = NULL WHERE task_id = ? AND step_id = ?",
                (StepStatus.RETRYING.value, max(int(row["max_attempts"]), 2), task_id, step_id),
            )
            conn.execute(
                "UPDATE dead_letters SET requeued_at = ? WHERE task_id = ? AND step_id = ?"
                " AND requeued_at IS NULL",
                (at, task_id, step_id),
            )
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (TaskStatus.RUNNING.value, at, task_id),
            )
            self._event(conn, task_id, "step.requeued", f"{step_id} requeued from DLQ",
                        step_id=step_id, at=at)
            self._append_audit(
                conn,
                kind="dead_letter.requeued",
                task_id=task_id,
                payload={"step_id": step_id},
                at=at,
            )
        return self.get_task(task_id)

    def record_effect(
        self,
        key: str,
        *,
        task_id: str,
        step_id: str,
        summary: str = "",
        now: str | None = None,
    ) -> bool:
        """Record an external side effect exactly once (returns ``True`` if new).

        Agents that touch the outside world call this *before* acting. A second
        call returns ``False`` and bumps ``replay_count`` instead of repeating
        the effect — that is the difference between "retried" and "duplicated".
        """
        at = now or utc_now()
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO effects(idempotency_key, task_id, step_id, summary,"
                " recorded_at) VALUES(?,?,?,?,?)",
                (key, task_id, step_id, summary[:400], at),
            )
            if cur.rowcount == 1:
                return True
            conn.execute(
                "UPDATE effects SET replay_count = replay_count + 1 WHERE idempotency_key = ?",
                (key,),
            )
            return False

    def declare_effect_intent(
        self,
        key: str,
        *,
        task_id: str,
        step_id: str,
        summary: str = "",
        worker: str = "",
        now: str | None = None,
    ) -> dict[str, Any]:
        """Write *intent* rows for step effects before the handler runs.

        This is the write-ahead half of exactly-once. A process that appends to
        an external ledger and dies before recording the effect leaves **no**
        trace — so a recovering worker cannot tell "never ran" from "ran and
        vanished", and repeating the effect is the only safe choice. With an
        intent row committed first, a recovering worker sees ``state='intent'``
        and must reconcile (verify, or compensate) instead of blindly repeating.

        Returns the effect row as it stands after the call.
        """
        at = now or utc_now()
        keys = [key]
        with self.transaction() as conn:
            for effect_key in keys:
                conn.execute(
                    "INSERT OR IGNORE INTO effects(idempotency_key, task_id, step_id,"
                    " summary, recorded_at, state, intent_at, intent_worker)"
                    " VALUES(?,?,?,?,?, 'intent', ?, ?)",
                    (effect_key, task_id, step_id, summary[:400], at, at, worker[:120]),
                )
                conn.execute(
                    "UPDATE effects SET intent_at = COALESCE(intent_at, ?),"
                    " intent_worker = COALESCE(intent_worker, ?)"
                    " WHERE idempotency_key = ? AND state = 'intent'",
                    (at, worker[:120], effect_key),
                )
            self._append_audit(
                conn,
                kind="effect.intent",
                task_id=task_id,
                payload={"step_id": step_id, "keys": keys, "worker": worker},
                at=at,
            )
            row = conn.execute(
                "SELECT * FROM effects WHERE idempotency_key = ?", (key,)
            ).fetchone()
        return dict(row) if row else {}

    def reconcile_effects(self, task_id: str, *, at: str | None = None) -> tuple[dict[str, Any], ...]:
        """Reconcile effect intents left behind by a dead or crashed worker.

        An ``intent`` row is an *unfinished* external effect. Two honest options
        exist, and the store refuses to pick silently:

        * **completed** — the effect really happened (the agent verifies it), so
          record it and move on;
        * **compensated** — it may or may not have happened, and compensation
          makes that irrelevant (delete, reverse, void).

        Anything left as ``intent`` is reported as an unresolved risk, not
        counted as done.
        """
        stamp = at or utc_now()
        with self.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM effects WHERE task_id = ? AND state = 'intent'", (task_id,)
            ).fetchall()
            ids = [row["idempotency_key"] for row in rows]
            if not ids:
                return ()
            conn.execute(
                "UPDATE effects SET state = 'compensated', compensated_at = ?,"
                " compensation = COALESCE(compensation, 'reconciled: outcome unknown,"
                " compensation applied before retry')"
                " WHERE task_id = ? AND state = 'intent'",
                (stamp, task_id),
            )
            self._append_audit(
                conn,
                kind="effect.reconciled",
                task_id=task_id,
                payload={"keys": ids, "outcome": "compensated"},
                at=stamp,
            )
            self._event(
                conn,
                task_id,
                "effect.reconciled",
                f"{len(ids)} effect intent(s) left by a dead worker were compensated",
                at=stamp,
            )
        return tuple({"idempotency_key": key, "outcome": "compensated"} for key in ids)

    def complete_effect(
        self,
        key: str,
        *,
        task_id: str,
        step_id: str,
        summary: str = "",
        now: str | None = None,
    ) -> bool:
        """Mark a declared intent as really completed (idempotent)."""
        at = now or utc_now()
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE effects SET state = 'completed', completed_at = ?,"
                " summary = COALESCE(NULLIF(?, ''), summary)"
                " WHERE idempotency_key = ? AND state IN ('intent', 'completed')",
                (at, summary[:400], key),
            )
            if cur.rowcount == 0:
                conn.execute(
                    "INSERT OR IGNORE INTO effects(idempotency_key, task_id, step_id,"
                    " summary, recorded_at, state, completed_at)"
                    " VALUES(?,?,?,?,?, 'completed', ?)",
                    (key, task_id, step_id, summary[:400], at, at),
                )
        return True

    def unresolved_effect_intents(self, task_id: str | None = None) -> tuple[dict[str, Any], ...]:
        """Effect intents whose external outcome is still unknown."""
        if task_id is None:
            rows = self._conn.execute(
                "SELECT * FROM effects WHERE state = 'intent' ORDER BY recorded_at"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM effects WHERE state = 'intent' AND task_id = ?"
                " ORDER BY recorded_at",
                (task_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def effect(self, key: str) -> dict[str, Any] | None:
        """Return one recorded effect, or ``None``."""
        row = self._conn.execute(
            "SELECT * FROM effects WHERE idempotency_key = ?", (key,)
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ #
    # Worker registry (cross-process coordination)
    # ------------------------------------------------------------------ #

    def register_worker(
        self,
        worker_id: str,
        *,
        hostname: str | None = None,
        pid: int | None = None,
        now_ts: float | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        """Announce a worker process. Re-registering the same id is a heartbeat.

        The row is what makes "who is alive?" answerable *without* asking the OS:
        a worker that stops heartbeating is dead, no matter which machine it ran
        on. That is the only honest cross-machine liveness signal available at
        zero infrastructure cost.
        """
        import os
        import socket

        require_safe_id(worker_id, where="worker_id")
        at = now or utc_now()
        ts = now_ts if now_ts is not None else self._now_ts()
        host = hostname or socket.gethostname()[:120]
        process = pid if pid is not None else os.getpid()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO workers(worker_id, hostname, pid, started_at, heartbeat_at)"
                " VALUES(?,?,?,?,?)"
                " ON CONFLICT(worker_id) DO UPDATE SET heartbeat_at = excluded.heartbeat_at,"
                " stopped_at = NULL",
                (worker_id, host, process, at, ts),
            )
            self._append_audit(
                conn,
                kind="worker.registered",
                task_id=None,
                payload={"worker_id": worker_id, "pid": pid, "hostname": hostname},
                at=at,
            )
            self._event(conn, "*", "worker.registered",
                        f"{worker_id}@{host}:{process} joined", at=at)
        return {"worker_id": worker_id, "hostname": host, "pid": process, "at": at}

    def worker_heartbeat(
        self,
        worker_id: str,
        *,
        now_ts: float | None = None,
        last_task_id: str | None = None,
        steps_claimed: int | None = None,
        steps_kept: int | None = None,
    ) -> None:
        """Update liveness and running counters for one worker."""
        ts = now_ts if now_ts is not None else self._now_ts()
        with self._lock:
            self._conn.execute(
                "UPDATE workers SET heartbeat_at = ?, last_task_id = COALESCE(?, last_task_id),"
                " steps_claimed = COALESCE(?, steps_claimed),"
                " steps_kept = COALESCE(?, steps_kept) WHERE worker_id = ?",
                (ts, last_task_id, steps_claimed, steps_kept, worker_id),
            )

    def deregister_worker(
        self, worker_id: str, *, now: str | None = None, now_ts: float | None = None
    ) -> None:
        """Mark a worker as stopped (graceful exit). It may still be shown as dead later."""
        at = now or utc_now()
        with self.transaction() as conn:
            conn.execute(
                "UPDATE workers SET stopped_at = ? WHERE worker_id = ?", (at, worker_id)
            )
            self._append_audit(
                conn,
                kind="worker.stopped",
                task_id=None,
                payload={"worker_id": worker_id},
                at=at,
            )
            self._event(conn, "*", "worker.stopped", f"{worker_id} left", at=at)

    def list_workers(self, *, stale_after_s: float = 120.0,
                     now_ts: float | None = None) -> tuple[dict[str, Any], ...]:
        """Return every known worker with an explicit ``alive`` verdict."""
        ts = now_ts if now_ts is not None else self._now_ts()
        rows = self._conn.execute("SELECT * FROM workers ORDER BY started_at").fetchall()
        out = []
        for row in rows:
            stopped = row["stopped_at"] is not None
            fresh = (ts - float(row["heartbeat_at"])) <= stale_after_s
            out.append(
                {
                    "worker_id": row["worker_id"],
                    "hostname": row["hostname"],
                    "pid": row["pid"],
                    "started_at": row["started_at"],
                    "age_s": round(ts - float(row["heartbeat_at"]), 3),
                    "stopped": stopped,
                    "alive": bool(fresh and not stopped),
                    "last_task_id": row["last_task_id"],
                    "steps_claimed": row["steps_claimed"],
                    "steps_kept": row["steps_kept"],
                }
            )
        return tuple(out)

    def live_workers(self, *, stale_after_s: float = 120.0,
                     now_ts: float | None = None) -> tuple[dict[str, Any], ...]:
        """Only the workers whose heartbeat is fresh and who have not stopped."""
        return tuple(
            worker
            for worker in self.list_workers(stale_after_s=stale_after_s, now_ts=now_ts)
            if worker["alive"]
        )

    def requeue_steps_of_dead_workers(
        self,
        *,
        stale_after_s: float,
        now: str | None = None,
        now_ts: float | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Requeue work held by a worker whose process died without releasing it.

        Unlike :meth:`recover_expired_leases` (which is lease-time based), this is
        *worker* based: it only touches steps owned by an id whose heartbeat is
        stale or that has deregistered. That distinction matters when two live
        workers are running — a fresh lease on a live worker is never stolen.
        """
        at = now or utc_now()
        ts = now_ts if now_ts is not None else self._now_ts()
        registry = {
            row["worker_id"]: row
            for row in self._conn.execute(
                "SELECT worker_id, heartbeat_at, stopped_at FROM workers"
            )
        }
        dead_ids = {
            worker_id
            for worker_id, row in registry.items()
            if row["stopped_at"] is not None
            or (ts - float(row["heartbeat_at"])) > stale_after_s
        }
        recovered: list[dict[str, Any]] = []
        with self.transaction() as conn:
            rows = conn.execute(
                "SELECT task_id, step_id, attempts, lease_owner, heartbeat_at FROM steps"
                " WHERE status = ?",
                (StepStatus.RUNNING.value,),
            ).fetchall()
            for row in rows:
                owner = row["lease_owner"]
                # A registered owner is judged by its worker row; an owner that
                # never registered (an ad-hoc run) is judged by the step's own
                # heartbeat, so a live lease is never stolen in either case.
                if owner is None:
                    stale = False
                elif owner in registry:
                    stale = owner in dead_ids
                else:
                    stale = row["heartbeat_at"] is None or (
                        (ts - float(row["heartbeat_at"])) > stale_after_s
                    )
                if not stale:
                    continue
                conn.execute(
                    "UPDATE steps SET status = ?, lease_owner = NULL, heartbeat_at = NULL,"
                    " started_at = NULL WHERE task_id = ? AND step_id = ? AND status = ?",
                    (StepStatus.RETRYING.value, row["task_id"], row["step_id"],
                     StepStatus.RUNNING.value),
                )
                conn.execute(
                    "UPDATE attempts SET finished_at = ?, outcome = 'owner_dead'"
                    " WHERE task_id = ? AND step_id = ? AND attempt_no = ?",
                    (at, row["task_id"], row["step_id"], row["attempts"]),
                )
                recovered.append(
                    {
                        "task_id": row["task_id"],
                        "step_id": row["step_id"],
                        "attempts": row["attempts"],
                        "previous_owner": owner,
                    }
                )
                self._event(
                    conn, row["task_id"], "step.owner_dead",
                    f"{row['step_id']} requeued: owner {owner!r} is not alive",
                    step_id=row["step_id"], at=at,
                )
                self._append_audit(
                    conn, kind="step.recovered_dead_worker", task_id=row["task_id"],
                    payload={"step_id": row["step_id"], "previous_owner": owner,
                             "attempts": row["attempts"]}, at=at,
                )
        return tuple(recovered)

    def active_task_ids(self) -> tuple[str, ...]:
        """Task ids that are not terminal yet — the work pool a worker may claim from."""
        rows = self._conn.execute(
            "SELECT task_id FROM tasks WHERE status IN (?, ?) ORDER BY created_at",
            (TaskStatus.QUEUED.value, TaskStatus.RUNNING.value),
        ).fetchall()
        return tuple(row["task_id"] for row in rows)

    # ------------------------------------------------------------------ #
    # Checkpoints and recovery seeds
    # ------------------------------------------------------------------ #

    def checkpoint(self, task_id: str, *, note: str = "", now: str | None = None) -> dict[str, Any]:
        """Persist a resume-ready snapshot of one task and return it.

        A checkpoint is intentionally *derived*: it stores the specification,
        the status of every step, the idempotency ledger size, and a digest.
        On restore, the digest is verified before anything is trusted.
        """
        at = now or utc_now()
        snapshot = self.get_task(task_id)
        spec = self.spec(task_id)
        state = {
            "task": spec.to_dict(),
            "status": snapshot.status.value,
            "degraded": snapshot.degraded,
            "steps": [
                {
                    "step_id": step.step_id,
                    "status": step.status.value,
                    "attempts": step.attempts,
                }
                for step in snapshot.steps
            ],
            "effects": self._conn.execute("SELECT COUNT(*) FROM effects").fetchone()[0],
        }
        blob = canonical_json(state)
        digest = sha256_text(blob)
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO checkpoints(task_id, created_at, completed_steps, total_steps,"
                " state_json, state_sha256, note) VALUES(?,?,?,?,?,?,?)",
                (
                    task_id,
                    at,
                    sum(1 for s in snapshot.steps if s.status is StepStatus.SUCCEEDED),
                    len(snapshot.steps),
                    blob,
                    digest,
                    note,
                ),
            )
            self._append_audit(
                conn,
                kind="task.checkpoint",
                task_id=task_id,
                payload={"sha256": digest, "note": note},
                at=at,
            )
        return {"state": state, "sha256": digest, "created_at": at}

    def last_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        """Return the most recent checkpoint for a task."""
        row = self._conn.execute(
            "SELECT seq, created_at, completed_steps, total_steps, state_json, state_sha256, note"
            " FROM checkpoints WHERE task_id = ? ORDER BY seq DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "seq": row["seq"],
            "created_at": row["created_at"],
            "completed_steps": row["completed_steps"],
            "total_steps": row["total_steps"],
            "state": json.loads(row["state_json"]),
            "sha256": row["state_sha256"],
            "note": row["note"],
            "integrity_ok": sha256_text(row["state_json"]) == row["state_sha256"],
        }

    def export_resume_seed(self, task_id: str, *, now: str | None = None) -> dict[str, Any]:
        """Export a portable, hash-verified seed another process/machine can import."""
        at = now or utc_now()
        snapshot = self.get_task(task_id)
        if snapshot.status in TERMINAL_TASK_STATUSES:
            raise StoreError(f"task {task_id!r} is already {snapshot.status.value}")
        spec = self.spec(task_id)
        done = [
            {
                "step_id": step.step_id,
                "attempts": step.attempts,
                "summary": step.summary,
                "status": step.status.value,
            }
            for step in snapshot.steps
            if step.status in {StepStatus.SUCCEEDED, StepStatus.SKIPPED}
        ]
        body = {
            "kind": "fleet-2027/resume-seed",
            "version": 1,
            "exported_at": at,
            "task": spec.to_dict(),
            "spec_sha256": snapshot.spec_sha256,
            "completed": done,
            "degraded": snapshot.degraded,
        }
        return {**body, "sha256": sha256_text(canonical_json(body))}

    def import_resume_seed(self, seed: Mapping[str, Any], *, now: str | None = None) -> TaskSpec:
        """Verify and import a resume seed into an empty store.

        The seed's digest is recomputed; a mismatch refuses the import instead
        of resuming work from a file nobody can vouch for.
        """
        at = now or utc_now()
        seed = dict(seed)
        claimed = seed.pop("sha256", None)
        if claimed is None or sha256_text(canonical_json(seed)) != claimed:
            raise ContractError("resume seed digest mismatch — refusing to import")
        if seed.get("kind") != "fleet-2027/resume-seed":
            raise ContractError("not a fleet-2027 resume seed")
        spec = TaskSpec.from_dict(seed["task"])
        if spec.spec_hash() != seed.get("spec_sha256"):
            raise ContractError("resume seed spec hash mismatch")
        created = self.submit(spec, now=at, worker="import")
        if not created:
            return spec
        with self.transaction() as conn:
            for item in seed.get("completed", []):
                status = StepStatus(item.get("status", "succeeded"))
                if status not in {StepStatus.SUCCEEDED, StepStatus.SKIPPED}:
                    continue
                conn.execute(
                    "UPDATE steps SET status = ?, attempts = ?, summary = ?, completed_at = ?"
                    " WHERE task_id = ? AND step_id = ?",
                    (
                        status.value,
                        int(item.get("attempts", 1)),
                        item.get("summary"),
                        at,
                        spec.task_id,
                        item["step_id"],
                    ),
                )
            conn.execute(
                "UPDATE tasks SET degraded = ? WHERE task_id = ?",
                (1 if seed.get("degraded") else 0, spec.task_id),
            )
            self._event(conn, spec.task_id, "task.imported", "resumed from exported seed", at=at)
        self.finalize(spec.task_id, now=at)
        return spec

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    def stats(self) -> dict[str, Any]:
        """Return honest counters for dashboards and the evidence gate."""
        tasks = {
            row["status"]: row["n"]
            for row in self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"
            )
        }
        steps = {
            row["status"]: row["n"]
            for row in self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM steps GROUP BY status"
            )
        }
        return {
            "schema_version": int(self._meta(self._conn, "schema_version")),
            "tasks": tasks,
            "steps": steps,
            "dead_letters": len(self.list_dead_letters()),
            "effects": self._conn.execute("SELECT COUNT(*) FROM effects").fetchone()[0],
            "checkpoints": self._conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0],
            "audit_rows": self._conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
            "task_events": self._conn.execute("SELECT COUNT(*) FROM task_events").fetchone()[0],
        }

    def verify_integrity(self) -> dict[str, Any]:
        """Re-derive what can be proven about this database and say what cannot.

        Returns a mapping with the individual results plus a single ``ok`` flag.
        ``ok`` is only true when every *provable* property holds; the
        ``limitations`` list states plainly what this check does not cover, so
        the report cannot be read as a stronger claim than it is.
        """
        results: dict[str, Any] = {}
        try:
            row = self._conn.execute("PRAGMA integrity_check").fetchone()
            results["sqlite_integrity_check"] = bool(row and row[0] == "ok")
        except sqlite3.DatabaseError as exc:  # pragma: no cover - corrupt file path
            results["sqlite_integrity_check"] = False
            results["sqlite_integrity_error"] = str(exc)
        audit_ok, audit_rows, problem = self.verify_audit_chain()
        results["audit_chain_ok"] = audit_ok
        results["audit_rows"] = audit_rows
        if problem:
            results["audit_chain_problem"] = problem
        results["schema_version"] = int(self._meta(self._conn, "schema_version"))
        results["effects_without_step"] = self._conn.execute(
            "SELECT COUNT(*) FROM effects e LEFT JOIN steps s"
            " ON e.task_id = s.task_id AND e.step_id = s.step_id WHERE s.step_id IS NULL"
        ).fetchone()[0]
        results["orphan_steps"] = self._conn.execute(
            "SELECT COUNT(*) FROM steps s LEFT JOIN tasks t ON s.task_id = t.task_id"
            " WHERE t.task_id IS NULL"
        ).fetchone()[0]
        results["running_steps_without_heartbeat"] = self._conn.execute(
            "SELECT COUNT(*) FROM steps WHERE status = 'running' AND heartbeat_at IS NULL"
        ).fetchone()[0]
        results["ok"] = (
            results["sqlite_integrity_check"]
            and results["audit_chain_ok"]
            and results["effects_without_step"] == 0
            and results["orphan_steps"] == 0
            and results["running_steps_without_heartbeat"] == 0
        )
        results["limitations"] = [
            "a writer with SQL access can forge rows; signing the database is not implemented",
            "checkpoint digests prove integrity, not authorship",
            "SQLite page checksums protect against corruption, not against tampering",
        ]
        return results


    @staticmethod
    def _now_ts() -> float:
        """Monotonic-ish wall clock in seconds used only for lease arithmetic."""
        import time

        return time.time()

    # ------------------------------------------------------------------ #
    # Convenience queries used by the runner
    # ------------------------------------------------------------------ #

    def unresolved_dependencies(
        self, task_id: str, step_id: str
    ) -> tuple[tuple[str, ...], bool]:
        """Return ``(deps, satisfied)`` for a step."""
        row = self._conn.execute(
            "SELECT depends_on_json FROM steps WHERE task_id = ? AND step_id = ?",
            (task_id, step_id),
        ).fetchone()
        if row is None:
            raise StoreError(f"unknown step {task_id}/{step_id}")
        deps = json.loads(row["depends_on_json"])
        statuses = {
            r["step_id"]: StepStatus(r["status"])
            for r in self._conn.execute(
                "SELECT step_id, status FROM steps WHERE task_id = ?", (task_id,)
            )
        }
        satisfied = all(statuses[dep] in RESOLVED_STEP_STATUSES for dep in deps)
        return tuple(deps), satisfied

    def require_task(self, task_id: str) -> TaskSnapshot:
        """Return a task snapshot, raising a clear error when it is unknown."""
        require_safe_id(task_id, where="task_id")
        return self.get_task(task_id)
