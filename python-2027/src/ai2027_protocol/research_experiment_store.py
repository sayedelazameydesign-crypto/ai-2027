"""Durable, auditable persistence of experiment results for the Research Radar.

Every experiment result — including failures and timeouts — is recorded here
without changing a paper's registry status. Adoption still requires a separate
authenticated human decision via ``ResearchStore.decide``. Extracting this
logic keeps ``research_store.py`` within the project's small-module rule.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .research_contracts import TestEvidence
from .research_ledger import ResearchAuditLedger


def experiment_results_schema() -> str:
    """Return the DDL for the durable experiment-results table."""
    return """
CREATE TABLE IF NOT EXISTS research_experiment_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_key TEXT NOT NULL REFERENCES research_papers(paper_key) ON DELETE CASCADE,
    artifact_sha256 TEXT NOT NULL,
    experiment TEXT NOT NULL,
    environment TEXT NOT NULL,
    gates_json TEXT NOT NULL,
    measurements_json TEXT NOT NULL,
    tested_at TEXT NOT NULL,
    all_passed INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS research_experiment_results_by_paper
    ON research_experiment_results (paper_key);
"""


def record_experiment_result(
    connection: sqlite3.Connection,
    paper_key: str,
    evidence: TestEvidence,
    actor: str,
) -> int:
    """Insert one experiment result and an audited event inside the caller's txn."""
    from .research_store import _canonical_json, _now

    gates_json = _canonical_json(evidence.gates.model_dump(mode="json"))
    measurements_json = _canonical_json(evidence.measurements)
    cursor = connection.execute(
        "INSERT INTO research_experiment_results"
        " (paper_key, artifact_sha256, experiment, environment, gates_json,"
        "  measurements_json, tested_at, all_passed)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            paper_key,
            evidence.artifact_sha256,
            evidence.experiment,
            evidence.environment,
            gates_json,
            measurements_json,
            evidence.tested_at.isoformat(),
            1 if evidence.gates.all_passed else 0,
        ),
    )
    ResearchAuditLedger.append(
        connection,
        paper_key=paper_key,
        event_type="experiment_result_recorded",
        actor=actor,
        occurred_at=_now(),
        facts={
            "artifact_sha256": evidence.artifact_sha256,
            "all_passed": bool(evidence.gates.all_passed),
            "measurements": evidence.measurements,
        },
    )
    result_id = cursor.lastrowid
    if result_id is None:
        from .research_store_errors import ResearchIntegrityError

        raise ResearchIntegrityError("Experiment result insert did not return an id.")
    return int(result_id)


def list_experiment_results(
    connection: sqlite3.Connection, paper_key: str, *, limit: int
) -> tuple[dict[str, Any], ...]:
    """Return the durable, auditable experiment results recorded for a paper."""
    rows = connection.execute(
        "SELECT * FROM research_experiment_results WHERE paper_key = ? ORDER BY id DESC LIMIT ?",
        (paper_key, limit),
    ).fetchall()
    return tuple(
        {
            "id": int(r["id"]),
            "paper_key": str(r["paper_key"]),
            "artifact_sha256": str(r["artifact_sha256"]),
            "experiment": str(r["experiment"]),
            "environment": str(r["environment"]),
            "gates": json.loads(r["gates_json"]),
            "measurements": json.loads(r["measurements_json"]),
            "tested_at": str(r["tested_at"]),
            "all_passed": bool(r["all_passed"]),
        }
        for r in rows
    )


__all__ = [
    "experiment_results_schema",
    "list_experiment_results",
    "record_experiment_result",
]
