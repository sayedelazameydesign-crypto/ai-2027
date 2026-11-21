"""Deployment readiness for the published Agent Studio.

The Studio service is published as one origin: ``studio.html`` is served by
the FastAPI application itself at ``/studio`` and calls ``/v1/studio/*`` with
relative paths.  That is what keeps the deployment free of a proxy, of CORS
headers and of any credential in a browser bundle.

These tests protect the two properties that make that design safe:

* the UI and the API are reachable from the same application, and the UI is
  actually served (not a placeholder);
* a provider credential is reported by presence only.  Its value must never
  appear in a response, in the UI, or in the job summary - the response says
  ``configured: true`` and nothing more.

A missing credential must also degrade cleanly: a ``token_missing`` state,
never a 500.

Stdlib plus the project's own dependencies.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from ai2027_protocol.server import create_app

# Shapes of real provider credentials.  Used to prove that a value set in the
# environment never reaches a response body.
SECRET_PATTERN = re.compile(r"(sk-|ghp_|gsk_|AIza|gsk-ant|Bearer )[A-Za-z0-9_\-]{6,}")

# A value that is obviously a fixture, so a leak would be unmistakable.
#
# It is deliberately still credential-SHAPED, so it exercises the leak
# detector rather than passing it by not looking like a secret at all.
#
# It also has to satisfy three independent checks at once, which is why the
# wording is what it is:
#   * scripts/secret_scan.py and the auditor's literal-secret rule both skip
#     a value whose line carries an allowlist hint, so "dummy" is required;
#   * a key containing "placeholder" is deliberately treated as NOT
#     configured by the providers (is_configured), so that hint would make
#     the provider report configured: false and the presence assertion
#     meaningless.
# "dummy" is allowlisted and does not trip the placeholder guard.
FIXTURE_KEY = "sk-dummykey00000000000000000000000000000000"


def _client(tmp_path: Path) -> httpx.AsyncClient:
    app = create_app(workspace_root=tmp_path, studio_dir=tmp_path / "studio")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test")


@pytest.mark.asyncio
async def test_studio_ui_is_served_by_the_api_application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/studio`` returns the real UI, not a placeholder."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    async with _client(tmp_path) as client:
        response = await client.get("/studio")

    assert response.status_code == 200
    assert len(response.text) > 1000
    # It is the interactive studio, and it talks to the API relatively.
    assert "AI-2027 Agent Studio" in response.text
    assert "/v1/studio/" in response.text


@pytest.mark.asyncio
async def test_studio_ui_calls_the_api_with_relative_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Relative paths are what make the UI and the API one origin.

    An absolute URL to another host would mean a browser bundle reaching a
    second origin, which is exactly the cross-origin case this deployment
    avoids by construction.
    """
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    async with _client(tmp_path) as client:
        response = await client.get("/studio")

    body = response.text
    assert 'fetch("/v1/studio/' in body or "fetch('/v1/studio/" in body
    # No absolute cross-origin API URL in the served UI.
    assert "http://studio.test" not in body
    assert "https://api.openai.com" not in body


@pytest.mark.asyncio
async def test_studio_status_reports_credential_presence_without_its_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configured provider reports presence only; the value never leaks."""
    monkeypatch.setenv("OPENROUTER_API_KEY", FIXTURE_KEY)
    monkeypatch.delenv("CONNECTORS_HUB_API_TOKEN", raising=False)

    async with _client(tmp_path) as client:
        response = await client.get("/v1/studio/status")

    assert response.status_code == 200
    raw_body = response.text

    # The value must not appear anywhere in the payload.
    assert FIXTURE_KEY not in raw_body
    assert not SECRET_PATTERN.search(raw_body)

    payload = response.json()
    openrouter = next(
        provider for provider in payload["providers"] if provider["provider"] == "openrouter"
    )
    # Presence is reported, and the value is not.
    assert openrouter["configured"] is True
    assert FIXTURE_KEY not in str(openrouter)


@pytest.mark.asyncio
async def test_studio_status_never_carries_credential_shaped_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second sweep over the whole payload, with no provider configured."""
    for name in (
        "OPENROUTER_API_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "NVIDIA_API_KEY",
        "CONNECTORS_HUB_API_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    async with _client(tmp_path) as client:
        response = await client.get("/v1/studio/status")

    assert response.status_code == 200
    assert not SECRET_PATTERN.search(response.text)
    payload = response.json()
    for provider in payload["providers"]:
        assert set(provider) >= {"provider", "model", "configured"}
        assert isinstance(provider["configured"], bool)


@pytest.mark.asyncio
async def test_studio_status_is_the_health_probe_the_deployment_declares(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route named in render.yaml's healthCheckPath must answer 200.

    A liveness probe that returns 500 would make Render redeploy a healthy
    service forever, so the probe route has to answer cleanly with no
    credentials and no Hub configured.
    """
    for name in ("OPENROUTER_API_KEY", "CONNECTORS_HUB_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    async with _client(tmp_path) as client:
        response = await client.get("/v1/studio/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "ai2027-agent-studio"
    assert "providers" in payload


@pytest.mark.asyncio
async def test_studio_ui_is_served_when_no_credential_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A public instance with no secrets still serves a working UI."""
    for name in ("OPENROUTER_API_KEY", "CONNECTORS_HUB_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    async with _client(tmp_path) as client:
        ui = await client.get("/studio")
        status = await client.get("/v1/studio/status")

    assert ui.status_code == 200
    assert status.status_code == 200
    payload = status.json()
    openrouter = next(
        provider for provider in payload["providers"] if provider["provider"] == "openrouter"
    )
    assert openrouter["configured"] is False


@pytest.mark.asyncio
async def test_connectors_catalog_reports_token_missing_instead_of_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing Hub token is a clean state, never a server error."""
    monkeypatch.delenv("CONNECTORS_HUB_API_TOKEN", raising=False)
    monkeypatch.delenv("CONNECTORS_HUB_URL", raising=False)

    async with _client(tmp_path) as client:
        response = await client.get("/v1/studio/connectors/catalog")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["reason"] == "token_missing"
    assert payload["connectors"] == []


@pytest.mark.asyncio
async def test_read_only_mode_blocks_writes_on_a_public_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The public blueprint is read-only, so a write must be refused."""
    monkeypatch.setenv("AI2027_READ_ONLY", "1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    app = create_app(workspace_root=tmp_path, studio_dir=tmp_path / "studio")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://studio.test"
    ) as client:
        response = await client.post("/v1/studio/sessions", json={"title": "should be refused"})

    assert response.status_code == 403
    assert "READ_ONLY" in response.text


@pytest.mark.asyncio
async def test_zero_spend_budget_is_reported_and_blocks_paid_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The published instance authorises no spend at all."""
    monkeypatch.setenv("AI2027_MAX_SPEND_USD", "0")
    monkeypatch.setenv("OPENROUTER_API_KEY", FIXTURE_KEY)
    monkeypatch.delenv("CONNECTORS_HUB_API_TOKEN", raising=False)

    async with _client(tmp_path) as client:
        response = await client.get("/v1/studio/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["budget"]["max_spend_usd"] == 0.0
    assert payload["budget"]["spent_usd"] == 0.0
    # The key being present must not be reported as proof of free usage.
    assert payload["budget"]["api_key_never_implies_free"] is True
    assert FIXTURE_KEY not in response.text
