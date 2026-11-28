"""Async FastAPI service exposing Free-First AI routing, auditing, and traces."""

from __future__ import annotations

import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from pydantic import Field

from .ai_providers import describe_provider_readiness
from .auditor import ProtocolAuditor
from .benchmark import run_benchmark_suite
from .celia_memory import (
    CeliaIndexSummary,
    CeliaMemoryHit,
    CeliaMemoryStats,
    CeliaMemoryStore,
    OllamaEmbeddingBackend,
)
from .contracts import (
    AuditRequest,
    BatchPromptRequest,
    BenchmarkReport,
    BudgetSnapshot,
    CompletionResponse,
    PromptRequest,
    ProtocolAuditReport,
    ProviderStatus,
    SpanRecord,
    StrictModel,
    TelemetrySink,
)
from .ollama_layer import OllamaRuntimeClient
from .ollama_readiness import OllamaVerificationReport, verify_ollama_capabilities
from .research_api import build_research_router
from .research_contracts import ResearchGateway
from .router import AllProvidersFailedError, FreeFirstRouter
from .security import SecurityValidationError
from .studio import build_studio_router
from .studio_auth import StudioAuthConfig, install_studio_auth
from .telemetry import (
    ObservabilityConfig,
    ObservabilityRuntime,
    extract_trace_context,
    inject_trace_headers,
)

_DEFAULT_ROOT = Path(__file__).resolve().parents[3]

PROTOCOL_RULES: tuple[dict[str, str | int], ...] = (
    {"id": 1, "title": "Foundation", "spec": "Python 3.13+ target, uv, ruff, strict Pydantic V2"},
    {"id": 2, "title": "Architecture", "spec": "Small units, Protocol contracts, swappable parts"},
    {"id": 3, "title": "Concurrency", "spec": "asyncio + TaskGroup, non-blocking event loop"},
    {"id": 4, "title": "Performance", "spec": "Measure with perf_counter_ns before optimizing"},
    {"id": 5, "title": "Testing", "spec": "pytest + hypothesis, >90% branch coverage gate"},
    {"id": 6, "title": "Security", "spec": "Zero hardcoded secrets, scrub logs, strict inputs"},
    {"id": 7, "title": "Observability", "spec": "structlog + OpenTelemetry (Local/Persist/Dist)"},
    {"id": 8, "title": "AI Resilience", "spec": "Free-First routing, MAX_SPEND=0 guard, retry"},
    {"id": 9, "title": "Delivery", "spec": "Automated CI + $0 Workers & Google Colab targets"},
    {"id": 10, "title": "Excellence", "spec": "First-run ready, 1-command demo, 1-line docstrings"},
)


class RagIndexRequest(StrictModel):
    """Validated request indexing workspace docs into persistent Celia Memory RAG."""

    paths: tuple[str, ...] = Field(default=("docs", "README.md"), min_length=1, max_length=16)
    max_files: int = Field(default=40, ge=1, le=200)


class RagSearchRequest(StrictModel):
    """Validated semantic/lexical RAG query over persistent Celia Memory."""

    query: str = Field(min_length=1, max_length=512)
    k: int = Field(default=5, ge=1, le=20)


def create_app(
    *,
    workspace_root: Path | None = None,
    studio_dir: Path | None = None,
    router: FreeFirstRouter | None = None,
    telemetry: TelemetrySink | None = None,
    read_only: bool | None = None,
    research_gateway: ResearchGateway | None = None,
) -> FastAPI:
    """Build a configured FastAPI application with replaceable router and telemetry."""
    root = (workspace_root or _DEFAULT_ROOT).resolve()
    resolved_studio_dir = (studio_dir or (root / ".ai2027_studio")).resolve()
    runtime: TelemetrySink = telemetry or ObservabilityRuntime(
        json_logs=True,
        config=ObservabilityConfig.from_env(default_dir=resolved_studio_dir / "telemetry"),
    )
    ai_router = router or FreeFirstRouter(telemetry=runtime)
    auditor = ProtocolAuditor(workspace_root=root, telemetry=runtime)
    is_read_only = (
        os.environ.get("AI2027_READ_ONLY", "0") == "1" if read_only is None else read_only
    )

    app = FastAPI(
        title="AI-2027 Agent Studio & Protocol API",
        version="0.2.0",
        description="Unified Manus+Claude Autonomous Agent Studio, Free-First AI, L0/L1/L5.",
    )

    @app.middleware("http")
    async def distributed_trace_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Propagate W3C traceparent / X-Trace-Id across service boundaries."""
        parent_ctx = extract_trace_context(request.headers)
        if isinstance(runtime, ObservabilityRuntime):
            with runtime.start_span(
                "http.request",
                {"http.method": request.method, "http.path": request.url.path},
                parent_context=parent_ctx,
            ):
                response = await call_next(request)
                for hk, hv in inject_trace_headers().items():
                    response.headers.setdefault(hk, hv)
                return response
        response = await call_next(request)
        response.headers.setdefault("x-trace-id", runtime.current_trace_id())
        return response

    auth_config = StudioAuthConfig.from_env()
    if auth_config is not None:
        install_studio_auth(app, auth_config)

    studio_router, _agent, _store = build_studio_router(
        workspace_root=root,
        studio_dir=resolved_studio_dir,
        router=ai_router,
        telemetry=runtime,
        read_only=is_read_only,
    )
    app.include_router(studio_router)

    topics_path = root / "research" / "topics.toml"
    if not topics_path.is_file():
        topics_path = Path(__file__).resolve().parents[3] / "research" / "topics.toml"
    research_router, research_store = build_research_router(
        store_path=resolved_studio_dir / "research.sqlite3",
        topics_path=topics_path,
        read_only=is_read_only,
        gateway=research_gateway,
    )
    app.include_router(research_router)
    app.state.research_store = research_store

    @app.get("/health")
    async def health() -> dict[str, Any]:
        """Return service health, Python runtime, and MAX_SPEND budget guard state."""
        snap = ai_router.budget_snapshot()
        return {
            "ok": True,
            "service": "ai2027-python-2027",
            "python": sys.version.split()[0],
            "read_only": is_read_only,
            "max_spend_usd": snap.max_spend_usd,
            "spent_usd": snap.spent_usd,
            "cost_usd": snap.spent_usd,
        }

    @app.get("/v1/protocol")
    async def protocol_info() -> dict[str, Any]:
        """Describe the 10 Python 2027 protocol rules and $0 deployment targets."""
        return {
            "protocol": "Python 2027 Software Excellence Protocol",
            "rules": list(PROTOCOL_RULES),
            "platforms": [
                "local-uv",
                "cloudflare-python-worker",
                "cloudflare-edge-gateway",
                "google-colab",
            ],
            "providers": [p.kind.value for p in ai_router.providers],
        }

    @app.get("/v1/providers", response_model=list[ProviderStatus])
    async def list_providers() -> list[ProviderStatus]:
        """Report configured provider readiness and billing-tier admission state."""
        return list(
            describe_provider_readiness(
                ai_router.providers, budget_policy=ai_router.budget_guard.policy
            )
        )

    @app.get("/v1/budget", response_model=BudgetSnapshot)
    async def get_budget_snapshot() -> BudgetSnapshot:
        """Return live MAX_SPEND=0 BudgetGuard ledger counts and spend totals."""
        return ai_router.budget_snapshot()

    @app.post("/v1/route", response_model=CompletionResponse)
    async def route_prompt(request: PromptRequest, response: Response) -> CompletionResponse:
        """Route one validated prompt across the BudgetGuard Free-First chain."""
        if is_read_only:
            raise HTTPException(
                status_code=403, detail="Service is running in AI2027_READ_ONLY mode."
            )
        try:
            completion = await ai_router.route(request)
        except AllProvidersFailedError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        response.headers["X-Trace-Id"] = completion.trace_id
        return completion

    @app.post("/v1/route/batch", response_model=list[CompletionResponse])
    async def route_batch(request: BatchPromptRequest) -> list[CompletionResponse]:
        """Execute a bounded batch of prompts concurrently with asyncio.TaskGroup."""
        if is_read_only:
            raise HTTPException(
                status_code=403, detail="Service is running in AI2027_READ_ONLY mode."
            )
        batch_error: str | None = None
        try:
            return await ai_router.route_batch(
                request.requests, max_concurrency=request.max_concurrency
            )
        except* AllProvidersFailedError as exc_group:
            batch_error = f"Batch failed ({len(exc_group.exceptions)} error(s))."
        raise HTTPException(status_code=503, detail=batch_error)

    @app.post("/v1/audit", response_model=ProtocolAuditReport)
    async def audit_project(request: AuditRequest, response: Response) -> ProtocolAuditReport:
        """Audit a validated relative repository path against the 10 protocol rules."""
        try:
            report = await auditor.audit_projects([request.target_path])
        except SecurityValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        response.headers["X-Trace-Id"] = report.trace_id
        return report

    @app.get("/v1/traces", response_model=list[SpanRecord])
    async def list_traces(persisted: bool = Query(default=False)) -> list[SpanRecord]:
        """Return recent in-memory or disk-persisted OpenTelemetry spans."""
        if persisted and isinstance(runtime, ObservabilityRuntime):
            return runtime.persisted_spans(limit=100)
        return runtime.exported_spans()[-50:]

    @app.get("/v1/traces/status")
    async def traces_status() -> dict[str, Any]:
        """Report local/persistent/distributed/export observability configuration."""
        if isinstance(runtime, ObservabilityRuntime):
            return runtime.status_summary()
        return {"mode": "custom", "in_memory_spans_count": len(runtime.exported_spans())}

    @app.post("/v1/traces/export")
    async def export_traces() -> dict[str, Any]:
        """Flush persisted spans to any configured external OTLP/extra exporters."""
        if is_read_only:
            raise HTTPException(
                status_code=403, detail="Service is running in AI2027_READ_ONLY mode."
            )
        if isinstance(runtime, ObservabilityRuntime):
            return {"exported": runtime.export_now(), "status": runtime.status_summary()}
        return {"exported": {}}

    @app.get("/v1/bench", response_model=BenchmarkReport)
    async def run_bench(response: Response) -> BenchmarkReport:
        """Execute a lightweight live benchmark suite and return measured timings."""
        report = await run_benchmark_suite(iterations=8, telemetry=runtime)
        response.headers["X-Trace-Id"] = report.trace_id
        return report

    def _build_celia_store() -> CeliaMemoryStore:
        client = OllamaRuntimeClient()
        backend = OllamaEmbeddingBackend(client) if client.is_configured() else None
        db_file = (resolved_studio_dir / "celia_memory.sqlite3").resolve()
        return CeliaMemoryStore(db_file, backend=backend, allow_lexical_fallback=True)

    @app.get("/v1/ollama/status", response_model=OllamaVerificationReport)
    async def ollama_status(live: bool = Query(default=False)) -> OllamaVerificationReport:
        """Report Phase-0 host hardware readiness and optional live Ollama verification."""
        return await verify_ollama_capabilities(workspace_root=root, run_live_inference=live)

    @app.get("/v1/memory/rag/stats", response_model=CeliaMemoryStats)
    async def celia_rag_stats() -> CeliaMemoryStats:
        """Return persistent Celia Memory SQLite chunk and backend metrics."""
        return await _build_celia_store().stats()

    @app.post("/v1/memory/rag/index", response_model=CeliaIndexSummary)
    async def celia_rag_index(request: RagIndexRequest) -> CeliaIndexSummary:
        """Index validated workspace documentation paths into Celia Memory SQLite."""
        if is_read_only:
            raise HTTPException(
                status_code=403, detail="Service is running in AI2027_READ_ONLY mode."
            )
        try:
            return await _build_celia_store().index_paths(
                root, request.paths, max_files=request.max_files
            )
        except SecurityValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/memory/rag/search", response_model=list[CeliaMemoryHit])
    async def celia_rag_search(request: RagSearchRequest) -> list[CeliaMemoryHit]:
        """Search persistent Celia Memory SQLite RAG without hitting unverified claims."""
        try:
            return await _build_celia_store().search(request.query, k=request.k)
        except SecurityValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app
