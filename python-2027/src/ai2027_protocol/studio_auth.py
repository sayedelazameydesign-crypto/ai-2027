"""Optional fail-closed Google sign-in for a single-owner Studio deployment.

The authorization-code flow uses PKCE and Google's HTTPS userinfo endpoint.
It requests identity scopes only; it does not grant or access Gmail.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 -- endpoint, not a credential
_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
_STATE_COOKIE = "__Host-ai2027-oauth"
_SESSION_COOKIE = "__Host-ai2027-session"
_SESSION_SECONDS = 3600
_STATE_SECONDS = 600


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode(raw: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", raw):
        raise ValueError("invalid encoding")
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


@dataclass(frozen=True)
class StudioAuthConfig:
    """Validated identity configuration; secrets never enter responses."""

    client_id: str
    client_secret: str
    redirect_uri: str
    allowed_email: str
    signing_key: bytes

    @property
    def origin(self) -> str:
        """Return the configured browser origin for CSRF checks."""
        parsed = urlsplit(self.redirect_uri)
        return f"{parsed.scheme}://{parsed.netloc}"

    @classmethod
    def from_env(cls) -> StudioAuthConfig | None:
        """Load configuration or fail closed when Google authentication is selected."""
        mode = os.environ.get("AI2027_STUDIO_AUTH_MODE", "off")
        if mode == "off":
            return None
        if mode != "google":
            raise ValueError("AI2027_STUDIO_AUTH_MODE must be off or google")
        client_id = os.environ.get("AI2027_GOOGLE_CLIENT_ID", "").strip()
        secret = os.environ.get("AI2027_GOOGLE_CLIENT_SECRET", "").strip()
        uri = os.environ.get("AI2027_GOOGLE_REDIRECT_URI", "").strip()
        email = os.environ.get("AI2027_STUDIO_ALLOWED_EMAIL", "").strip().lower()
        encoded_key = os.environ.get("AI2027_STUDIO_SESSION_KEY", "").strip()
        try:
            key = _decode(encoded_key.rstrip("="))
            parsed = urlsplit(uri)
            port = parsed.port
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Invalid Studio authentication configuration") from exc
        if (
            not client_id
            or not secret
            or len(key) < 32
            or not re.fullmatch(r"[^\s@]+@[^\s@]+", email)
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path != "/auth/callback"
            or parsed.query
            or parsed.fragment
            or (port is not None and port != 443)
        ):
            raise ValueError("Invalid Studio authentication configuration")
        return cls(client_id, secret, uri, email, key)


def _sign(payload: dict[str, Any], key: bytes, purpose: str) -> str:
    body = _encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    mac = hmac.new(key, f"{purpose}.{body}".encode(), hashlib.sha256).digest()
    return f"{body}.{_encode(mac)}"


def _verify(value: str, key: bytes, purpose: str, max_age: int) -> dict[str, Any] | None:
    try:
        if len(value) > 4096:
            return None
        body, signature = value.split(".")
        expected = hmac.new(key, f"{purpose}.{body}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _decode(signature)):
            return None
        payload = json.loads(_decode(body))
        if not isinstance(payload, dict) or type(payload.get("iat")) is not int:
            return None
        age = int(time.time()) - payload["iat"]
        return payload if 0 <= age <= max_age else None
    except (ValueError, TypeError, UnicodeError):
        return None


def _cookie(response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(
        name, value, max_age=max_age, secure=True, httponly=True, samesite="lax", path="/"
    )


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"detail": message}, status_code=status, headers={"Cache-Control": "no-store"}
    )


def _google_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False)


def install_studio_auth(app: FastAPI, config: StudioAuthConfig) -> None:
    """Protect the entire app except the login callback and minimal health endpoint."""

    @app.middleware("http")
    async def authentication(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if path == "/auth/health" and request.method == "GET":
            return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
        if path == "/auth/login" and request.method == "GET":
            login_state = secrets.token_urlsafe(32)
            verifier = secrets.token_urlsafe(48)
            challenge = _encode(hashlib.sha256(verifier.encode()).digest())
            params = {
                "client_id": config.client_id,
                "redirect_uri": config.redirect_uri,
                "response_type": "code",
                "scope": "openid email",
                "state": login_state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
            response: Response = RedirectResponse(
                f"{_AUTH_URL}?{urlencode(params)}", status_code=302
            )
            _cookie(
                response,
                _STATE_COOKIE,
                _sign(
                    {"state": login_state, "verifier": verifier, "iat": int(time.time())},
                    config.signing_key,
                    "oauth",
                ),
                _STATE_SECONDS,
            )
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response
        if path == "/auth/callback" and request.method == "GET":
            state = _verify(
                request.cookies.get(_STATE_COOKIE, ""), config.signing_key, "oauth", _STATE_SECONDS
            )
            code = request.query_params.get("code", "")
            supplied = request.query_params.get("state", "")
            response_error = _error(401, "Google sign-in failed")
            response_error.delete_cookie(
                _STATE_COOKIE, path="/", secure=True, httponly=True, samesite="lax"
            )
            if (
                not state
                or not isinstance(state.get("state"), str)
                or not isinstance(state.get("verifier"), str)
                or not supplied
                or not hmac.compare_digest(state["state"].encode(), supplied.encode())
                or not code
                or len(code) > 4096
            ):
                return response_error
            try:
                async with _google_client() as client:
                    token_response = await client.post(
                        _TOKEN_URL,
                        data={
                            "code": code,
                            "client_id": config.client_id,
                            "client_secret": config.client_secret,
                            "redirect_uri": config.redirect_uri,
                            "grant_type": "authorization_code",
                            "code_verifier": state["verifier"],
                        },
                    )
                    token_response.raise_for_status()
                    token = token_response.json().get("access_token")
                    if not isinstance(token, str) or not token or len(token) > 4096:
                        return response_error
                    user_response = await client.get(
                        _USERINFO_URL, headers={"Authorization": f"Bearer {token}"}
                    )
                    user_response.raise_for_status()
                    user = user_response.json()
                email = user.get("email")
                subject = user.get("sub")
                if (
                    not isinstance(email, str)
                    or email.lower() != config.allowed_email
                    or user.get("email_verified") is not True
                    or not isinstance(subject, str)
                    or not subject
                    or len(subject) > 255
                ):
                    denied = _error(403, "Account is not authorized")
                    denied.delete_cookie(
                        _STATE_COOKIE, path="/", secure=True, httponly=True, samesite="lax"
                    )
                    return denied
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                return response_error
            response = RedirectResponse("/studio", status_code=303)
            response.delete_cookie(
                _STATE_COOKIE, path="/", secure=True, httponly=True, samesite="lax"
            )
            _cookie(
                response,
                _SESSION_COOKIE,
                _sign(
                    {"sub": subject, "email": email.lower(), "iat": int(time.time())},
                    config.signing_key,
                    "session",
                ),
                _SESSION_SECONDS,
            )
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response
        session = _verify(
            request.cookies.get(_SESSION_COOKIE, ""),
            config.signing_key,
            "session",
            _SESSION_SECONDS,
        )
        if not session or session.get("email") != config.allowed_email or not session.get("sub"):
            if request.method == "GET" and path in {"/", "/studio"}:
                return RedirectResponse(
                    "/auth/login", status_code=303, headers={"Cache-Control": "no-store"}
                )
            return _error(401, "Authentication required")
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.headers.get("origin") != config.origin
        ):
            return _error(403, "Invalid request origin")
        if path == "/auth/logout" and request.method == "POST":
            response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
            response.delete_cookie(
                _SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax"
            )
            return response
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response
