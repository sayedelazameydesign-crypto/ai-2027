from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from ai2027_protocol.research_contracts import (
    IdentifierKind,
    PaperIdentifier,
    ResearchPaper,
    ResearchSource,
    ResearchSourceNotFoundError,
    ResearchSourceUnavailableError,
)
from ai2027_protocol.server import create_app

_REPO_ROOT = Path(__file__).resolve().parents[2]


def make_paper() -> ResearchPaper:
    """Create a verified local fixture shared by the research API contract tests."""
    return ResearchPaper.from_verified_source(
        paper_key="doi:10.1234/research.paper",
        source=ResearchSource.ARXIV,
        source_id="2401.01234v2",
        arxiv_id="2401.01234v2",
        doi="10.1234/research.paper",
        title="Robust Agent Evaluation in Local Systems",
        abstract="We measure an evaluation framework for local engineering agents.",
        published_at=date(2025, 1, 10),
        categories=("cs.AI",),
        authors=("Example Researcher",),
        source_url="https://arxiv.org/abs/2401.01234v2",
        fetched_at=datetime(2026, 11, 19, tzinfo=UTC),
    )


def evaluation_payload() -> dict[str, object]:
    """Return the explicit source/inference split required for a saved evaluation."""
    return {
        "source_evidence": [
            {"field": "abstract", "quote": "evaluation framework for local engineering agents"}
        ],
        "engineering_assessment": {
            "problem_and_method": "The paper measures agent behavior on structured tasks.",
            "assumptions_and_limitations": "The abstract does not prove production-scale results.",
            "system_fit": "This could guide the local agent evaluation suite.",
            "proposed_experiment": (
                "Compare a small test set to the current deterministic baseline."
            ),
            "incremental_cost": "Local compute only; no paid API is needed.",
            "likely_breakage": "An incomplete benchmark could reward undesirable behavior.",
        },
    }


def evidence_payload(*, passed: bool = True) -> dict[str, object]:
    """Return a measured experiment card with all independently named gates."""
    return {
        "experiment": "Run the candidate method against the bounded local benchmark.",
        "environment": "Python 3.13, no secrets, isolated workspace.",
        "artifact_sha256": hashlib.sha256(b"test evidence").hexdigest(),
        "gates": {
            "accuracy_passed": passed,
            "latency_passed": passed,
            "resource_passed": passed,
            "edge_cases_passed": passed,
        },
        "measurements": {"latency_ms": 12.5},
        "tested_at": "2026-11-19T00:00:00Z",
    }


class FakeGateway:
    """Injectable test source proving the API never needs a live academic call."""

    def __init__(self, paper: ResearchPaper) -> None:
        self.paper = paper
        self.identifier: PaperIdentifier | None = None
        self.query: str | None = None
        self.limit = 0
        self.search_error: Exception | None = None

    async def search(self, query: str, *, limit: int) -> tuple[ResearchPaper, ...]:
        """Return one deterministic result or a typed upstream failure."""
        self.query = query
        self.limit = limit
        if self.search_error is not None:
            raise self.search_error
        return (self.paper,)

    async def resolve(self, identifier: PaperIdentifier) -> ResearchPaper:
        """Record the canonical identifier requested by the API caller."""
        self.identifier = identifier
        if identifier.kind is not IdentifierKind.ARXIV:
            raise ResearchSourceNotFoundError("test source only has arXiv metadata")
        return self.paper


async def _client(app: FastAPI) -> httpx.AsyncClient:
    """Build an in-process client; no local TCP server or external network is used."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


@pytest.mark.asyncio
async def test_research_api_search_ingest_evaluate_test_and_audit(tmp_path: Path) -> None:
    """End-to-end route flow preserves provenance, separation, decisions, and audit hashes."""
    gateway = FakeGateway(make_paper())
    app = create_app(
        workspace_root=_REPO_ROOT,
        studio_dir=tmp_path / "studio",
        research_gateway=gateway,
    )
    async with await _client(app) as client:
        topics = await client.get("/v1/research/topics")
        assert topics.status_code == 200
        assert {topic["id"] for topic in topics.json()} >= {"agent-evaluation", "systems-security"}

        search = await client.post(
            "/v1/research/search",
            json={"topic_id": "agent-evaluation", "window_days": 3650, "limit": 5},
        )
        assert search.status_code == 200
        assert search.json()["returned_count"] == 1
        assert gateway.query is not None and "cat:cs.AI" in gateway.query
        assert gateway.limit == 25
        assert (await client.get("/v1/research/papers")).json()["returned_count"] == 0

        ingested = await client.post(
            "/v1/research/ingest",
            json={"identifier": "2401.01234", "topic_id": "agent-evaluation"},
        )
        assert ingested.status_code == 200
        assert ingested.json()["status"] == "candidate"
        assert ingested.json()["paper"]["source_url"] == "https://arxiv.org/abs/2401.01234v2"
        assert ingested.json()["paper"]["metadata_sha256"]
        assert gateway.identifier == PaperIdentifier(kind=IdentifierKind.ARXIV, value="2401.01234")

        evaluated = await client.post(
            "/v1/research/evaluation",
            json={"identifier": "doi:10.1234/research.paper", "evaluation": evaluation_payload()},
        )
        assert evaluated.status_code == 200
        assert evaluated.json()["evaluation"]["source_evidence"][0]["field"] == "abstract"
        assert "engineering_assessment" in evaluated.json()["evaluation"]

        tested = await client.post(
            "/v1/research/decision",
            json={
                "identifier": "2401.01234",
                "status": "tested",
                "evidence": evidence_payload(),
            },
        )
        assert tested.status_code == 200
        assert tested.json()["status"] == "tested"
        assert tested.json()["test_evidence"]["gates"]["edge_cases_passed"] is True

        listed = await client.get("/v1/research/papers", params={"status": "tested"})
        assert listed.status_code == 200
        assert listed.json()["integrity_verified"] is True
        assert listed.json()["returned_count"] == 1
        audit = await client.get("/v1/research/audit")
        assert audit.status_code == 200
        assert audit.json()["integrity_valid"] is True
        assert audit.json()["event_count"] == 3
        assert [event["event_type"] for event in audit.json()["events"]] == [
            "paper_ingested",
            "evaluation_saved",
            "status_decided",
        ]


@pytest.mark.asyncio
async def test_research_api_rejects_invalid_topic_identifier_and_unverified_excerpt(
    tmp_path: Path,
) -> None:
    """Input validation and source excerpt checks block fabricated or unscoped candidates."""
    app = create_app(
        workspace_root=_REPO_ROOT,
        studio_dir=tmp_path / "studio",
        research_gateway=FakeGateway(make_paper()),
    )
    async with await _client(app) as client:
        bad_topic = await client.post("/v1/research/search", json={"topic_id": "not-configured"})
        assert bad_topic.status_code == 422
        arbitrary_url = await client.post(
            "/v1/research/ingest",
            json={"identifier": "https://evil.example/paper", "topic_id": "agent-evaluation"},
        )
        assert arbitrary_url.status_code == 422
        unknown_identifier = await client.post(
            "/v1/research/ingest",
            json={"identifier": "10.9876/does-not-exist", "topic_id": "agent-evaluation"},
        )
        assert unknown_identifier.status_code == 404
        ingested = await client.post(
            "/v1/research/ingest",
            json={"identifier": "2401.01234", "topic_id": "agent-evaluation"},
        )
        assert ingested.status_code == 200
        fake_quote = evaluation_payload()
        evidence = fake_quote["source_evidence"]
        assert isinstance(evidence, list)
        evidence[0] = {"field": "abstract", "quote": "unsupported claim from model memory"}
        invalid = await client.post(
            "/v1/research/evaluation",
            json={"identifier": "2401.01234", "evaluation": fake_quote},
        )
        assert invalid.status_code == 503
        assert "integrity" in invalid.text.lower() or "verified source" in invalid.text.lower()


@pytest.mark.asyncio
async def test_research_api_fail_closed_source_errors_and_read_only_mutations(
    tmp_path: Path,
) -> None:
    """Upstream failures are honest 503s and read-only mode allows search but blocks writes."""
    gateway = FakeGateway(make_paper())
    gateway.search_error = ResearchSourceUnavailableError("Academic source is down.")
    ro_app = create_app(
        workspace_root=_REPO_ROOT,
        studio_dir=tmp_path / "ro",
        read_only=True,
        research_gateway=gateway,
    )
    async with await _client(ro_app) as client:
        down = await client.post("/v1/research/search", json={"topic_id": "agent-evaluation"})
        assert down.status_code == 503
        gateway.search_error = None
        search = await client.post("/v1/research/search", json={"topic_id": "agent-evaluation"})
        assert search.status_code == 200
        blocked = await client.post(
            "/v1/research/ingest",
            json={"identifier": "2401.01234", "topic_id": "agent-evaluation"},
        )
        assert blocked.status_code == 403
        blocked_eval = await client.post(
            "/v1/research/evaluation",
            json={"identifier": "2401.01234", "evaluation": evaluation_payload()},
        )
        assert blocked_eval.status_code == 403
        blocked_decision = await client.post(
            "/v1/research/decision",
            json={"identifier": "2401.01234", "status": "rejected", "reason": "No fit."},
        )
        assert blocked_decision.status_code == 403


@pytest.mark.asyncio
async def test_research_api_requires_test_evidence_and_reports_tampering(tmp_path: Path) -> None:
    """Decision and registry routes refuse missing experiment proof or modified DB state."""
    app = create_app(
        workspace_root=_REPO_ROOT,
        studio_dir=tmp_path / "studio",
        research_gateway=FakeGateway(make_paper()),
    )
    async with await _client(app) as client:
        await client.post(
            "/v1/research/ingest",
            json={"identifier": "2401.01234", "topic_id": "agent-evaluation"},
        )
        missing_evidence = await client.post(
            "/v1/research/decision",
            json={"identifier": "2401.01234", "status": "tested"},
        )
        assert missing_evidence.status_code == 409
        bad_gates = await client.post(
            "/v1/research/decision",
            json={
                "identifier": "2401.01234",
                "status": "tested",
                "evidence": evidence_payload(passed=False),
            },
        )
        assert bad_gates.status_code == 409
        db = app.state.research_store.path
        with sqlite3.connect(db) as connection:
            connection.execute("UPDATE research_papers SET status = 'rejected'")
        tampered = await client.get("/v1/research/papers")
        assert tampered.status_code == 503
        audit = await client.get("/v1/research/audit")
        assert audit.status_code == 503
