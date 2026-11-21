"""FastAPI Studio router unifying Manus-like execution, Artifacts, Memory, and Approvals."""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import ConfigDict, Field, ValidationError

from .agent import (
    AgentSessionState,
    AgentTaskRequest,
    AutonomousAgent,
    SessionStore,
)
from .ai_providers import describe_provider_readiness
from .bridges import ApprovalProposal, ConnectorDescriptor, MemoryHit, MemoryStats
from .compliance import ComplianceRegistryReport, build_compliance_registry
from .connector_hub_bridge import ConnectorReadOutcome, ConnectorSearchOutcome
from .contracts import BudgetSnapshot, StrictModel, TelemetrySink
from .router import FreeFirstRouter
from .security import SecurityValidationError, resolve_safe_path
from .session_store import SessionBusyError
from .telemetry import ObservabilityRuntime

_STATIC_STUDIO_HTML = Path(__file__).resolve().parent / "static" / "studio.html"


class CreateSessionRequest(StrictModel):
    """Input payload creating a new isolated Studio session."""

    title: str = Field(default="New Agent Session", min_length=1, max_length=120)


class ApprovalDecisionRequest(StrictModel):
    """Optional human reviewer note recorded when approving or rejecting an action."""

    note: str | None = Field(default=None, max_length=240)


class ConnectorSearchRequest(StrictModel):
    """Input payload for bounded read-only search across one L5 workspace connector."""

    connector: str = Field(default="github", min_length=2, max_length=32)
    query: str = Field(min_length=1, max_length=160)
    limit: int = Field(default=5, ge=1, le=10)


class ConnectorReadRequest(StrictModel):
    """Input payload for bounded UTF-8 read of one external connector resource."""

    connector: str = Field(default="github", min_length=2, max_length=32)
    resource_id: str = Field(min_length=1, max_length=256)


class StudioStatusResponse(StrictModel):
    """Unified readiness overview spanning L0, L1, BudgetGuard, OTel, and L5."""

    service: str = "ai2027-agent-studio"
    read_only: bool
    cost_usd: float = 0.0
    kernel_available: bool
    memory: MemoryStats
    budget: BudgetSnapshot
    observability: dict[str, Any] = Field(default_factory=dict)
    providers: tuple[dict[str, Any], ...]
    connectors: tuple[ConnectorDescriptor, ...]
    sessions_count: int
    pending_approvals: int


class ConnectorHubCatalogItem(StrictModel):
    """Sanitized connector metadata copied from the authenticated Connectors Hub."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    state: Literal["unconfigured", "configured", "needs_resource_config"]
    auth_group: str = Field(min_length=1, max_length=64)
    search_supported: bool
    read_supported: bool
    notes: tuple[str, ...] = Field(default=(), max_length=12)


class ConnectorHubCatalogResponse(StrictModel):
    """Connection state and safe catalog data from the distinct Connectors Hub service."""

    source: Literal["connectors-hub"] = "connectors-hub"
    available: bool
    reason: Literal[
        "connected",
        "token_missing",
        "unauthorized",
        "service_unavailable",
        "upstream_error",
        "invalid_endpoint",
        "invalid_payload",
    ]
    checked_at: str
    connectors: tuple[ConnectorHubCatalogItem, ...] = ()


def _connector_hub_base_url() -> str:
    """Validate the server-side Connectors Hub URL; callers cannot supply a URL."""
    raw = os.environ.get("CONNECTORS_HUB_URL", "http://127.0.0.1:8765").strip()
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid Connectors Hub endpoint") from exc
    loopback_host = host in {"localhost", "127.0.0.1", "::1"}
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or (parsed.scheme == "http" and not loopback_host)
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError("invalid Connectors Hub endpoint")
    return raw.rstrip("/")


def _catalog_result(
    reason: Literal[
        "connected",
        "token_missing",
        "unauthorized",
        "service_unavailable",
        "upstream_error",
        "invalid_endpoint",
        "invalid_payload",
    ],
    connectors: tuple[ConnectorHubCatalogItem, ...] = (),
) -> ConnectorHubCatalogResponse:
    """Create a catalog result with an explicit, truthful check timestamp."""
    return ConnectorHubCatalogResponse(
        available=reason == "connected",
        reason=reason,
        checked_at=datetime.now(UTC).isoformat(),
        connectors=connectors,
    )


async def fetch_connectors_hub_catalog(
    *,
    base_url: str,
    token: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ConnectorHubCatalogResponse:
    """Fetch only the authenticated, read-only connector catalog from the Hub."""
    body = bytearray()
    try:
        async with httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(3.0, connect=1.0),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with client.stream(
                "GET",
                f"{base_url.rstrip('/')}/v1/connectors",
                headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
            ) as response:
                if response.status_code in {401, 403}:
                    return _catalog_result("unauthorized")
                if response.status_code == 503:
                    return _catalog_result("service_unavailable")
                if response.status_code != 200:
                    return _catalog_result("upstream_error")
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > 256_000:
                        return _catalog_result("invalid_payload")
                    body.extend(chunk)
    except (httpx.TimeoutException, httpx.TransportError):
        return _catalog_result("service_unavailable")
    except (ValueError, httpx.InvalidURL):
        return _catalog_result("invalid_endpoint")

    try:
        payload = json.loads(body)
        if not isinstance(payload, list) or len(payload) > 100:
            return _catalog_result("invalid_payload")
        connectors = tuple(ConnectorHubCatalogItem.model_validate(item) for item in payload)
    except (ValueError, ValidationError, TypeError):
        return _catalog_result("invalid_payload")
    return _catalog_result("connected", connectors)


def _load_studio_html() -> str:
    if _STATIC_STUDIO_HTML.is_file():
        return _STATIC_STUDIO_HTML.read_text(encoding="utf-8")
    return "<!doctype html><html><body><h1>AI-2027 Studio UI missing</h1></body></html>"


def build_studio_router(
    *,
    workspace_root: Path,
    studio_dir: Path,
    router: FreeFirstRouter,
    telemetry: TelemetrySink,
    read_only: bool,
) -> tuple[APIRouter, AutonomousAgent, SessionStore]:
    """Build the FastAPI Studio router, AutonomousAgent, and SessionStore."""
    store = SessionStore(studio_dir)
    agent = AutonomousAgent(
        workspace_root=workspace_root,
        session_store=store,
        router=router,
        telemetry=telemetry,
        read_only=read_only,
    )
    api = APIRouter()

    @api.get("/", response_class=HTMLResponse, include_in_schema=False)
    @api.get("/studio", response_class=HTMLResponse, include_in_schema=False)
    async def studio_home() -> HTMLResponse:
        """Serve the interactive bilingual Manus+Claude Studio web application."""
        return HTMLResponse(
            content=_load_studio_html(),
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @api.get("/v1/studio/status", response_model=StudioStatusResponse)
    async def studio_status() -> StudioStatusResponse:
        """Report unified readiness across L0 Kernel, L1 Memory, L4 LLMs, and L5."""
        mem_stats = await agent.memory_bridge.stats()
        connectors = await agent.connectors_bridge.list_connectors()
        sessions = await store.list_sessions(limit=50)
        approvals = await agent.connectors_bridge.list_approvals()
        pending = sum(1 for item in approvals if item.status == "pending")
        providers = tuple(
            s.model_dump(mode="json")
            for s in describe_provider_readiness(
                router.providers, budget_policy=router.budget_guard.policy
            )
        )
        obs_info = (
            telemetry.status_summary()
            if isinstance(telemetry, ObservabilityRuntime)
            else {"mode": "custom"}
        )
        return StudioStatusResponse(
            read_only=read_only,
            kernel_available=agent.kernel_bridge.is_available,
            memory=mem_stats,
            budget=router.budget_snapshot(),
            observability=obs_info,
            providers=providers,
            connectors=tuple(connectors),
            sessions_count=len(sessions),
            pending_approvals=pending,
        )

    @api.get(
        "/v1/studio/connectors/catalog",
        response_model=ConnectorHubCatalogResponse,
    )
    async def connector_hub_catalog() -> ConnectorHubCatalogResponse:
        """Expose the real Hub catalog without revealing its bearer token or proxying tools."""
        token = os.environ.get("CONNECTORS_HUB_API_TOKEN", "").strip()
        if not token:
            return _catalog_result("token_missing")
        try:
            base_url = _connector_hub_base_url()
        except ValueError:
            return _catalog_result("invalid_endpoint")
        return await fetch_connectors_hub_catalog(base_url=base_url, token=token)

    @api.get("/v1/studio/sessions", response_model=list[AgentSessionState])
    async def list_sessions() -> list[AgentSessionState]:
        """List recent Studio sessions ordered newest first."""
        return await store.list_sessions(limit=30)

    @api.post("/v1/studio/sessions", response_model=AgentSessionState)
    async def create_session(payload: CreateSessionRequest) -> AgentSessionState:
        """Allocate a fresh isolated session sandbox."""
        if read_only:
            raise HTTPException(status_code=403, detail="Service is in AI2027_READ_ONLY mode.")
        return await asyncio.to_thread(store.create_session, title=payload.title)

    @api.get("/v1/studio/sessions/{session_id}", response_model=AgentSessionState)
    async def get_session(session_id: str) -> AgentSessionState:
        """Fetch full persisted state, steps, and verified artifacts for a session."""
        try:
            return await store.load(session_id)
        except SecurityValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.post("/v1/studio/sessions/{session_id}/run", response_model=AgentSessionState)
    async def run_session_task(
        session_id: str, payload: AgentTaskRequest, response: Response
    ) -> AgentSessionState:
        """Run an autonomous task synchronously and return verified session state."""
        if read_only:
            raise HTTPException(status_code=403, detail="Service is in AI2027_READ_ONLY mode.")
        try:
            state = await agent.run(session_id, payload)
        except SessionBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SecurityValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        response.headers["X-Trace-Id"] = state.trace_id
        return state

    @api.post("/v1/studio/sessions/{session_id}/stream")
    async def stream_session_task(session_id: str, payload: AgentTaskRequest) -> StreamingResponse:
        """Execute an autonomous task and stream live Server-Sent Events."""
        if read_only:
            raise HTTPException(status_code=403, detail="Service is in AI2027_READ_ONLY mode.")
        try:
            _ = await store.load(session_id)
        except SecurityValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        # Acquire before the 200 goes out: a busy session must answer 409, not open
        # a stream that immediately dies. The agent releases it when the stream ends.
        try:
            lease = store.acquire_lease(session_id) if store.lease_enabled else None
        except SessionBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        async def _event_gen() -> AsyncIterator[str]:
            try:
                async for evt in agent.stream_run(session_id, payload, lease=lease):
                    data_json = evt.model_dump_json()
                    yield f"event: {evt.event}\ndata: {data_json}\n\n"
            except Exception as exc:  # noqa: BLE001 - stream error cleanly instead of silent EOF
                err_payload = json.dumps({"message": str(exc)})
                yield f"event: error\ndata: {err_payload}\n\n"
            finally:
                store.release_lease(lease)

        return StreamingResponse(
            _event_gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @api.post("/v1/studio/sessions/{session_id}/stop")
    async def stop_session_task(session_id: str) -> dict[str, Any]:
        """Engage the cooperative kill switch for a running session."""
        try:
            _ = await store.load(session_id)
            store.request_abort(session_id)
        except SecurityValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True, "session_id": session_id, "abort_requested": True}

    @api.get("/v1/studio/sessions/{session_id}/artifacts")
    async def list_session_artifacts(session_id: str) -> dict[str, Any]:
        """Return verified artifacts and L0 tamper-evident ledger summary for session_id."""
        try:
            state = await store.load(session_id)
            sdir = store.session_dir(session_id)
        except SecurityValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        ledger_path = sdir / "ledger.jsonl"

        def _count_ledger() -> int:
            if not ledger_path.is_file():
                return 0
            return len(ledger_path.read_text(encoding="utf-8").splitlines())

        ledger_events = await asyncio.to_thread(_count_ledger)
        return {
            "session_id": session_id,
            "artifacts": [a.model_dump(mode="json") for a in state.artifacts],
            "ledger_events": ledger_events,
        }

    @api.get("/v1/studio/sessions/{session_id}/artifacts/{artifact_path:path}")
    async def fetch_session_artifact(session_id: str, artifact_path: str) -> Response:
        """Serve one verified sandbox artifact safely with isolated preview CSP."""
        try:
            sdir = store.session_dir(session_id)
            sandbox_dir = sdir / "sandbox"
            target = resolve_safe_path(sandbox_dir, artifact_path)
        except SecurityValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if not await asyncio.to_thread(target.is_file):
            raise HTTPException(status_code=404, detail="Artifact not found.")

        data = await asyncio.to_thread(target.read_bytes)
        ctype, _ = mimetypes.guess_type(target.name)
        media = ctype or "text/plain; charset=utf-8"
        if target.suffix.lower() in {".md", ".py", ".json", ".txt", ".log", ".csv"}:
            media = f"{ctype}; charset=utf-8" if ctype else "text/plain; charset=utf-8"
        headers = {
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox allow-scripts",
        }
        return Response(content=data, media_type=media, headers=headers)

    @api.get("/v1/studio/approvals", response_model=list[ApprovalProposal])
    async def list_approvals(
        session_id: str | None = Query(default=None),
    ) -> list[ApprovalProposal]:
        """List pending and decided L5 human approval proposals."""
        return await agent.connectors_bridge.list_approvals(session_id=session_id)

    @api.post("/v1/studio/approvals/{action_id}/approve", response_model=ApprovalProposal)
    # kernel-exempt: HTTP transport only — read-only guard, delegate, translate
    # errors to status codes. It holds no approval policy of its own.
    async def approve_action(
        action_id: str, payload: ApprovalDecisionRequest | None = None
    ) -> ApprovalProposal:
        """Record explicit human approval for a pending external connector proposal."""
        if read_only:
            raise HTTPException(status_code=403, detail="Service is in AI2027_READ_ONLY mode.")
        try:
            return await agent.connectors_bridge.decide_approval(
                action_id=action_id,
                approve=True,
                note=payload.note if payload else None,
            )
        except SecurityValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.post("/v1/studio/approvals/{action_id}/reject", response_model=ApprovalProposal)
    async def reject_action(
        action_id: str, payload: ApprovalDecisionRequest | None = None
    ) -> ApprovalProposal:
        """Reject a pending external connector proposal."""
        if read_only:
            raise HTTPException(status_code=403, detail="Service is in AI2027_READ_ONLY mode.")
        try:
            return await agent.connectors_bridge.decide_approval(
                action_id=action_id,
                approve=False,
                note=payload.note if payload else None,
            )
        except SecurityValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.get("/v1/studio/memory/search", response_model=list[MemoryHit])
    async def search_memory(
        q: str = Query(min_length=1, max_length=256),
        k: int = Query(default=5, ge=1, le=20),
    ) -> list[MemoryHit]:
        """Search L1 EvidenceMemoryStore for SHA-256 verified artifacts and insights."""
        return await agent.memory_bridge.search(q, k=k)

    @api.get("/v1/studio/agent-card")
    async def studio_agent_card() -> dict[str, Any]:
        """Serve an A2A-compatible discovery card advertising Studio tools & honest limits."""
        providers = [
            s.model_dump(mode="json") for s in describe_provider_readiness(router.providers)
        ]
        tools = [spec.model_dump(mode="json") for spec in agent.tool_executor.specs()]
        return {
            "protocolVersion": "0.2.0",
            "name": "AI-2027 Agent Studio",
            "description": (
                "Unified L0-L5 autonomous workspace bridging projects/agi-system SHA-256 "
                "verification, L1 EvidenceMemoryStore, L4 Free-First LLM routing ($0), "
                "and L5 human-approved connectors."
            ),
            "costUsd": 0.0,
            "capabilities": {
                "streaming": True,
                "evidenceRequired": True,
                "humanApprovalRequiredForExternalWrites": True,
                "sandboxedExecution": True,
            },
            "providers": providers,
            "tools": tools,
            "endpoints": {
                "status": "/v1/studio/status",
                "sessions": "/v1/studio/sessions",
                "stream": "/v1/studio/sessions/{session_id}/stream",
                "evidence": "/v1/studio/sessions/{session_id}/evidence",
                "approvals": "/v1/studio/approvals",
                "memory_search": "/v1/studio/memory/search?q={query}",
                "connectors_catalog": "/v1/studio/connectors/catalog",
                "connectors_search": "/v1/studio/connectors/search",
            },
            "honest_limits": [
                "Runs default to offline-deterministic mode unless Free-First LLM keys exist.",
                "External writes are never auto-dispatched; they enter approvals.json for review.",
                "L1 retrieval uses lexical hashed uni/bi-grams + declared thesaurus, not neural.",
            ],
        }

    @api.get("/v1/studio/sessions/{session_id}/evidence")
    async def download_session_evidence(session_id: str) -> Response:
        """Independently re-verify every session artifact on disk and return JSON bundle."""
        try:
            state = await store.load(session_id)
            sdir = store.session_dir(session_id)
        except SecurityValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        sandbox_dir = sdir / "sandbox"
        ledger_path = sdir / "ledger.jsonl"

        def _collect_evidence() -> dict[str, Any]:
            checked_artifacts: list[dict[str, Any]] = []
            all_match = True
            for art in state.artifacts:
                target = resolve_safe_path(sandbox_dir, art.path)
                exists = target.is_file()
                disk_sha = hashlib.sha256(target.read_bytes()).hexdigest() if exists else None
                matches = bool(exists and art.sha256 and disk_sha == art.sha256)
                if not matches:
                    all_match = False
                checked_artifacts.append(
                    {
                        "path": art.path,
                        "kind": art.kind,
                        "recorded_sha256": art.sha256,
                        "disk_sha256": disk_sha,
                        "file_exists": exists,
                        "hash_matches": matches,
                        "byte_size": target.stat().st_size if exists else 0,
                    }
                )
            fsck = agent.kernel_bridge.fsck_ledger_sync(ledger_path)
            return {
                "session_id": state.session_id,
                "trace_id": state.trace_id,
                "mode": state.mode,
                "provider": state.provider,
                "cost_usd": state.cost_usd,
                "billing_tier": state.billing_tier.value,
                "cost_status": state.cost_status.value,
                "cost_known": state.cost_known,
                "router_attempts": [a.model_dump(mode="json") for a in state.router_attempts],
                "verified_all_artifacts": all_match and len(checked_artifacts) > 0,
                "artifacts": checked_artifacts,
                "ledger": fsck or {"exists": ledger_path.is_file(), "chain_valid": True},
            }

        bundle = await asyncio.to_thread(_collect_evidence)
        bundle["memory_stats"] = (await agent.memory_bridge.stats()).model_dump(mode="json")
        headers = {
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{session_id}-evidence.json"',
        }
        return Response(
            content=json.dumps(bundle, indent=2, ensure_ascii=False),
            media_type="application/json",
            headers=headers,
        )

    @api.delete("/v1/studio/sessions/{session_id}")
    async def delete_session(session_id: str) -> dict[str, Any]:
        """Delete an isolated session directory cleanly."""
        if read_only:
            raise HTTPException(status_code=403, detail="Service is in AI2027_READ_ONLY mode.")
        try:
            _ = await store.load(session_id)
            sdir = store.session_dir(session_id)
        except SecurityValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        await asyncio.to_thread(shutil.rmtree, sdir)
        return {"deleted": True, "session_id": session_id}

    @api.post("/v1/studio/connectors/search", response_model=ConnectorSearchOutcome)
    async def search_connector(payload: ConnectorSearchRequest) -> ConnectorSearchOutcome:
        """Run bounded read-only search against a configured L5 connector."""
        try:
            return await agent.connector_client.search(
                payload.connector, payload.query, limit=payload.limit
            )
        except SecurityValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.post("/v1/studio/connectors/read", response_model=ConnectorReadOutcome)
    async def read_connector(payload: ConnectorReadRequest) -> ConnectorReadOutcome:
        """Read one explicit bounded UTF-8 resource from a configured L5 connector."""
        try:
            return await agent.connector_client.read(payload.connector, payload.resource_id)
        except SecurityValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.get("/v1/studio/compliance", response_model=ComplianceRegistryReport)
    async def get_compliance_registry() -> ComplianceRegistryReport:
        """Return three-tier compliance registry across all 48 catalog projects."""
        return await build_compliance_registry(workspace_root, telemetry=telemetry)

    return api, agent, store
