"""SQLite research registry with deduplication and a tamper-evident audit chain.

Only records returned by the trusted source gateway should reach ``add_candidate``. The store
keeps the provider snapshot, agent evaluation, human decision, evidence hashes, identifier aliases,
and a chained audit event in one local transaction. A failed integrity check blocks reads/writes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from .research_contracts import (
    ResearchEvaluation,
    ResearchPaper,
    ResearchRecord,
    ResearchStatus,
    TestEvidence,
    parse_paper_identifier,
)
from .research_experiment_store import (
    experiment_results_schema,
    list_experiment_results,
    record_experiment_result,
)
from .research_ledger import ResearchAuditLedger
from .research_store_errors import (
    ResearchConflictError,
    ResearchIntegrityError,
    ResearchNotFoundError,
    ResearchTransitionError,
)

_ZERO_HASH = "0" * 64
_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_papers (
    paper_key TEXT PRIMARY KEY,
    paper_json TEXT NOT NULL,
    topics_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('candidate', 'tested', 'rejected')),
    evaluation_json TEXT,
    decision_reason TEXT,
    test_evidence_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_identifiers (
    kind TEXT NOT NULL CHECK (kind IN ('arxiv', 'doi')),
    value TEXT NOT NULL,
    paper_key TEXT NOT NULL REFERENCES research_papers(paper_key) ON DELETE CASCADE,
    PRIMARY KEY (kind, value)
);
CREATE INDEX IF NOT EXISTS research_identifiers_by_paper
    ON research_identifiers (paper_key);
CREATE TABLE IF NOT EXISTS research_source_checks (
    paper_key TEXT NOT NULL REFERENCES research_papers(paper_key) ON DELETE CASCADE,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    paper_json TEXT NOT NULL,
    paper_digest TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    PRIMARY KEY (paper_key, source, source_id)
);
CREATE TABLE IF NOT EXISTS research_audit (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_key TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    details_json TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL
);
"""


def _canonical_json(value: object) -> str:
    """Serialize data deterministically for hashing and local storage."""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ResearchIntegrityError(
            "Research record contains a non-canonical JSON value."
        ) from exc


def _sha256(value: str) -> str:
    """Hash UTF-8 data using the protocol's single SHA-256 representation."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> str:
    """Return an explicit UTC timestamp for the local audit ledger."""
    return datetime.now(UTC).isoformat()


def _normalized_text(value: str) -> str:
    """Normalize whitespace and case for exact source title comparisons."""
    return " ".join(value.split()).casefold()


def _record_from_row(row: sqlite3.Row) -> ResearchRecord:
    """Rehydrate and validate the current public state from a SQLite row."""
    try:
        paper = ResearchPaper.model_validate_json(row["paper_json"])
        topics_raw = json.loads(row["topics_json"])
        if not isinstance(topics_raw, list) or not all(
            isinstance(item, str) for item in topics_raw
        ):
            raise ValueError("topics are not a string list")
        evaluation = (
            ResearchEvaluation.model_validate_json(row["evaluation_json"])
            if row["evaluation_json"] is not None
            else None
        )
        evidence = (
            TestEvidence.model_validate_json(row["test_evidence_json"])
            if row["test_evidence_json"] is not None
            else None
        )
        return ResearchRecord(
            paper=paper,
            topics=tuple(topics_raw),
            status=ResearchStatus(row["status"]),
            evaluation=evaluation,
            decision_reason=row["decision_reason"],
            test_evidence=evidence,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ResearchIntegrityError("Stored research record failed schema validation.") from exc


class ResearchStore:
    """Local SQLite registry enforcing verified ingestion, legal transitions, and audit checks."""

    def __init__(self, path: Path) -> None:
        """Create or open a private local registry database at ``path``."""
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(_SCHEMA)
            connection.executescript(experiment_results_schema())
            connection.commit()
        if os.name != "nt" and self.path.exists():
            self.path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        """Open a short-lived SQLite connection with bounded lock wait and FK checks."""
        connection = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @staticmethod
    def _aliases(paper: ResearchPaper) -> tuple[tuple[str, str], ...]:
        """Return identifier aliases in stable order for deduplication."""
        return tuple(
            sorted((identifier.kind.value, identifier.value) for identifier in paper.identifiers)
        )

    @staticmethod
    def _paper_json(paper: ResearchPaper) -> str:
        """Serialize all normalized verified metadata, including its digest."""
        return _canonical_json(paper.model_dump(mode="json"))

    @staticmethod
    def _source_digest(paper_json: str) -> str:
        """Hash the complete provider-bound normalized record stored in source_checks."""
        return _sha256(paper_json)

    def _assert_integrity(self, connection: sqlite3.Connection) -> None:
        """Fail closed if the audit ledger and stored state differ."""
        ResearchAuditLedger.assert_valid(connection)

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        paper_key: str,
        event_type: str,
        actor: str,
        facts: dict[str, object],
    ) -> None:
        """Append an audited state snapshot inside the caller's transaction."""
        ResearchAuditLedger.append(
            connection,
            paper_key=paper_key,
            event_type=event_type,
            actor=actor,
            occurred_at=_now(),
            facts=facts,
        )

    @staticmethod
    def _find_existing_key(
        connection: sqlite3.Connection, aliases: tuple[tuple[str, str], ...]
    ) -> set[str]:
        """Find papers already bound to any canonical DOI/arXiv alias."""
        if not aliases:
            return set()
        matching_keys: set[str] = set()
        for kind, value in aliases:
            rows = connection.execute(
                "SELECT paper_key FROM research_identifiers WHERE kind = ? AND value = ?",
                (kind, value),
            ).fetchall()
            matching_keys.update(str(row["paper_key"]) for row in rows)
        return matching_keys

    @staticmethod
    def _load_row(connection: sqlite3.Connection, paper_key: str) -> sqlite3.Row:
        """Fetch one existing registry row or raise a typed not-found result."""
        row = connection.execute(
            "SELECT * FROM research_papers WHERE paper_key = ?", (paper_key,)
        ).fetchone()
        if row is None:
            raise ResearchNotFoundError("Verified paper is not in the local registry.")
        return cast(sqlite3.Row, row)

    def _record_for_identifier(
        self, connection: sqlite3.Connection, raw_identifier: str
    ) -> ResearchRecord:
        """Resolve a normalized alias to its independently validated paper record."""
        identifier = parse_paper_identifier(raw_identifier)
        row = connection.execute(
            "SELECT p.* FROM research_papers AS p JOIN research_identifiers AS i"
            " ON p.paper_key = i.paper_key WHERE i.kind = ? AND i.value = ?",
            (identifier.kind.value, identifier.value),
        ).fetchone()
        if row is None:
            raise ResearchNotFoundError("Verified paper is not in the local registry.")
        return _record_from_row(row)

    def add_candidate(
        self,
        paper: ResearchPaper,
        *,
        topic_id: str,
        actor: str = "agent",
    ) -> ResearchRecord:
        """Persist verified source metadata as a candidate and log provenance atomically."""
        if not _topic_id_safe(topic_id):
            raise ValueError("topic_id must be a configured lower-case slug.")
        aliases = self._aliases(paper)
        if not aliases:
            raise ResearchIntegrityError("Verified paper has no DOI or arXiv alias.")
        paper_json = self._paper_json(paper)
        now = _now()
        conflict_message: str | None = None
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_integrity(connection)
            matching_keys = self._find_existing_key(connection, aliases)
            if len(matching_keys) > 1:
                raise ResearchConflictError(
                    "DOI/arXiv aliases point to multiple records; manual review required."
                )
            if not matching_keys:
                connection.execute(
                    "INSERT INTO research_papers"
                    " (paper_key, paper_json, topics_json, status, evaluation_json,"
                    " decision_reason, test_evidence_json, created_at, updated_at)"
                    " VALUES (?, ?, ?, 'candidate', NULL, NULL, NULL, ?, ?)",
                    (paper.paper_key, paper_json, _canonical_json([topic_id]), now, now),
                )
                for kind, value in aliases:
                    connection.execute(
                        "INSERT INTO research_identifiers "
                        "(kind, value, paper_key) VALUES (?, ?, ?)",
                        (kind, value, paper.paper_key),
                    )
                check_digest = self._source_digest(paper_json)
                connection.execute(
                    "INSERT INTO research_source_checks"
                    " (paper_key, source, source_id, paper_json, paper_digest, checked_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        paper.paper_key,
                        paper.source.value,
                        paper.source_id,
                        paper_json,
                        check_digest,
                        now,
                    ),
                )
                self._append_event(
                    connection,
                    paper_key=paper.paper_key,
                    event_type="paper_ingested",
                    actor=actor,
                    facts={
                        "source": paper.source.value,
                        "source_id": paper.source_id,
                        "identifiers": [f"{kind}:{value}" for kind, value in aliases],
                        "topic_id": topic_id,
                        "metadata_sha256": paper.metadata_sha256,
                        "fetched_at": paper.fetched_at.isoformat(),
                    },
                )
                stored_key = paper.paper_key
            else:
                stored_key = next(iter(matching_keys))
                row = self._load_row(connection, stored_key)
                existing = _record_from_row(row)
                if (
                    _normalized_text(existing.paper.title) != _normalized_text(paper.title)
                    or existing.paper.published_at != paper.published_at
                ):
                    conflict_message = (
                        "Source title/date conflicts with the existing DOI/arXiv record; "
                        "candidate was not overwritten."
                    )
                else:
                    checked = connection.execute(
                        "SELECT paper_digest FROM research_source_checks"
                        " WHERE paper_key = ? AND source = ? AND source_id = ?",
                        (stored_key, paper.source.value, paper.source_id),
                    ).fetchone()
                    incoming_digest = self._source_digest(paper_json)
                    if checked is not None and checked["paper_digest"] != incoming_digest:
                        conflict_message = (
                            "The same source identifier returned changed metadata; "
                            "refresh requires explicit review."
                        )
                    else:
                        if checked is None:
                            connection.execute(
                                "INSERT INTO research_source_checks"
                                " (paper_key, source, source_id, paper_json, paper_digest,"
                                " checked_at)"
                                " VALUES (?, ?, ?, ?, ?, ?)",
                                (
                                    stored_key,
                                    paper.source.value,
                                    paper.source_id,
                                    paper_json,
                                    incoming_digest,
                                    now,
                                ),
                            )
                        for kind, value in aliases:
                            connection.execute(
                                "INSERT OR IGNORE INTO research_identifiers"
                                " (kind, value, paper_key) VALUES (?, ?, ?)",
                                (kind, value, stored_key),
                            )
                        topics = set(json.loads(row["topics_json"]))
                        topics.add(topic_id)
                        topics_json = _canonical_json(sorted(topics))
                        connection.execute(
                            "UPDATE research_papers SET topics_json = ?, updated_at = ?"
                            " WHERE paper_key = ?",
                            (topics_json, now, stored_key),
                        )
                        self._append_event(
                            connection,
                            paper_key=stored_key,
                            event_type=(
                                "source_crosscheck_added" if checked is None else "paper_reverified"
                            ),
                            actor=actor,
                            facts={
                                "source": paper.source.value,
                                "source_id": paper.source_id,
                                "metadata_sha256": paper.metadata_sha256,
                                "topic_id": topic_id,
                                "fetched_at": paper.fetched_at.isoformat(),
                            },
                        )
                if conflict_message:
                    self._append_event(
                        connection,
                        paper_key=stored_key,
                        event_type="source_metadata_conflict",
                        actor="system",
                        facts={
                            "source": paper.source.value,
                            "source_id": paper.source_id,
                            "incoming_metadata_sha256": paper.metadata_sha256,
                            "reason": conflict_message,
                        },
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        if conflict_message:
            raise ResearchConflictError(conflict_message)
        return self.get_by_key(stored_key)

    def get(self, raw_identifier: str) -> ResearchRecord:
        """Read one paper by DOI/arXiv alias after checking the entire integrity chain."""
        connection = self._connect()
        try:
            self._assert_integrity(connection)
            return self._record_for_identifier(connection, raw_identifier)
        finally:
            connection.close()

    def get_by_key(self, paper_key: str) -> ResearchRecord:
        """Read one paper by its internal stable key after integrity verification."""
        connection = self._connect()
        try:
            self._assert_integrity(connection)
            return _record_from_row(self._load_row(connection, paper_key))
        finally:
            connection.close()

    def list_records(
        self,
        *,
        status: ResearchStatus | None = None,
        limit: int = 100,
    ) -> tuple[ResearchRecord, ...]:
        """List newest records only when all registry and audit hashes verify."""
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200.")
        connection = self._connect()
        try:
            self._assert_integrity(connection)
            if status is None:
                rows = connection.execute(
                    "SELECT * FROM research_papers ORDER BY updated_at DESC, paper_key LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM research_papers WHERE status = ?"
                    " ORDER BY updated_at DESC, paper_key LIMIT ?",
                    (status.value, limit),
                ).fetchall()
            return tuple(_record_from_row(row) for row in rows)
        finally:
            connection.close()

    def save_evaluation(
        self,
        raw_identifier: str,
        evaluation: ResearchEvaluation,
        *,
        actor: str = "agent",
    ) -> ResearchRecord:
        """Save an analysis only when each source quote occurs in the fetched title/abstract."""
        evaluation_json = _canonical_json(evaluation.model_dump(mode="json"))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_integrity(connection)
            current = self._record_for_identifier(connection, raw_identifier)
            for evidence in evaluation.source_evidence:
                source_text = (
                    current.paper.title if evidence.field == "title" else current.paper.abstract
                )
                if _normalized_text(evidence.quote) not in _normalized_text(source_text):
                    raise ResearchIntegrityError(
                        "Evaluation quote does not occur in the verified source title/abstract."
                    )
            now = _now()
            connection.execute(
                "UPDATE research_papers SET evaluation_json = ?, updated_at = ? "
                "WHERE paper_key = ?",
                (evaluation_json, now, current.paper.paper_key),
            )
            self._append_event(
                connection,
                paper_key=current.paper.paper_key,
                event_type="evaluation_saved",
                actor=actor,
                facts={
                    "evaluation_sha256": _sha256(evaluation_json),
                    "evidence_quote_count": len(evaluation.source_evidence),
                },
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_by_key(current.paper.paper_key)

    def decide(
        self,
        raw_identifier: str,
        *,
        status: ResearchStatus,
        reason: str | None = None,
        evidence: TestEvidence | None = None,
        actor: str = "human",
        proof: str | None = None,
    ) -> ResearchRecord:
        """Move a candidate to tested/rejected only with the required independent evidence.

        A human decision is a privileged write: ``actor="human"`` is never granted
        automatically. Callers acting on behalf of a human must supply a non-empty
        ``proof`` token, so an unauthenticated or proxied request cannot mint a
        "human" decision into the tamper-evident audit chain.
        """
        try:
            status = ResearchStatus(status)
        except ValueError as exc:
            raise ResearchTransitionError("Decision status must be tested or rejected.") from exc
        if status is ResearchStatus.CANDIDATE:
            raise ResearchTransitionError("A decision must be tested or rejected, not candidate.")
        if actor == "human" and not (proof and proof.strip()):
            raise ResearchTransitionError("A human decision requires an authenticated proof token.")
        if status is ResearchStatus.TESTED:
            if evidence is None or not evidence.gates.all_passed:
                raise ResearchTransitionError(
                    "Tested requires all four verification gates to pass."
                )
            if reason is not None:
                raise ResearchTransitionError("Tested decisions cannot carry a rejection reason.")
        elif status is ResearchStatus.REJECTED:
            if evidence is not None or reason is None or not reason.strip():
                raise ResearchTransitionError("Rejected requires a reason and no test evidence.")
            reason = reason.strip()
            if len(reason) > 1000:
                raise ResearchTransitionError("Rejection reason must not exceed 1000 characters.")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_integrity(connection)
            current = self._record_for_identifier(connection, raw_identifier)
            if current.status is not ResearchStatus.CANDIDATE:
                raise ResearchTransitionError(
                    "Only candidate papers may receive a terminal decision."
                )
            now = _now()
            evidence_json = (
                _canonical_json(evidence.model_dump(mode="json")) if evidence is not None else None
            )
            connection.execute(
                "UPDATE research_papers SET status = ?, decision_reason = ?, "
                "test_evidence_json = ?, updated_at = ? WHERE paper_key = ?",
                (status.value, reason, evidence_json, now, current.paper.paper_key),
            )
            self._append_event(
                connection,
                paper_key=current.paper.paper_key,
                event_type="status_decided",
                actor=actor,
                facts={
                    "from": current.status.value,
                    "to": status.value,
                    "reason": reason,
                    "test_evidence_sha256": _sha256(evidence_json) if evidence_json else None,
                },
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_by_key(current.paper.paper_key)

    def record_experiment_result(
        self, raw_identifier: str, evidence: TestEvidence, *, actor: str = "agent"
    ) -> int:
        """Persist every result (incl. failures) durably; ``tested`` needs :meth:`decide`."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_integrity(connection)
            paper_key = self._record_for_identifier(connection, raw_identifier).paper.paper_key
            result_id = record_experiment_result(connection, paper_key, evidence, actor)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return result_id

    def list_experiment_results(
        self, raw_identifier: str, *, limit: int = 50
    ) -> tuple[dict[str, Any], ...]:
        """Return durable, auditable experiment results for a paper."""
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200.")
        connection = self._connect()
        try:
            self._assert_integrity(connection)
            paper_key = self._record_for_identifier(connection, raw_identifier).paper.paper_key
            return list_experiment_results(connection, paper_key, limit=limit)
        finally:
            connection.close()

    def audit_report(self, *, limit: int = 200) -> dict[str, Any]:
        """Verify the full local chain and return a bounded tail for human review."""
        if not 1 <= limit <= 1000:
            raise ValueError("audit limit must be between 1 and 1000.")
        connection = self._connect()
        try:
            self._assert_integrity(connection)
            rows = connection.execute(
                "SELECT * FROM research_audit ORDER BY event_id DESC LIMIT ?", (limit,)
            ).fetchall()
            events = [
                {
                    "event_id": int(row["event_id"]),
                    "paper_key": str(row["paper_key"]),
                    "event_type": str(row["event_type"]),
                    "actor": str(row["actor"]),
                    "occurred_at": str(row["occurred_at"]),
                    "details": json.loads(row["details_json"]),
                    "previous_hash": str(row["previous_hash"]),
                    "event_hash": str(row["event_hash"]),
                }
                for row in reversed(rows)
            ]
            head = connection.execute(
                "SELECT event_hash FROM research_audit ORDER BY event_id DESC LIMIT 1"
            ).fetchone()
            return {
                "integrity_valid": True,
                "event_count": int(
                    connection.execute("SELECT COUNT(*) FROM research_audit").fetchone()[0]
                ),
                "head_hash": str(head["event_hash"]) if head is not None else _ZERO_HASH,
                "events": events,
            }
        finally:
            connection.close()


def _topic_id_safe(value: str) -> bool:
    """Keep storage topic IDs aligned with the loader's slug contract."""
    return re.fullmatch(r"[a-z][a-z0-9-]{1,48}", value) is not None
