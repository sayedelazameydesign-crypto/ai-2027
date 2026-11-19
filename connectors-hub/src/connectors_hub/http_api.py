"""Provider HTTP helpers. Never include credential or provider response bodies in errors."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from .catalog import ConnectorSpec
from .config import ConfigurationError, Settings, validate_supabase_url
from .credentials import CredentialVault, VaultError
from .models import ConnectorId


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _raise_for_provider_status(response: httpx.Response) -> None:
    if response.status_code == 401:
        raise ProviderError(
            "authentication_failed",
            "Provider rejected the credential; reconnect it locally",
            status_code=401,
        )
    if response.status_code == 403:
        raise ProviderError(
            "permission_denied",
            "Provider denied this operation or the requested scope is missing",
            status_code=403,
        )
    if 300 <= response.status_code < 400:
        raise ProviderError(
            "redirect_blocked",
            "Provider returned a redirect; it was not followed",
            status_code=response.status_code,
        )
    if response.status_code >= 400:
        # Do not forward provider response bodies: they can include user data,
        # request details, or sensitive diagnostics.
        raise ProviderError(
            "provider_error",
            f"Provider returned HTTP {response.status_code}",
            status_code=response.status_code,
        )


async def _request_bounded(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any] | None,
    json_body: Any,
    max_bytes: int,
) -> httpx.Response:
    try:
        async with client.stream(
            method,
            url,
            headers=headers,
            params=params,
            json=json_body,
            follow_redirects=False,
        ) as response:
            _raise_for_provider_status(response)
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ProviderError(
                        "response_too_large",
                        f"Provider response exceeded the {max_bytes}-byte read limit",
                    )
                chunks.append(chunk)
            return httpx.Response(
                status_code=response.status_code,
                headers=response.headers,
                content=b"".join(chunks),
                request=response.request,
            )
    except httpx.TimeoutException as exc:
        raise ProviderError("provider_timeout", "Provider request timed out") from exc
    except httpx.RequestError as exc:
        raise ProviderError(
            "provider_unavailable", "Provider request could not be completed"
        ) from exc


def _assert_trusted_base(spec: ConnectorSpec, settings: Settings) -> str:
    if spec.id == ConnectorId.SUPABASE:
        try:
            base = validate_supabase_url(settings.supabase_url)
        except ConfigurationError as exc:
            raise ProviderError(
                "unsafe_endpoint", "Supabase endpoint is not an approved project URL"
            ) from exc
    else:
        base = spec.api_base
    if not base:
        raise ProviderError("not_configured", "Provider endpoint is not configured")
    parsed = urlsplit(base)
    if parsed.scheme != "https" and not (
        parsed.hostname in {"localhost", "127.0.0.1", "::1"} and parsed.scheme == "http"
    ):
        raise ProviderError("unsafe_endpoint", "Provider endpoint must use HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProviderError(
            "unsafe_endpoint", "Provider endpoint contains unsupported URL components"
        )
    return base.rstrip("/")


def provider_headers(connector: ConnectorId, vault: CredentialVault) -> dict[str, str]:
    try:
        if connector == ConnectorId.SUPABASE:
            publishable_key = vault.require("supabase_publishable_key")
            user_jwt = vault.require("supabase_user_jwt")
            return {
                "apikey": publishable_key,
                "Authorization": f"Bearer {user_jwt}",
                "Accept": "application/json",
            }
        spec_auth = {
            ConnectorId.DROPBOX: ("dropbox_access_token", "Bearer"),
            ConnectorId.GITHUB: ("github_token", "Bearer"),
            ConnectorId.GMAIL: ("google_access_token", "Bearer"),
            ConnectorId.GOOGLE_CALENDAR: ("google_access_token", "Bearer"),
            ConnectorId.GOOGLE_DRIVE: ("google_access_token", "Bearer"),
            ConnectorId.GOOGLE_DOCS: ("google_access_token", "Bearer"),
            ConnectorId.GOOGLE_SHEETS: ("google_access_token", "Bearer"),
            ConnectorId.GOOGLE_SLIDES: ("google_access_token", "Bearer"),
            ConnectorId.LINEAR: ("linear_api_key", ""),
            ConnectorId.NOTION: ("notion_integration_token", "Bearer"),
            ConnectorId.VERCEL: ("vercel_token", "Bearer"),
        }
        name, scheme = spec_auth[connector]
        token = vault.require(name)
    except VaultError as exc:
        raise ProviderError(
            "not_configured",
            "Provider credential is missing or the local keyring is unavailable",
        ) from exc

    authorization = f"{scheme} {token}" if scheme else token
    headers = {"Authorization": authorization, "Accept": "application/json"}
    if connector == ConnectorId.GITHUB:
        headers["X-GitHub-Api-Version"] = "2022-11-28"
        headers["Accept"] = "application/vnd.github+json"
    elif connector == ConnectorId.NOTION:
        headers["Notion-Version"] = "2022-06-28"
    elif connector == ConnectorId.DROPBOX or connector == ConnectorId.LINEAR:
        headers["Content-Type"] = "application/json"
    return headers


async def request_json(
    client: httpx.AsyncClient,
    spec: ConnectorSpec,
    settings: Settings,
    vault: CredentialVault,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
) -> Any:
    base = _assert_trusted_base(spec, settings)
    # Paths are connector-owned constants, not user-supplied URLs.
    if not path or path.startswith("//") or ".." in path.split("/"):
        raise ProviderError("unsafe_path", "Provider path failed validation")
    url = f"{base}/{path.lstrip('/')}"
    response = await _request_bounded(
        client,
        method,
        url,
        headers=provider_headers(spec.id, vault),
        params=params,
        json_body=json_body,
        max_bytes=4 * 1024 * 1024,
    )
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError(
            "invalid_provider_response", "Provider response was not valid JSON"
        ) from exc


async def request_raw(
    client: httpx.AsyncClient,
    connector: ConnectorId,
    vault: CredentialVault,
    method: str,
    url: str,
    *,
    allowed_hosts: frozenset[str],
    params: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
    json_body: Any = None,
    max_bytes: int = 512 * 1024,
) -> httpx.Response:
    """Request a bounded non-JSON body from a hard-coded provider host only."""
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ProviderError(
            "unsafe_endpoint", "Provider endpoint is malformed"
        ) from exc
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() not in allowed_hosts
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or port not in {None, 443}
    ):
        raise ProviderError(
            "unsafe_endpoint",
            "Non-JSON reads are restricted to an approved HTTPS provider host",
        )
    headers = provider_headers(connector, vault)
    for key, value in (extra_headers or {}).items():
        if key.lower() in {"authorization", "host", "content-length"}:
            raise ProviderError(
                "unsafe_header",
                "A provider credential or transport header cannot be overridden",
            )
        headers[key] = value
    return await _request_bounded(
        client,
        method,
        url,
        headers=headers,
        params=params,
        json_body=json_body,
        max_bytes=max_bytes,
    )
