"""FastAPI entry point. Mutations are queued only; approval is CLI-only."""

from __future__ import annotations

import hmac
from collections.abc import Callable

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query

from .approvals import ActionError, ApprovalStore
from .catalog import get_connector_info
from .config import Settings
from .credentials import CredentialVault, VaultError
from .http_api import ProviderError
from .models import (
    ActionProposalRequest,
    ActionProposalResponse,
    ActionStatusResponse,
    ConnectorInfo,
    ReadRequest,
    ReadResponse,
    ReasoningRequest,
    ReasoningResponse,
    SearchRequest,
    SearchResponse,
)
from .read import read_one
from .reasoning import reason_one
from .search import search_many

HttpAsyncClientFactory = Callable[[], httpx.AsyncClient]


def create_app(
    *,
    settings: Settings | None = None,
    vault: CredentialVault | None = None,
    api_token: str | None = None,
    action_store: ApprovalStore | None = None,
    async_client_factory: HttpAsyncClientFactory | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    vault = vault or CredentialVault()
    actions = action_store or ApprovalStore(settings.database_path, vault)

    app = FastAPI(
        title="Python Unified Connectors Hub",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.vault = vault
    app.state.actions = actions
    app.state.async_client_factory = async_client_factory
    app.state.api_token_override = api_token

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "mode": "local",
            "write_execution": "local_cli_approval_only",
        }

    def require_api_token(authorization: str | None = Header(default=None)) -> None:
        expected = app.state.api_token_override
        if expected is None:
            try:
                expected = vault.get("api_bearer_token")
            except VaultError:
                expected = None
        if not expected:
            raise HTTPException(
                status_code=503, detail="Local API token is not initialized"
            )
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=401, detail="A local bearer token is required"
            )
        supplied = authorization[7:].strip()
        if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
            raise HTTPException(status_code=401, detail="Invalid local bearer token")

    @app.get(
        "/v1/connectors",
        response_model=list[ConnectorInfo],
        dependencies=[Depends(require_api_token)],
    )
    async def connectors() -> list[ConnectorInfo]:
        return get_connector_info(vault, settings)

    @app.post(
        "/v1/search",
        response_model=SearchResponse,
        dependencies=[Depends(require_api_token)],
    )
    async def search(request: SearchRequest) -> SearchResponse:
        factory = app.state.async_client_factory
        if factory is None:
            return await search_many(request, settings=settings, vault=vault)
        client = factory()
        try:
            return await search_many(
                request, settings=settings, vault=vault, client=client
            )
        finally:
            await client.aclose()

    @app.post(
        "/v1/read",
        response_model=ReadResponse,
        dependencies=[Depends(require_api_token)],
    )
    async def read(request: ReadRequest) -> ReadResponse:
        factory = app.state.async_client_factory
        client = (
            factory()
            if factory is not None
            else httpx.AsyncClient(
                timeout=httpx.Timeout(20.0, connect=5.0), follow_redirects=False
            )
        )
        try:
            return await read_one(
                request, client=client, settings=settings, vault=vault
            )
        except ProviderError as exc:
            if exc.status_code in {401, 403, 404}:
                status_code = exc.status_code
            elif exc.code in {
                "not_configured",
                "resource_not_allowlisted",
                "resource_not_configured",
            }:
                status_code = 409
            elif exc.code in {
                "invalid_resource",
                "invalid_resource_id",
                "unsupported_resource_type",
                "unsupported_media_type",
            }:
                status_code = 422
            elif exc.code == "response_too_large":
                status_code = 413
            else:
                status_code = 502
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        finally:
            await client.aclose()

    @app.post(
        "/v1/reasoning",
        response_model=ReasoningResponse,
        dependencies=[Depends(require_api_token)],
    )
    async def reasoning(request: ReasoningRequest) -> ReasoningResponse:
        factory = app.state.async_client_factory
        client = (
            factory()
            if factory is not None
            else httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=5.0),
                follow_redirects=False,
                trust_env=False,
            )
        )
        try:
            return await reason_one(
                request, client=client, settings=settings, vault=vault
            )
        except ProviderError as exc:
            if exc.status_code in {401, 403, 404}:
                status_code = exc.status_code
            elif exc.code in {
                "not_configured",
                "remote_confirmation_required",
            }:
                status_code = 409
            elif exc.code in {
                "invalid_model",
                "model_required",
                "output_limit_exceeded",
                "context_limit_exceeded",
                "invalid_context_config",
                "unsafe_endpoint",
            }:
                status_code = 422
            elif exc.code == "response_too_large":
                status_code = 413
            else:
                status_code = 502
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        finally:
            await client.aclose()

    @app.post(
        "/v1/actions",
        response_model=ActionProposalResponse,
        dependencies=[Depends(require_api_token)],
    )
    async def propose_action(request: ActionProposalRequest) -> ActionProposalResponse:
        try:
            result = actions.propose(request)
        except ActionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except VaultError as exc:
            raise HTTPException(
                status_code=503,
                detail="The secure local keyring is unavailable; no action was queued",
            ) from exc
        return ActionProposalResponse(
            id=result.id,
            connector=result.connector,
            summary=result.summary,
            method=result.method,
            path=result.path,
            status=result.status,
            request_hash=result.request_hash,
            created_at=result.created_at,
            expires_at=result.expires_at,
            approval_instructions=f"Review locally with `connectors-hub approve {result.id}`; HTTP cannot approve or execute actions.",
        )

    @app.get(
        "/v1/actions",
        response_model=list[ActionStatusResponse],
        dependencies=[Depends(require_api_token)],
    )
    async def pending_actions(
        limit: int = Query(default=50, ge=1, le=100),
    ) -> list[ActionStatusResponse]:
        return actions.list_pending(limit)

    @app.get(
        "/v1/actions/{action_id}",
        response_model=ActionStatusResponse,
        dependencies=[Depends(require_api_token)],
    )
    async def action_status(action_id: str) -> ActionStatusResponse:
        try:
            return actions.get_status(action_id)
        except ActionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/v1/audit", dependencies=[Depends(require_api_token)])
    async def audit(limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
        return actions.audit_events(limit)

    @app.get("/", include_in_schema=False, dependencies=[Depends(require_api_token)])
    async def root() -> dict[str, str]:
        return {"service": "Python Unified Connectors Hub", "health": "/health"}

    return app
