"""Append-only hash-chain checks for the local Research Radar registry."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from typing import Any

from .research_contracts import (
    ResearchEvaluation,
    ResearchPaper,
    ResearchRecord,
    ResearchStatus,
    TestEvidence,
)
from .research_store_errors import ResearchIntegrityError

_ZERO_HASH = "0" * 64


def _canonical_json(value: object) -> str:
    """Serialize audit/state data deterministically and reject non-JSON values."""
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
    """Hash UTF-8 data using the registry's single SHA-256 representation."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _event_hash(
    *,
    event_id: int,
    paper_key: str,
    event_type: str,
    actor: str,
    occurred_at: str,
    details_json: str,
    previous_hash: str,
) -> str:
    """Compute a chained event digest from its immutable fields."""
    event = {
        "event_id": event_id,
        "paper_key": paper_key,
        "event_type": event_type,
        "actor": actor,
        "occurred_at": occurred_at,
        "details_json": details_json,
        "previous_hash": previous_hash,
    }
    return _sha256(_canonical_json(event))


def _current_state(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, str | None]:
    """Hash every mutable row component plus aliases/source checks for comparison."""
    aliases = [
        [item["kind"], item["value"]]
        for item in connection.execute(
            "SELECT kind, value FROM research_identifiers WHERE paper_key = ? ORDER BY kind, value",
            (row["paper_key"],),
        ).fetchall()
    ]
    source_checks = [
        [item["source"], item["source_id"], item["paper_digest"]]
        for item in connection.execute(
            "SELECT source, source_id, paper_digest FROM research_source_checks"
            " WHERE paper_key = ? ORDER BY source, source_id",
            (row["paper_key"],),
        ).fetchall()
    ]
    return {
        "paper_json_sha256": _sha256(row["paper_json"]),
        "topics_json_sha256": _sha256(row["topics_json"]),
        "status": str(row["status"]),
        "evaluation_json_sha256": (
            _sha256(row["evaluation_json"]) if row["evaluation_json"] is not None else None
        ),
        "decision_reason_sha256": (
            _sha256(row["decision_reason"]) if row["decision_reason"] is not None else None
        ),
        "test_evidence_json_sha256": (
            _sha256(row["test_evidence_json"]) if row["test_evidence_json"] is not None else None
        ),
        "identifiers_sha256": _sha256(_canonical_json(aliases)),
        "source_checks_sha256": _sha256(_canonical_json(source_checks)),
    }


def _validate_paper_row(row: sqlite3.Row) -> ResearchPaper:
    """Validate the stored metadata and triage state before it is trusted or returned."""
    try:
        paper = ResearchPaper.model_validate_json(row["paper_json"])
        raw_topics = json.loads(row["topics_json"])
        if not isinstance(raw_topics, list) or not all(
            isinstance(topic, str) for topic in raw_topics
        ):
            raise ValueError("topic list is malformed")
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
        ResearchRecord(
            paper=paper,
            topics=tuple(raw_topics),
            status=ResearchStatus(row["status"]),
            evaluation=evaluation,
            decision_reason=row["decision_reason"],
            test_evidence=evidence,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ResearchIntegrityError("Stored research record failed schema validation.") from exc
    if paper.paper_key != row["paper_key"]:
        raise ResearchIntegrityError("Stored paper key differs from its verified metadata.")
    return paper


class ResearchAuditLedger:
    """Centralize transactional event appends and fail-closed integrity verification."""

    @staticmethod
    def append(
        connection: sqlite3.Connection,
        *,
        paper_key: str,
        event_type: str,
        actor: str,
        occurred_at: str,
        facts: dict[str, object],
    ) -> None:
        """Append a current-state-bound audit event inside the caller's transaction."""
        if actor not in {"agent", "human", "system"}:
            raise ResearchIntegrityError("Research audit actor must be agent, human, or system.")
        row = connection.execute(
            "SELECT * FROM research_papers WHERE paper_key = ?", (paper_key,)
        ).fetchone()
        if row is None:
            raise ResearchIntegrityError("Cannot append an event for a missing paper.")
        details = dict(facts)
        details["state"] = _current_state(connection, row)
        details_json = _canonical_json(details)
        latest = connection.execute(
            "SELECT event_hash FROM research_audit ORDER BY event_id DESC LIMIT 1"
        ).fetchone()
        previous_hash = str(latest["event_hash"]) if latest is not None else _ZERO_HASH
        cursor = connection.execute(
            "INSERT INTO research_audit"
            " (paper_key, event_type, actor, occurred_at, details_json, previous_hash, event_hash)"
            " VALUES (?, ?, ?, ?, ?, ?, '')",
            (paper_key, event_type, actor, occurred_at, details_json, previous_hash),
        )
        if cursor.lastrowid is None:
            raise ResearchIntegrityError("Database did not return the inserted audit event ID.")
        event_id = cursor.lastrowid
        digest = _event_hash(
            event_id=event_id,
            paper_key=paper_key,
            event_type=event_type,
            actor=actor,
            occurred_at=occurred_at,
            details_json=details_json,
            previous_hash=previous_hash,
        )
        connection.execute(
            "UPDATE research_audit SET event_hash = ? WHERE event_id = ?", (digest, event_id)
        )

    @staticmethod
    def assert_valid(connection: sqlite3.Connection) -> None:
        """Verify all events, current rows, alias sets, and source-check snapshots."""
        previous_hash = _ZERO_HASH
        latest_state: dict[str, dict[str, Any]] = {}
        events = connection.execute("SELECT * FROM research_audit ORDER BY event_id").fetchall()
        for row in events:
            if row["previous_hash"] != previous_hash:
                raise ResearchIntegrityError("Research audit chain has a broken predecessor hash.")
            try:
                details = json.loads(row["details_json"])
            except json.JSONDecodeError as exc:
                raise ResearchIntegrityError("Research audit event contains invalid JSON.") from exc
            if not isinstance(details, dict) or not isinstance(details.get("state"), dict):
                raise ResearchIntegrityError("Research audit event is missing its state snapshot.")
            if _canonical_json(details) != row["details_json"]:
                raise ResearchIntegrityError("Research audit event is not canonically serialized.")
            expected = _event_hash(
                event_id=int(row["event_id"]),
                paper_key=str(row["paper_key"]),
                event_type=str(row["event_type"]),
                actor=str(row["actor"]),
                occurred_at=str(row["occurred_at"]),
                details_json=str(row["details_json"]),
                previous_hash=str(row["previous_hash"]),
            )
            if row["event_hash"] != expected:
                raise ResearchIntegrityError("Research audit event hash is invalid.")
            previous_hash = expected
            latest_state[str(row["paper_key"])] = details["state"]

        papers = connection.execute("SELECT * FROM research_papers ORDER BY paper_key").fetchall()
        paper_keys = {str(row["paper_key"]) for row in papers}
        if paper_keys != set(latest_state):
            raise ResearchIntegrityError("Registry records and audit history do not match.")
        for row in papers:
            _validate_paper_row(row)
            if latest_state[str(row["paper_key"])] != _current_state(connection, row):
                raise ResearchIntegrityError("Research row differs from its last audited state.")

        checks = connection.execute("SELECT * FROM research_source_checks").fetchall()
        for check in checks:
            if _sha256(str(check["paper_json"])) != check["paper_digest"]:
                raise ResearchIntegrityError("Stored independent-source check digest is invalid.")
            try:
                checked_paper = ResearchPaper.model_validate_json(check["paper_json"])
            except ValueError as exc:
                raise ResearchIntegrityError(
                    "Stored independent-source record is malformed."
                ) from exc
            if (
                checked_paper.source.value != check["source"]
                or checked_paper.source_id != check["source_id"]
                or checked_paper.paper_key != check["paper_key"]
            ):
                raise ResearchIntegrityError(
                    "Independent-source record is bound to the wrong paper."
                )
