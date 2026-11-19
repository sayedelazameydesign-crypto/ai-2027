"""FastAPI Studio router unifying Manus-like execution, Artifacts, Memory, and Approvals."""

from __future__ import annotations

import asyncio
import json
import mimetypes
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import Field

from .agent import AgentSessionState, AgentTaskRequest, AutonomousAgent, SessionStore
from .ai_providers import describe_provider_readiness
from .bridges import ApprovalProposal, ConnectorDescriptor, MemoryHit, MemoryStats
from .contracts import StrictModel, TelemetrySink
from .router import FreeFirstRouter
from .security import SecurityValidationError, resolve_safe_path

_STATIC_STUDIO_HTML = Path(__file__).resolve().parent / "static" / "studio.html"


class CreateSessionRequest(StrictModel):
    """Input payload creating a new isolated Studio session."""

    title: str = Field(default="New Agent Session", min_length=1, max_length=120)


class ApprovalDecisionRequest(StrictModel):
    """Optional human reviewer note recorded when approving or rejecting an action."""

    note: str | None = Field(default=None, max_length=240)


class StudioStatusResponse(StrictModel):
    """Unified readiness overview spanning L0, L1, L4 Free-First LLMs, and L5."""

    service: str = "ai2027-agent-studio"
    read_only: bool
    cost_usd: float = 0.0
    kernel_available: bool
    memory: MemoryStats
    providers: tuple[dict[str, Any], ...]
    connectors: tuple[ConnectorDescriptor, ...]
    sessions_count: int
    pending_approvals: int


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
            s.model_dump(mode="json") for s in describe_provider_readiness(router.providers)
        )
        return StudioStatusResponse(
            read_only=read_only,
            kernel_available=agent.kernel_bridge.is_available,
            memory=mem_stats,
            providers=providers,
            connectors=tuple(connectors),
            sessions_count=len(sessions),
            pending_approvals=pending,
        )

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

        async def _event_gen() -> AsyncIterator[str]:
            try:
                async for evt in agent.stream_run(session_id, payload):
                    data_json = evt.model_dump_json()
                    yield f"event: {evt.event}\ndata: {data_json}\n\n"
            except Exception as exc:  # noqa: BLE001 - stream error cleanly instead of silent EOF
                err_payload = json.dumps({"message": str(exc)})
                yield f"event: error\ndata: {err_payload}\n\n"

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

    return api, agent, store
