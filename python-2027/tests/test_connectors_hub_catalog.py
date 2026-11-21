from __future__ import annotations

import httpx
import pytest

from ai2027_protocol import studio as studio_module
from ai2027_protocol.server import create_app
from ai2027_protocol.studio import fetch_connectors_hub_catalog


def _test_token() -> str:
    return "test-token"


@pytest.mark.asyncio
async def test_fetch_connectors_hub_catalog_returns_only_safe_real_catalog_fields() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/connectors"
        assert request.headers["authorization"] == "Bearer test-token"
        return httpx.Response(
            200,
            request=request,
            json=[
                {
                    "id": "github",
                    "name": "GitHub",
                    "state": "configured",
                    "auth_group": "github",
                    "search_supported": True,
                    "read_supported": True,
                    "notes": ["Read-only discovery"],
                    "access_token": "must-not-leave-upstream",
                }
            ],
        )

    catalog = await fetch_connectors_hub_catalog(
        base_url="http://127.0.0.1:8765",
        token=_test_token(),
        transport=httpx.MockTransport(handler),
    )

    assert catalog.available is True
    assert catalog.reason == "connected"
    assert catalog.source == "connectors-hub"
    assert len(catalog.connectors) == 1
    assert catalog.connectors[0].id == "github"
    assert catalog.connectors[0].state == "configured"
    assert "access_token" not in catalog.connectors[0].model_dump()


@pytest.mark.asyncio
async def test_fetch_connectors_hub_catalog_reports_auth_and_payload_failures() -> None:
    async def unauthorized(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "invalid token"})

    async def malformed(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"connectors": []})

    unauthorized_result = await fetch_connectors_hub_catalog(
        base_url="http://127.0.0.1:8765",
        token=_test_token(),
        transport=httpx.MockTransport(unauthorized),
    )
    malformed_result = await fetch_connectors_hub_catalog(
        base_url="http://127.0.0.1:8765",
        token=_test_token(),
        transport=httpx.MockTransport(malformed),
    )

    assert unauthorized_result.available is False
    assert unauthorized_result.reason == "unauthorized"
    assert unauthorized_result.connectors == ()
    assert malformed_result.available is False
    assert malformed_result.reason == "invalid_payload"


@pytest.mark.asyncio
async def test_fetch_connectors_hub_catalog_does_not_follow_redirects() -> None:
    async def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"Location": "https://example.invalid/"},
            request=request,
        )

    result = await fetch_connectors_hub_catalog(
        base_url="http://127.0.0.1:8765",
        token=_test_token(),
        transport=httpx.MockTransport(redirect),
    )

    assert result.available is False
    assert result.reason == "upstream_error"
    assert result.connectors == ()


@pytest.mark.asyncio
async def test_studio_catalog_route_reports_missing_server_token_without_leaking_it(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("CONNECTORS_HUB_API_TOKEN", raising=False)
    app = create_app(workspace_root=tmp_path, studio_dir=tmp_path / "studio")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://studio.test"
    ) as client:
        response = await client.get("/v1/studio/connectors/catalog")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "connectors-hub"
    assert payload["available"] is False
    assert payload["reason"] == "token_missing"
    assert payload["connectors"] == []


@pytest.mark.asyncio
async def test_studio_catalog_route_refuses_plain_http_to_remote_hub(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CONNECTORS_HUB_API_TOKEN", "test-token")
    monkeypatch.setenv("CONNECTORS_HUB_URL", "http://hub.example:8765")
    app = create_app(workspace_root=tmp_path, studio_dir=tmp_path / "studio")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://studio.test"
    ) as client:
        response = await client.get("/v1/studio/connectors/catalog")

    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.json()["reason"] == "invalid_endpoint"


@pytest.mark.asyncio
async def test_studio_catalog_route_uses_server_config_and_returns_hub_catalog(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CONNECTORS_HUB_API_TOKEN", _test_token())
    monkeypatch.setenv("CONNECTORS_HUB_URL", "https://hub.example")

    async def fake_fetch(*, base_url: str, token: str, transport=None):
        assert base_url == "https://hub.example"
        assert token == _test_token()
        return studio_module.ConnectorHubCatalogResponse(
            available=True,
            reason="connected",
            checked_at="2026-11-20T00:00:00+00:00",
            connectors=(
                studio_module.ConnectorHubCatalogItem(
                    id="github",
                    name="GitHub",
                    state="configured",
                    auth_group="github",
                    search_supported=True,
                    read_supported=True,
                    notes=("Catalog metadata only",),
                ),
            ),
        )

    monkeypatch.setattr(studio_module, "fetch_connectors_hub_catalog", fake_fetch)
    app = create_app(workspace_root=tmp_path, studio_dir=tmp_path / "studio")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://studio.test"
    ) as client:
        response = await client.get("/v1/studio/connectors/catalog")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["connectors"][0]["id"] == "github"
    assert "token" not in response.text.lower()
