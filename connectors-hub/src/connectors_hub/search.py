"""Read-only, provider-native search. No results are persisted or indexed."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

from .catalog import SPECS, ConnectorSpec
from .config import Settings
from .credentials import CredentialVault, VaultError
from .http_api import ProviderError, request_json
from .models import (
    ConnectorId,
    SearchFailure,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

_GOOGLE_MIME_TYPES = {
    ConnectorId.GOOGLE_DOCS: "application/vnd.google-apps.document",
    ConnectorId.GOOGLE_SHEETS: "application/vnd.google-apps.spreadsheet",
    ConnectorId.GOOGLE_SLIDES: "application/vnd.google-apps.presentation",
}
_SAFE_SUPABASE_QUERY = re.compile(r"^[\w\s.@+-]{1,160}$", re.UNICODE)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _text(value: Any, limit: int = 500) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value[:limit]


def _result(
    connector: ConnectorId,
    item_id: Any,
    title: Any,
    *,
    url: Any = None,
    snippet: Any = None,
    updated: Any = None,
    metadata: dict[str, Any] | None = None,
) -> SearchResult | None:
    if not isinstance(item_id, (str, int)):
        return None
    clean_title = _text(title, 240) or str(item_id)
    return SearchResult(
        connector=connector,
        id=str(item_id)[:200],
        title=clean_title,
        url=_text(url, 1000),
        snippet=_text(snippet, 500),
        updated_at=_text(updated, 80),
        metadata=metadata or {},
    )


def _google_drive_query(query: str, mime_type: str | None = None) -> str:
    escaped = query.replace("\\", "\\\\").replace("'", "\\'")
    parts = [f"name contains '{escaped}' or fullText contains '{escaped}'"]
    if mime_type:
        parts = [f"mimeType = '{mime_type}'", f"({parts[0]})"]
    return "trashed = false and (" + " and ".join(parts) + ")"


def _dropbox_match(
    match: dict[str, Any], connector: ConnectorId
) -> SearchResult | None:
    metadata = match.get("metadata", {})
    item = metadata.get("metadata", metadata) if isinstance(metadata, dict) else {}
    if not isinstance(item, dict):
        item = {}
    return _result(
        connector,
        item.get("id") or item.get("path_lower") or item.get("name"),
        item.get("name") or item.get("path_display"),
        url=item.get("preview_url"),
        snippet=item.get("highlight_and_snippet"),
        updated=item.get("server_modified"),
        metadata={
            "path": _text(item.get("path_display"), 500),
            "kind": item.get(".tag"),
        },
    )


async def _search_google(
    connector: ConnectorId,
    spec: ConnectorSpec,
    request: SearchRequest,
    client: httpx.AsyncClient,
    settings: Settings,
    vault: CredentialVault,
) -> list[SearchResult]:
    if connector == ConnectorId.GMAIL:
        data = await request_json(
            client,
            spec,
            settings,
            vault,
            "GET",
            "messages",
            params={
                "q": request.query,
                "maxResults": request.limit,
            },
        )
        messages = data.get("messages", []) if isinstance(data, dict) else []
        sem = asyncio.Semaphore(8)

        async def hydrate(message: dict[str, Any]) -> SearchResult | None:
            message_id = message.get("id")
            if not message_id:
                return None
            async with sem:
                details = await request_json(
                    client,
                    spec,
                    settings,
                    vault,
                    "GET",
                    f"messages/{message_id}",
                    params={
                        "format": "metadata",
                        "metadataHeaders": ["Subject", "From", "Date"],
                    },
                )
            headers = (
                details.get("payload", {}).get("headers", [])
                if isinstance(details, dict)
                else []
            )
            header_map = {
                h.get("name", "").lower(): h.get("value")
                for h in headers
                if isinstance(h, dict)
            }
            return _result(
                connector,
                message_id,
                header_map.get("subject") or details.get("snippet") or "Gmail message",
                url=f"https://mail.google.com/mail/u/0/#all/{message_id}",
                snippet=details.get("snippet"),
                updated=details.get("internalDate"),
                metadata={
                    "from": _text(header_map.get("from"), 200),
                    "date": _text(header_map.get("date"), 80),
                },
            )

        hydrated = await asyncio.gather(
            *(hydrate(m) for m in messages[: request.limit] if isinstance(m, dict))
        )
        return [item for item in hydrated if item is not None]

    if connector == ConnectorId.GOOGLE_CALENDAR:
        data = await request_json(
            client,
            spec,
            settings,
            vault,
            "GET",
            "calendars/primary/events",
            params={
                "q": request.query,
                "maxResults": request.limit,
                "singleEvents": "true",
                "orderBy": "startTime",
                "fields": "items(id,summary,description,htmlLink,updated,start,end),nextPageToken",
            },
        )
        rows = data.get("items", []) if isinstance(data, dict) else []
        return [
            r
            for item in rows
            if isinstance(item, dict)
            if (
                r := _result(
                    connector,
                    item.get("id"),
                    item.get("summary"),
                    url=item.get("htmlLink"),
                    snippet=item.get("description"),
                    updated=item.get("updated"),
                    metadata={"start": item.get("start"), "end": item.get("end")},
                )
            )
            is not None
        ]

    mime_type = _GOOGLE_MIME_TYPES.get(connector)
    params = {
        "q": _google_drive_query(request.query, mime_type),
        "pageSize": request.limit,
        "orderBy": "modifiedTime desc",
        "fields": "files(id,name,mimeType,modifiedTime,webViewLink,description),nextPageToken",
    }
    data = await request_json(
        client, spec, settings, vault, "GET", "files", params=params
    )
    rows = data.get("files", []) if isinstance(data, dict) else []
    return [
        r
        for item in rows
        if isinstance(item, dict)
        if (
            r := _result(
                connector,
                item.get("id"),
                item.get("name"),
                url=item.get("webViewLink"),
                snippet=item.get("description"),
                updated=item.get("modifiedTime"),
                metadata={"mime_type": item.get("mimeType")},
            )
        )
        is not None
    ]


async def _search_github(
    spec: ConnectorSpec,
    request: SearchRequest,
    client: httpx.AsyncClient,
    settings: Settings,
    vault: CredentialVault,
) -> list[SearchResult]:
    results: list[SearchResult] = []
    for endpoint in ("search/issues", "search/repositories"):
        data = await request_json(
            client,
            spec,
            settings,
            vault,
            "GET",
            endpoint,
            params={
                "q": request.query,
                "per_page": request.limit,
            },
        )
        rows = data.get("items", []) if isinstance(data, dict) else []
        for item in rows:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or item.get("full_name") or item.get("name")
            description = item.get("body") or item.get("description")
            result = _result(
                ConnectorId.GITHUB,
                item.get("id") or item.get("node_id"),
                title,
                url=item.get("html_url"),
                snippet=description,
                updated=item.get("updated_at"),
                metadata={
                    "kind": "pull_request"
                    if "pull_request" in item
                    else "issue"
                    if "issue_url" in item
                    else "repository",
                    "repository": (item.get("repository") or {}).get("full_name")
                    if isinstance(item.get("repository"), dict)
                    else item.get("full_name"),
                },
            )
            if result:
                results.append(result)
    return results[: request.limit * 2]


async def _search_linear(
    spec: ConnectorSpec,
    request: SearchRequest,
    client: httpx.AsyncClient,
    settings: Settings,
    vault: CredentialVault,
) -> list[SearchResult]:
    query = """query SearchIssues($query: String!) {
      issueSearch(query: $query) {
        nodes { id identifier title description url updatedAt state { name } team { name } }
      }
    }"""
    data = await request_json(
        client,
        spec,
        settings,
        vault,
        "POST",
        "graphql",
        json_body={
            "query": query,
            "variables": {"query": request.query},
        },
    )
    rows = (
        data.get("data", {}).get("issueSearch", {}).get("nodes", [])
        if isinstance(data, dict)
        else []
    )
    return [
        r
        for item in rows[: request.limit]
        if isinstance(item, dict)
        if (
            r := _result(
                ConnectorId.LINEAR,
                item.get("identifier") or item.get("id"),
                item.get("title"),
                url=item.get("url"),
                snippet=item.get("description"),
                updated=item.get("updatedAt"),
                metadata={
                    "state": (item.get("state") or {}).get("name"),
                    "team": (item.get("team") or {}).get("name"),
                },
            )
        )
        is not None
    ]


async def _search_supabase(
    spec: ConnectorSpec,
    request: SearchRequest,
    client: httpx.AsyncClient,
    settings: Settings,
    vault: CredentialVault,
) -> list[SearchResult]:
    table = request.resource
    if not table or table not in settings.supabase_search_tables:
        raise ProviderError(
            "resource_not_allowlisted",
            "Supply a table explicitly allowlisted for Supabase search",
        )
    if not _SAFE_IDENTIFIER.fullmatch(table):
        raise ProviderError("invalid_resource", "Supabase table name is invalid")
    columns = settings.supabase_search_columns
    if not columns or any(not _SAFE_IDENTIFIER.fullmatch(c) for c in columns):
        raise ProviderError(
            "resource_not_configured", "No safe Supabase search columns are configured"
        )
    if not _SAFE_SUPABASE_QUERY.fullmatch(request.query):
        raise ProviderError(
            "unsafe_query",
            "Supabase search accepts letters, numbers, spaces, dots, underscores, plus and hyphen only",
        )
    escaped = request.query.replace("\\", "\\\\").replace('"', '\\"')
    filters = ",".join(f"{column}.ilike.*{escaped}*" for column in columns)
    params = {"select": ",".join(columns), "limit": request.limit, "or": f"({filters})"}
    data = await request_json(
        client, spec, settings, vault, "GET", f"rest/v1/{table}", params=params
    )
    if not isinstance(data, list):
        return []
    results: list[SearchResult] = []
    for index, row in enumerate(data[: request.limit]):
        if not isinstance(row, dict):
            continue
        identifier = row.get("id") or row.get("uuid") or f"row-{index}"
        title = next(
            (row.get(c) for c in ("title", "name") if isinstance(row.get(c), str)),
            identifier,
        )
        snippet = next(
            (
                row.get(c)
                for c in ("content", "description", "body")
                if isinstance(row.get(c), str)
            ),
            None,
        )
        result = _result(
            ConnectorId.SUPABASE,
            identifier,
            title,
            snippet=snippet,
            updated=row.get("updated_at"),
            metadata={"table": table},
        )
        if result:
            results.append(result)
    return results


async def _search_vercel(
    spec: ConnectorSpec,
    request: SearchRequest,
    client: httpx.AsyncClient,
    settings: Settings,
    vault: CredentialVault,
) -> list[SearchResult]:
    data = await request_json(
        client,
        spec,
        settings,
        vault,
        "GET",
        "v9/projects",
        params={
            "search": request.query,
            "limit": request.limit,
        },
    )
    rows = data.get("projects", []) if isinstance(data, dict) else []
    return [
        r
        for item in rows
        if isinstance(item, dict)
        if (
            r := _result(
                ConnectorId.VERCEL,
                item.get("id"),
                item.get("name"),
                url=f"https://vercel.com/{item.get('accountId', '')}/{item.get('name', '')}"
                if item.get("name")
                else None,
                snippet=item.get("framework"),
                updated=item.get("updatedAt"),
                metadata={
                    "framework": item.get("framework"),
                    "latest_deployment": (item.get("latestDeployments") or [{}])[0].get(
                        "url"
                    )
                    if item.get("latestDeployments")
                    else None,
                },
            )
        )
        is not None
    ]


async def search_one(
    request: SearchRequest,
    connector: ConnectorId,
    *,
    client: httpx.AsyncClient,
    settings: Settings,
    vault: CredentialVault,
) -> list[SearchResult]:
    spec = SPECS[connector]
    if connector in {
        ConnectorId.GMAIL,
        ConnectorId.GOOGLE_CALENDAR,
        ConnectorId.GOOGLE_DRIVE,
        ConnectorId.GOOGLE_DOCS,
        ConnectorId.GOOGLE_SHEETS,
        ConnectorId.GOOGLE_SLIDES,
    }:
        return await _search_google(connector, spec, request, client, settings, vault)
    if connector == ConnectorId.DROPBOX:
        data = await request_json(
            client,
            spec,
            settings,
            vault,
            "POST",
            "files/search_v2",
            json_body={
                "query": request.query,
                "options": {"max_results": request.limit, "filename_only": False},
            },
        )
        matches = data.get("matches", []) if isinstance(data, dict) else []
        return [
            r
            for item in matches
            if isinstance(item, dict)
            if (r := _dropbox_match(item, connector)) is not None
        ]
    if connector == ConnectorId.GITHUB:
        return await _search_github(spec, request, client, settings, vault)
    if connector == ConnectorId.LINEAR:
        return await _search_linear(spec, request, client, settings, vault)
    if connector == ConnectorId.NOTION:
        data = await request_json(
            client,
            spec,
            settings,
            vault,
            "POST",
            "search",
            json_body={
                "query": request.query,
                "page_size": request.limit,
                "sort": {"direction": "descending", "timestamp": "last_edited_time"},
            },
        )
        rows = data.get("results", []) if isinstance(data, dict) else []
        results: list[SearchResult] = []
        for item in rows[: request.limit]:
            if not isinstance(item, dict):
                continue
            props = item.get("properties", {})
            title = item.get("title")
            if not isinstance(title, list):
                title = None
            if title is None and isinstance(props, dict):
                for prop in props.values():
                    if isinstance(prop, dict) and isinstance(prop.get("title"), list):
                        title = prop.get("title")
                        break
            title_text = " ".join(
                t.get("plain_text", "") for t in (title or []) if isinstance(t, dict)
            )
            result = _result(
                connector,
                item.get("id"),
                title_text or item.get("object"),
                url=item.get("url"),
                updated=item.get("last_edited_time"),
                metadata={"object": item.get("object")},
            )
            if result:
                results.append(result)
        return results
    if connector == ConnectorId.SUPABASE:
        return await _search_supabase(spec, request, client, settings, vault)
    if connector == ConnectorId.VERCEL:
        return await _search_vercel(spec, request, client, settings, vault)
    raise ProviderError("unsupported", "Search is not implemented for this connector")


async def search_many(
    request: SearchRequest,
    *,
    settings: Settings,
    vault: CredentialVault,
    client: httpx.AsyncClient | None = None,
) -> SearchResponse:
    from .catalog import get_connector_info

    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0), follow_redirects=False
        )
    try:
        infos = {item.id: item for item in get_connector_info(vault, settings)}
        requested = list(
            dict.fromkeys(
                request.connectors
                if request.connectors is not None
                else list(ConnectorId)
            )
        )
        configured: list[ConnectorId] = []
        skipped: list[ConnectorId] = []
        for connector in requested:
            info = infos[connector]
            if (
                info.state.value == "unconfigured"
                or connector == ConnectorId.SUPABASE
                and (
                    not request.resource
                    or request.resource not in settings.supabase_search_tables
                    or not settings.supabase_search_columns
                )
                or info.state.value == "needs_resource_config"
                and connector != ConnectorId.SUPABASE
            ):
                skipped.append(connector)
            else:
                configured.append(connector)

        async def run_one(connector: ConnectorId):
            try:
                return (
                    connector,
                    await search_one(
                        request,
                        connector,
                        client=client,
                        settings=settings,
                        vault=vault,
                    ),
                    None,
                )
            except ProviderError as exc:
                return (
                    connector,
                    [],
                    SearchFailure(connector=connector, code=exc.code, message=str(exc)),
                )
            except VaultError:
                return (
                    connector,
                    [],
                    SearchFailure(
                        connector=connector,
                        code="keyring_unavailable",
                        message="The secure local keyring is unavailable",
                    ),
                )
            except Exception:  # noqa: BLE001 - sanitize unexpected provider failures at the API boundary.
                # Keep provider payloads and exception strings (which can contain
                # URLs or user data) out of API responses and logs.
                return (
                    connector,
                    [],
                    SearchFailure(
                        connector=connector,
                        code="connector_error",
                        message="Search failed; sensitive provider details were suppressed",
                    ),
                )

        outcomes = await asyncio.gather(*(run_one(c) for c in configured))
        response = SearchResponse(
            query=request.query, skipped=skipped, searched=configured
        )
        for connector, results, failure in outcomes:
            response.results.extend(results[: request.limit])
            if failure:
                response.failures.append(failure)
        return response
    finally:
        if own_client:
            await client.aclose()
