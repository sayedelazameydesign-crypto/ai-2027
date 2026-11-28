from __future__ import annotations

import base64
import hashlib
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI

from ai2027_protocol import studio_auth
from ai2027_protocol.server import create_app
from ai2027_protocol.studio_auth import StudioAuthConfig, install_studio_auth


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> StudioAuthConfig:
    monkeypatch.setenv("AI2027_STUDIO_AUTH_MODE", "google")
    monkeypatch.setenv("AI2027_GOOGLE_CLIENT_ID", "dummy-client")
    monkeypatch.setenv("AI2027_GOOGLE_CLIENT_SECRET", "dummy-secret")
    monkeypatch.setenv("AI2027_GOOGLE_REDIRECT_URI", "https://studio.example/auth/callback")
    monkeypatch.setenv("AI2027_STUDIO_ALLOWED_EMAIL", "owner@example.com")
    monkeypatch.setenv("AI2027_STUDIO_SESSION_KEY", base64.urlsafe_b64encode(b"x" * 32).decode())
    config = StudioAuthConfig.from_env()
    assert config is not None
    return config


def small_app(config: StudioAuthConfig) -> FastAPI:
    app = FastAPI()

    @app.get("/studio")
    async def studio() -> dict[str, str]:
        return {"private": "content"}

    @app.get("/v1/studio/sessions")
    async def sessions() -> dict[str, str]:
        return {"private": "session"}

    @app.post("/v1/studio/connectors/read")
    async def read() -> dict[str, str]:
        return {"private": "mail"}

    install_studio_auth(app, config)
    return app


def mock_google(
    monkeypatch: pytest.MonkeyPatch, *, email: str, verified: bool = True
) -> list[httpx.Request]:
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "dummy-token"})
        return httpx.Response(200, json={"sub": "123", "email": email, "email_verified": verified})

    monkeypatch.setattr(
        studio_auth,
        "_google_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    return calls


async def login(client: httpx.AsyncClient) -> tuple[str, str]:
    response = await client.get("/auth/login")
    assert response.status_code == 302
    assert "Secure" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    params = parse_qs(urlsplit(response.headers["location"]).query)
    assert params["scope"] == ["openid email"]
    assert params["code_challenge_method"] == ["S256"]
    return params["state"][0], params["code_challenge"][0]


async def test_access_login_and_csrf(
    settings: StudioAuthConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = mock_google(monkeypatch, email="owner@example.com")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=small_app(settings)), base_url="https://studio.example"
    ) as client:
        assert (await client.get("/studio")).status_code == 303
        for method, path in [
            ("GET", "/v1/studio/sessions"),
            ("POST", "/v1/studio/connectors/read"),
            ("GET", "/docs"),
        ]:
            response = await client.request(method, path)
            assert response.status_code == 401
        assert (await client.get("/auth/health")).json() == {"ok": True}
        state, challenge = await login(client)
        response = await client.get("/auth/callback", params={"state": state, "code": "dummy-code"})
        assert response.status_code == 303
        assert "dummy-token" not in response.text + str(response.headers)
        body = parse_qs(calls[0].content.decode())
        verifier = body["code_verifier"][0]
        actual = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert actual == challenge
        assert calls[1].headers["authorization"] == "Bearer dummy-token"
        assert (await client.get("/v1/studio/sessions")).json() == {"private": "session"}
        assert (await client.post("/v1/studio/connectors/read")).status_code == 403
        assert (
            await client.post(
                "/v1/studio/connectors/read", headers={"Origin": "https://evil.example"}
            )
        ).status_code == 403
        assert (
            await client.post(
                "/v1/studio/connectors/read", headers={"Origin": "https://studio.example"}
            )
        ).status_code == 200
        assert (
            await client.post("/auth/logout", headers={"Origin": "https://studio.example"})
        ).status_code == 200
        assert (await client.get("/v1/studio/sessions")).status_code == 401


@pytest.mark.parametrize(
    "email,verified", [("other@example.com", True), ("owner@example.com", False)]
)
async def test_wrong_account_denied(
    settings: StudioAuthConfig, monkeypatch: pytest.MonkeyPatch, email: str, verified: bool
) -> None:
    mock_google(monkeypatch, email=email, verified=verified)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=small_app(settings)), base_url="https://studio.example"
    ) as client:
        state, _ = await login(client)
        assert (
            await client.get("/auth/callback", params={"state": state, "code": "dummy"})
        ).status_code == 403
        assert (await client.get("/v1/studio/sessions")).status_code == 401


async def test_bad_state_and_tampered_expired_sessions(
    settings: StudioAuthConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = mock_google(monkeypatch, email="owner@example.com")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=small_app(settings)), base_url="https://studio.example"
    ) as client:
        await login(client)
        assert (
            await client.get("/auth/callback", params={"state": "wrong", "code": "dummy"})
        ).status_code == 401
        assert not calls
        for payload in [
            {"iat": int(time.time()) - 3601, "sub": "123", "email": settings.allowed_email},
            {"iat": int(time.time()), "sub": "123", "email": "other@example.com"},
        ]:
            cookie = studio_auth._sign(payload, settings.signing_key, "session")
            client.cookies.set(studio_auth._SESSION_COOKIE, cookie)
            assert (await client.get("/v1/studio/sessions")).status_code == 401
        client.cookies.set(studio_auth._SESSION_COOKIE, "broken.cookie")
        assert (await client.get("/v1/studio/sessions")).status_code == 401


def test_config_fails_closed(settings: StudioAuthConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in [
        ("AI2027_GOOGLE_CLIENT_SECRET", ""),
        ("AI2027_STUDIO_SESSION_KEY", "short"),
        ("AI2027_GOOGLE_REDIRECT_URI", "http://studio.example/auth/callback"),
        ("AI2027_STUDIO_AUTH_MODE", "typo"),
    ]:
        with monkeypatch.context() as m:
            m.setenv(key, value)
            with pytest.raises(ValueError):
                StudioAuthConfig.from_env()


async def test_real_app_routes_are_guarded(settings: StudioAuthConfig, tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path, studio_dir=tmp_path / "studio", read_only=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://studio.example"
    ) as client:
        for path in [
            "/v1/studio/status",
            "/v1/studio/sessions",
            "/v1/protocol",
            "/health",
            "/openapi.json",
        ]:
            assert (await client.get(path)).status_code == 401
        for path in ["/v1/studio/connectors/search", "/v1/studio/connectors/read"]:
            assert (await client.post(path, json={})).status_code == 401
        assert (await client.get("/auth/health")).status_code == 200


async def test_provider_failure_fails_closed(
    settings: StudioAuthConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failure(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "dummy-secret"})

    monkeypatch.setattr(
        studio_auth,
        "_google_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(failure)),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=small_app(settings)), base_url="https://studio.example"
    ) as client:
        state, _ = await login(client)
        result = await client.get("/auth/callback", params={"state": state, "code": "dummy"})
        assert result.status_code == 401
        assert "dummy-secret" not in result.text
        assert (await client.get("/v1/studio/sessions")).status_code == 401


def test_real_app_does_not_start_with_invalid_google_config(
    settings: StudioAuthConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AI2027_GOOGLE_CLIENT_SECRET")
    with pytest.raises(ValueError):
        create_app(workspace_root=tmp_path, studio_dir=tmp_path / "studio")
