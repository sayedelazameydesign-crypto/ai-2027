"""Bounded read-only connector search and resource read client for Studio & Agent."""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import quote

import httpx

from .contracts import StrictModel
from .security import SecurityValidationError, redact_secrets

_MAX_READ_BYTES = 512 * 1024
_SAFE_RESOURCE_ID = re.compile(r"^[A-Za-z0-9_./:-]{1,256}$")

_CONNECTOR_ENV_VARS: dict[str, str] = {
    "gmail": "GOOGLE_ACCESS_TOKEN",
    "google_drive": "GOOGLE_ACCESS_TOKEN",
    "google_docs": "GOOGLE_ACCESS_TOKEN",
    "google_sheets": "GOOGLE_ACCESS_TOKEN",
    "google_slides": "GOOGLE_ACCESS_TOKEN",
    "google_calendar": "GOOGLE_ACCESS_TOKEN",
    "dropbox": "DROPBOX_ACCESS_TOKEN",
    "github": "GITHUB_TOKEN",
    "linear": "LINEAR_API_KEY",
    "notion": "NOTION_TOKEN",
    "supabase": "SUPABASE_URL",
    "vercel": "VERCEL_TOKEN",
}


class ConnectorSearchItem(StrictModel):
    """Sanitized single hit from a read-only external connector search."""

    connector: str
    item_id: str
    title: str
    url: str | None = None
    snippet: str | None = None


class ConnectorSearchOutcome(StrictModel):
    """Structured outcome returned by read-only connector search."""

    connector: str
    ok: bool
    configured: bool
    query: str
    items: tuple[ConnectorSearchItem, ...] = ()
    error: str | None = None


class ConnectorReadOutcome(StrictModel):
    """Bounded UTF-8 text payload read from one explicit external resource."""

    connector: str
    ok: bool
    configured: bool
    resource_id: str
    title: str = ""
    content: str = ""
    truncated: bool = False
    error: str | None = None


def _validate_resource_id(resource_id: str) -> str:
    clean = resource_id.strip()
    if not clean or ".." in clean or "\\" in clean or not _SAFE_RESOURCE_ID.fullmatch(clean):
        raise SecurityValidationError("Connector resource_id contains unsupported characters.")
    return clean


def _bounded_text(raw_obj: object) -> tuple[str, bool]:
    if isinstance(raw_obj, str):
        encoded = raw_obj.encode("utf-8", errors="replace")
        truncated = len(encoded) > _MAX_READ_BYTES
        text = encoded[:_MAX_READ_BYTES].decode("utf-8", errors="replace")
        return redact_secrets(text), truncated
    import json

    dumped = json.dumps(raw_obj, indent=2, ensure_ascii=False)
    encoded = dumped.encode("utf-8", errors="replace")
    truncated = len(encoded) > _MAX_READ_BYTES
    text = encoded[:_MAX_READ_BYTES].decode("utf-8", errors="replace")
    return redact_secrets(text), truncated


class LiveConnectorClient:
    """Safe, read-only connector search and fetch client with optional mock injection."""

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        """Create a connector reader using an optional injected httpx.AsyncClient."""
        self._injected_client = client

    def _get_token(self, connector: str) -> tuple[str | None, str]:
        if connector not in _CONNECTOR_ENV_VARS:
            raise SecurityValidationError(f"Unsupported connector {connector!r}.")
        env_var = _CONNECTOR_ENV_VARS[connector]
        val = os.environ.get(env_var, "").strip()
        return (val or None), env_var

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not url.startswith("https://"):
            raise SecurityValidationError("Only HTTPS connector endpoints are allowed.")
        if self._injected_client is not None:
            resp = await self._injected_client.request(
                method, url, headers=headers, params=params, json=json_body
            )
        else:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=4.0), follow_redirects=False
            ) as client:
                resp = await client.request(
                    method, url, headers=headers, params=params, json=json_body
                )
        resp.raise_for_status()
        if len(resp.content) > _MAX_READ_BYTES * 2:
            raise SecurityValidationError("Connector response exceeded bounded size limit.")
        payload = resp.json()
        return payload if isinstance(payload, dict) else {"items": payload}

    async def search(self, connector: str, query: str, *, limit: int = 5) -> ConnectorSearchOutcome:
        """Search a supported connector cleanly; report missing credentials honestly."""
        cid = connector.strip().lower()
        clean_q = query.strip()[:160]
        if not clean_q:
            raise SecurityValidationError("Connector search requires a non-empty query.")
        bounded_limit = max(1, min(limit, 10))
        token, env_var = self._get_token(cid)
        if token is None:
            return ConnectorSearchOutcome(
                connector=cid,
                ok=False,
                configured=False,
                query=clean_q,
                error=f"Credential {env_var} is absent; configure env var for live search.",
            )

        try:
            items = await self._dispatch_search(cid, token, clean_q, bounded_limit)
        except httpx.HTTPError as exc:
            return ConnectorSearchOutcome(
                connector=cid,
                ok=False,
                configured=True,
                query=clean_q,
                error=redact_secrets(f"Provider HTTP error: {type(exc).__name__}"),
            )
        except ValueError as exc:
            return ConnectorSearchOutcome(
                connector=cid,
                ok=False,
                configured=True,
                query=clean_q,
                error=redact_secrets(str(exc)),
            )

        return ConnectorSearchOutcome(
            connector=cid,
            ok=True,
            configured=True,
            query=clean_q,
            items=tuple(items[:bounded_limit]),
        )

    async def read(self, connector: str, resource_id: str) -> ConnectorReadOutcome:
        """Fetch one explicit resource from a configured connector within 512 KiB."""
        cid = connector.strip().lower()
        clean_id = _validate_resource_id(resource_id)
        token, env_var = self._get_token(cid)
        if token is None:
            return ConnectorReadOutcome(
                connector=cid,
                ok=False,
                configured=False,
                resource_id=clean_id,
                error=f"Credential {env_var} is absent; configure env var for live read.",
            )

        try:
            title, raw_payload = await self._dispatch_read(cid, token, clean_id)
        except httpx.HTTPError as exc:
            return ConnectorReadOutcome(
                connector=cid,
                ok=False,
                configured=True,
                resource_id=clean_id,
                error=redact_secrets(f"Provider HTTP error: {type(exc).__name__}"),
            )
        except ValueError as exc:
            return ConnectorReadOutcome(
                connector=cid,
                ok=False,
                configured=True,
                resource_id=clean_id,
                error=redact_secrets(str(exc)),
            )

        text, truncated = _bounded_text(raw_payload)
        return ConnectorReadOutcome(
            connector=cid,
            ok=True,
            configured=True,
            resource_id=clean_id,
            title=redact_secrets(title)[:200],
            content=text,
            truncated=truncated,
        )

    async def _dispatch_search(
        self, cid: str, token: str, query: str, limit: int
    ) -> list[ConnectorSearchItem]:
        if cid == "github":
            data = await self._request(
                "GET",
                "https://api.github.com/search/repositories",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
                params={"q": query, "per_page": limit},
            )
            return [
                ConnectorSearchItem(
                    connector="github",
                    item_id=str(row.get("full_name") or row.get("id") or ""),
                    title=redact_secrets(str(row.get("full_name") or "repo"))[:180],
                    url=str(row.get("html_url") or "") or None,
                    snippet=redact_secrets(str(row.get("description") or ""))[:240] or None,
                )
                for row in (data.get("items") or [])
                if isinstance(row, dict)
            ]

        if cid == "notion":
            data = await self._request(
                "POST",
                "https://api.notion.com/v1/search",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Notion-Version": "2022-06-28",
                },
                json_body={"query": query, "page_size": limit},
            )
            return [
                ConnectorSearchItem(
                    connector="notion",
                    item_id=str(row.get("id") or ""),
                    title=redact_secrets(str(row.get("object") or "page"))[:180],
                    url=str(row.get("url") or "") or None,
                )
                for row in (data.get("results") or [])
                if isinstance(row, dict)
            ]

        if cid == "linear":
            gql = (
                "query($q: String!, $n: Int!) { "
                "issueSearch(query: $q, first: $n) { nodes { id identifier title url } } }"
            )
            data = await self._request(
                "POST",
                "https://api.linear.app/graphql",
                headers={"Authorization": token},
                json_body={"query": gql, "variables": {"q": query, "n": limit}},
            )
            nodes = ((data.get("data") or {}).get("issueSearch") or {}).get("nodes") or []
            return [
                ConnectorSearchItem(
                    connector="linear",
                    item_id=str(row.get("identifier") or row.get("id") or ""),
                    title=redact_secrets(str(row.get("title") or "issue"))[:180],
                    url=str(row.get("url") or "") or None,
                )
                for row in nodes
                if isinstance(row, dict)
            ]

        if cid in {"google_drive", "google_docs", "google_sheets", "google_slides"}:
            escaped = query.replace("\\", "\\\\").replace("'", "\\'")
            data = await self._request(
                "GET",
                "https://www.googleapis.com/drive/v3/files",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "q": f"trashed = false and name contains '{escaped}'",
                    "pageSize": limit,
                    "fields": "files(id,name,webViewLink)",
                },
            )
            return [
                ConnectorSearchItem(
                    connector=cid,
                    item_id=str(row.get("id") or ""),
                    title=redact_secrets(str(row.get("name") or "file"))[:180],
                    url=str(row.get("webViewLink") or "") or None,
                )
                for row in (data.get("files") or [])
                if isinstance(row, dict)
            ]

        if cid == "vercel":
            data = await self._request(
                "GET",
                "https://api.vercel.com/v9/projects",
                headers={"Authorization": f"Bearer {token}"},
                params={"search": query, "limit": limit},
            )
            return [
                ConnectorSearchItem(
                    connector="vercel",
                    item_id=str(row.get("id") or row.get("name") or ""),
                    title=redact_secrets(str(row.get("name") or "project"))[:180],
                )
                for row in (data.get("projects") or [])
                if isinstance(row, dict)
            ]

        raise ValueError(
            f"Read-only search for {cid!r} requires explicit provider parameters; "
            "use connector.inspect or connector.propose_action."
        )

    async def _dispatch_read(
        self, cid: str, token: str, resource_id: str
    ) -> tuple[str, dict[str, Any]]:
        if cid == "github":
            parts = resource_id.split("/")
            if len(parts) < 2:
                raise SecurityValidationError("GitHub resource_id must be owner/repo[/path].")
            owner = quote(parts[0], safe="")
            repo = quote(parts[1], safe="")
            if len(parts) == 2:
                url = f"https://api.github.com/repos/{owner}/{repo}"
            else:
                sub = "/".join(quote(seg, safe="") for seg in parts[2:])
                url = f"https://api.github.com/repos/{owner}/{repo}/contents/{sub}"
            data = await self._request(
                "GET",
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            return str(data.get("full_name") or data.get("name") or resource_id), data

        if cid == "notion":
            pid = quote(resource_id, safe="")
            data = await self._request(
                "GET",
                f"https://api.notion.com/v1/pages/{pid}",
                headers={"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28"},
            )
            return str(data.get("id") or resource_id), data

        if cid == "vercel":
            pid = quote(resource_id, safe="")
            data = await self._request(
                "GET",
                f"https://api.vercel.com/v9/projects/{pid}",
                headers={"Authorization": f"Bearer {token}"},
            )
            return str(data.get("name") or resource_id), data

        raise ValueError(
            f"Live direct read is not configured for {cid!r} resource {resource_id!r}."
        )


__all__ = [
    "ConnectorReadOutcome",
    "ConnectorSearchItem",
    "ConnectorSearchOutcome",
    "LiveConnectorClient",
]
