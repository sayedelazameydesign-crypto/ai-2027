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


# --------------------------------------------------------------------------
# The model id the published Studio routes OpenRouter calls to.
#
# The blueprint declares OPENROUTER_MODEL as a dashboard value (sync: false)
# so the free model can be changed without a code change.  Two properties
# have to hold for that to be safe, and both are asserted below:
#
#   * the declared id is a genuinely free one - a paid id, "openrouter/auto"
#     or a "~latest" alias is UNKNOWN_COST and is refused at MAX_SPEND_USD=0;
#   * AI2027_FREE_MODELS cannot be used to reclassify OpenRouter.  That
#     allowlist is deliberately ignored for this provider, because the
#     presence of an API key does not prove $0.
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
RENDER_YAML = REPO_ROOT / "render.yaml"


def _studio_env_block() -> list[str]:
    """Return the envVar lines of the ai-2027-studio service.

    The blueprint is parsed by line rather than with PyYAML on purpose:
    PyYAML is NOT a dependency of this package (it is absent from uv.lock),
    so importing it here would break CI, which installs only ``.[dev]``.
    The CI workflow parses render.yaml with PyYAML in a separate job that
    installs it explicitly; these tests must run without it.

    The block runs from the service's ``name:`` line to the next service
    (a line starting with ``- type:``) or to the end of the file.
    """
    lines = RENDER_YAML.read_text(encoding="utf-8").splitlines()

    start: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == "name: ai-2027-studio":
            start = index
            break
    assert start is not None, "render.yaml does not declare the ai-2027-studio service"

    block: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("  - type:"):
            break
        block.append(line)
    return block


def _env_var(block: list[str], key: str) -> dict[str, str]:
    """Return the properties of one env var declared in the service block."""
    lines = [line.strip() for line in block]
    assert f"- key: {key}" in lines, f"{key} is not declared for ai-2027-studio"

    index = lines.index(f"- key: {key}")
    properties: dict[str, str] = {}
    for line in lines[index + 1 :]:
        if line.startswith("- key:"):
            break
        if ":" in line:
            name, _, value = line.partition(":")
            properties[name.strip()] = value.strip().strip('"')
    return properties


def test_blueprint_declares_the_openrouter_model_as_a_dashboard_value() -> None:
    """OPENROUTER_MODEL must be declared, and must not carry a committed value."""
    properties = _env_var(_studio_env_block(), "OPENROUTER_MODEL")

    # sync: false -> Render prompts for it and it never lands in Git.
    assert properties.get("sync") == "false", "OPENROUTER_MODEL must be sync: false"
    # No committed value: a value here would become the default for every
    # deployment of this blueprint.
    assert "value" not in properties, "OPENROUTER_MODEL must not carry a committed value"


def test_blueprint_keeps_the_studio_read_only_and_zero_spend() -> None:
    """The public instance must stay unable to write and unable to spend."""
    block = _studio_env_block()

    assert _env_var(block, "AI2027_READ_ONLY").get("value") == "1"
    assert _env_var(block, "AI2027_MAX_SPEND_USD").get("value") == "0"


def test_openrouter_free_router_is_classified_free_tier() -> None:
    """The recommended free id is admitted at zero spend."""
    from ai2027_protocol.budget import classify_billing_tier
    from ai2027_protocol.contracts import ProviderKind

    tier, _reason = classify_billing_tier(
        kind=ProviderKind.OPENROUTER, model="openrouter/free", is_remote=True
    )

    assert tier.value == "free_tier"


@pytest.mark.parametrize(
    "model",
    [
        "meta-llama/llama-3.3-70b-instruct:free",
        "qwen/qwen3-235b-a22b:free",
    ],
)
def test_explicit_free_suffixed_models_are_classified_free_tier(model: str) -> None:
    """A pinned ':free' id is admitted too."""
    from ai2027_protocol.budget import classify_billing_tier
    from ai2027_protocol.contracts import ProviderKind

    tier, _reason = classify_billing_tier(kind=ProviderKind.OPENROUTER, model=model, is_remote=True)

    assert tier.value == "free_tier"


@pytest.mark.parametrize(
    "model",
    [
        "openrouter/auto",
        "openrouter/auto-code",
        "anthropic/claude-sonnet-4",
        "openrouter/meta-llama/llama-3.3-70b-instruct",
    ],
)
def test_non_free_openrouter_models_remain_unknown_cost(model: str) -> None:
    """A paid id, an auto router or a latest alias is never free-tier."""
    from ai2027_protocol.budget import classify_billing_tier
    from ai2027_protocol.contracts import ProviderKind

    tier, _reason = classify_billing_tier(kind=ProviderKind.OPENROUTER, model=model, is_remote=True)

    assert tier.value == "unknown_cost", f"{model} must not be free-tier"


def test_free_models_allowlist_cannot_reclassify_openrouter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AI2027_FREE_MODELS must not open a paid model at zero spend."""
    from ai2027_protocol.budget import classify_billing_tier
    from ai2027_protocol.contracts import ProviderKind

    monkeypatch.setenv("AI2027_FREE_MODELS", "openrouter/auto,anthropic/claude-sonnet-4")

    for model in ("openrouter/auto", "anthropic/claude-sonnet-4"):
        tier, _reason = classify_billing_tier(
            kind=ProviderKind.OPENROUTER, model=model, is_remote=True
        )
        assert tier.value == "unknown_cost", f"{model} was reclassified via AI2027_FREE_MODELS"


def test_unset_or_blank_openrouter_model_falls_back_to_the_free_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leaving the dashboard field blank must stay free, not become a paid id."""
    from ai2027_protocol.ai_providers import create_openrouter_free_provider
    from ai2027_protocol.budget import classify_billing_tier

    for value in (None, "", "   "):
        monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
        if value is not None:
            monkeypatch.setenv("OPENROUTER_MODEL", value)

        provider = create_openrouter_free_provider(api_key="k")
        tier, _reason = classify_billing_tier(
            kind=provider.kind, model=provider.model, is_remote=True
        )

        assert provider.model == "openrouter/free"
        assert tier.value == "free_tier"
