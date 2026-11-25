"""FastAPI routes for explicit, source-verified Research Radar operations."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Literal, NoReturn

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field

from .contracts import StrictModel
from .research_contracts import (
    ResearchError,
    ResearchEvaluation,
    ResearchGateway,
    ResearchInputError,
    ResearchPaper,
    ResearchRecord,
    ResearchSourceNotFoundError,
    ResearchSourceUnavailableError,
    ResearchStatus,
    ResearchTopic,
    TestEvidence,
    load_research_topics,
)
from .research_radar import ResearchRadar
from .research_sources import OpenAcademicGateway
from .research_store import ResearchStore
from .research_store_errors import (
    ResearchConflictError,
    ResearchIntegrityError,
    ResearchNotFoundError,
    ResearchStoreError,
    ResearchTransitionError,
)


class ResearchSearchRequest(StrictModel):
    """A manually triggered search constrained to one configured research topic."""

    topic_id: str = Field(min_length=2, max_length=49)
    window_days: int = Field(default=180, ge=1, le=3650)
    limit: int = Field(default=10, ge=1, le=20)


class ResearchSearchResponse(StrictModel):
    """Recent source records returned without automatically persisting candidates."""

    topic_id: str
    window_days: int
    returned_count: int
    papers: tuple[ResearchPaper, ...]


class ResearchIngestRequest(StrictModel):
    """An explicit canonical identifier selected by a human/agent after search."""

    identifier: str = Field(min_length=1, max_length=200)
    topic_id: str = Field(min_length=2, max_length=49)


class ResearchEvaluationRequest(StrictModel):
    """Separated source quotes and engineering inference for a stored candidate."""

    identifier: str = Field(min_length=1, max_length=200)
    evaluation: ResearchEvaluation


class ResearchDecisionRequest(StrictModel):
    """Human decision payload; tested requires gates and a content-addressed artifact.

    ``proof`` is a required authenticated token for any decision: an
    unauthenticated caller cannot mint a ``human`` decision into the audit
    chain, so ``actor="human"`` is never granted automatically.
    """

    identifier: str = Field(min_length=1, max_length=200)
    status: Literal["tested", "rejected"]
    reason: str | None = Field(default=None, max_length=1000)
    evidence: TestEvidence | None = None
    proof: str = Field(min_length=1, max_length=2000)


class ResearchPaperListResponse(StrictModel):
    """Bounded local registry response with an explicit integrity result."""

    integrity_verified: bool = True
    returned_count: int
    papers: tuple[ResearchRecord, ...]


class ResearchAuditResponse(StrictModel):
    """Tamper-evident audit-chain tail verified against the entire local history."""

    integrity_valid: bool
    event_count: int
    head_hash: str
    events: tuple[dict[str, object], ...]


def _raise_http(exc: Exception) -> NoReturn:
    """Translate typed internal failures to sanitized HTTP status codes."""
    if isinstance(exc, (ResearchInputError, ValueError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, ResearchSourceNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ResearchNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (ResearchConflictError, ResearchTransitionError)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, ResearchIntegrityError):
        raise HTTPException(
            status_code=503, detail="Research registry integrity check failed."
        ) from exc
    if isinstance(exc, ResearchStoreError):
        raise HTTPException(status_code=503, detail="Research registry operation failed.") from exc
    if isinstance(exc, ResearchSourceUnavailableError):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if isinstance(exc, ResearchError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def build_research_router(
    *,
    store_path: Path,
    topics_path: Path,
    read_only: bool,
    gateway: ResearchGateway | None = None,
    decision_secret: str | None = None,
) -> tuple[APIRouter, ResearchStore]:
    """Build the Research Radar API around fixed sources and a local SQLite audit store.

    When ``decision_secret`` is set, the ``/decision`` route verifies the caller's
    ``proof`` against it before recording a ``human`` decision, so a forged or
    proxied request cannot mint a human-approved state change.
    """
    try:
        topics = load_research_topics(topics_path)
    except ResearchError as exc:
        raise ValueError("Research Radar requires a valid local topics configuration.") from exc
    store = ResearchStore(store_path)
    radar = ResearchRadar(topics, gateway or OpenAcademicGateway())
    topics_by_id = {topic.id: topic for topic in topics}
    api = APIRouter(prefix="/v1/research", tags=["research-radar"])

    @api.get("/topics", response_model=tuple[ResearchTopic, ...])
    async def list_research_topics() -> tuple[ResearchTopic, ...]:
        """List the small, allow-listed technical interests guiding paper discovery."""
        return radar.topics

    @api.post("/search", response_model=ResearchSearchResponse)
    async def search_research(payload: ResearchSearchRequest) -> ResearchSearchResponse:
        """Search recent papers for a configured topic; results are not saved automatically."""
        try:
            if payload.topic_id not in topics_by_id:
                raise ResearchInputError("Unknown research topic; choose a configured topic ID.")
            papers = await radar.search(
                payload.topic_id,
                window_days=payload.window_days,
                limit=payload.limit,
            )
        except ResearchError as exc:
            _raise_http(exc)
        return ResearchSearchResponse(
            topic_id=payload.topic_id,
            window_days=payload.window_days,
            returned_count=len(papers),
            papers=papers,
        )

    @api.post("/ingest", response_model=ResearchRecord)
    async def ingest_research_candidate(payload: ResearchIngestRequest) -> ResearchRecord:
        """Fetch official metadata for one selected DOI/arXiv ID and save it as candidate."""
        if read_only:
            raise HTTPException(status_code=403, detail="Research registry is read-only.")
        try:
            if payload.topic_id not in topics_by_id:
                raise ResearchInputError("Unknown research topic; choose a configured topic ID.")
            paper = await radar.resolve(payload.identifier)
            return await asyncio.to_thread(
                store.add_candidate,
                paper,
                topic_id=payload.topic_id,
                actor="agent",
            )
        except ResearchError as exc:
            _raise_http(exc)
        except (ResearchStoreError, ValueError) as exc:
            _raise_http(exc)

    @api.get("/papers", response_model=ResearchPaperListResponse)
    async def list_research_papers(
        status: Annotated[ResearchStatus | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> ResearchPaperListResponse:
        """List locally verified candidates and later triage states."""
        try:
            papers = await asyncio.to_thread(store.list_records, status=status, limit=limit)
        except ResearchStoreError as exc:
            _raise_http(exc)
        return ResearchPaperListResponse(returned_count=len(papers), papers=papers)

    @api.post("/evaluation", response_model=ResearchRecord)
    async def evaluate_research_candidate(payload: ResearchEvaluationRequest) -> ResearchRecord:
        """Persist a source-quoted analysis separately from the agent's engineering inference."""
        if read_only:
            raise HTTPException(status_code=403, detail="Research registry is read-only.")
        try:
            return await asyncio.to_thread(
                store.save_evaluation,
                payload.identifier,
                payload.evaluation,
                actor="agent",
            )
        except ResearchStoreError as exc:
            _raise_http(exc)

    @api.post("/decision", response_model=ResearchRecord)
    async def decide_research_candidate(payload: ResearchDecisionRequest) -> ResearchRecord:
        """Record human rejection or mark a candidate tested after every sandbox gate passes."""
        if read_only:
            raise HTTPException(status_code=403, detail="Research registry is read-only.")
        if decision_secret is not None and payload.proof != decision_secret:
            raise HTTPException(status_code=401, detail="Human decision proof token is not valid.")
        try:
            return await asyncio.to_thread(
                store.decide,
                payload.identifier,
                status=ResearchStatus(payload.status),
                reason=payload.reason,
                evidence=payload.evidence,
                actor="human",
                proof=payload.proof,
            )
        except (ResearchStoreError, ValueError) as exc:
            _raise_http(exc)

    @api.get("/audit", response_model=ResearchAuditResponse)
    async def research_audit(
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> ResearchAuditResponse:
        """Verify and return a bounded audit tail; tampered registries fail closed."""
        try:
            result = await asyncio.to_thread(store.audit_report, limit=limit)
        except ResearchStoreError as exc:
            _raise_http(exc)
        return ResearchAuditResponse(
            integrity_valid=bool(result["integrity_valid"]),
            event_count=int(result["event_count"]),
            head_hash=str(result["head_hash"]),
            events=tuple(result["events"]),
        )

    return api, store
