"""Bounded, read-only access to one explicitly identified provider resource."""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import replace
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

import httpx

from .catalog import SPECS
from .config import Settings
from .credentials import CredentialVault
from .http_api import ProviderError, request_json, request_raw
from .models import ConnectorId, ReadRequest, ReadResponse

_MAX_TEXT_BYTES = 512 * 1024
_MAX_READ_RESPONSE_BYTES = 2 * 1024 * 1024
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,256}$")
_SAFE_TABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".tsv",
    ".json",
    ".xml",
    ".yaml",
    ".yml",
    ".log",
    ".rst",
}
_TEXT_MIME_TYPES = {
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-yaml",
    "application/yaml",
    "application/csv",
    "text/csv",
}
_ALLOWED_READ_TYPES: dict[ConnectorId, frozenset[str]] = {
    ConnectorId.DROPBOX: frozenset({"file"}),
    ConnectorId.GITHUB: frozenset({"issue", "repository", "file"}),
    ConnectorId.GMAIL: frozenset({"message"}),
    ConnectorId.GOOGLE_CALENDAR: frozenset({"event"}),
    ConnectorId.GOOGLE_DRIVE: frozenset({"file"}),
    ConnectorId.GOOGLE_DOCS: frozenset({"document"}),
    ConnectorId.GOOGLE_SHEETS: frozenset({"spreadsheet"}),
    ConnectorId.GOOGLE_SLIDES: frozenset({"presentation"}),
    ConnectorId.LINEAR: frozenset({"issue"}),
    ConnectorId.NOTION: frozenset({"page"}),
    ConnectorId.SUPABASE: frozenset({"row"}),
    ConnectorId.VERCEL: frozenset({"project"}),
}


def _identifier(value: str, label: str = "resource ID") -> str:
    if value in {".", ".."} or not _SAFE_ID.fullmatch(value):
        raise ProviderError(
            "invalid_resource_id", f"{label} contains unsupported characters"
        )
    return quote(value, safe="")


def _read_text(content: bytes) -> str:
    if len(content) > _MAX_TEXT_BYTES:
        raise ProviderError(
            "response_too_large", "Text content exceeded the 512 KiB read limit"
        )
    if b"\x00" in content:
        raise ProviderError(
            "unsupported_media_type",
            "Binary content is not returned by the text-read endpoint",
        )
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ProviderError(
            "unsupported_media_type", "Content is not valid UTF-8 text"
        ) from exc


def _is_text_mime(mime_type: str) -> bool:
    normalized = mime_type.split(";", 1)[0].strip().lower()
    return normalized.startswith("text/") or normalized in _TEXT_MIME_TYPES


def _extract_google_text(value: Any) -> str:
    pieces: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            text_run = node.get("textRun")
            if isinstance(text_run, dict) and isinstance(text_run.get("content"), str):
                pieces.append(text_run["content"])
            for key, child in node.items():
                if key != "textRun":
                    visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return "".join(pieces)


def _github_parts(resource_id: str, *, file: bool = False) -> tuple[str, ...]:
    parts = resource_id.split("/")
    min_parts = 3 if file else 2
    if len(parts) < min_parts or any(not part or part in {".", ".."} for part in parts):
        raise ProviderError(
            "invalid_resource_id",
            "GitHub resources must use owner/repository[/path] format",
        )
    if any(
        any(ord(ch) < 32 or ord(ch) == 127 or ch == "\\" for ch in part)
        for part in parts
    ):
        raise ProviderError(
            "invalid_resource_id", "GitHub resource path contains an invalid character"
        )
    return tuple(parts)


def _dropbox_path(resource_id: str) -> str:
    if not resource_id.startswith(("/", "id:")):
        raise ProviderError(
            "invalid_resource_id",
            "Dropbox file must be identified by a path or file ID",
        )
    if "\\" in resource_id or "\x00" in resource_id:
        raise ProviderError(
            "invalid_resource_id", "Dropbox file path contains an invalid character"
        )
    if resource_id.startswith("/") and any(
        part in {".", ".."} for part in resource_id.split("/")
    ):
        raise ProviderError(
            "invalid_resource_id", "Dropbox file path contains a traversal component"
        )
    return resource_id


def _gmail_body(payload: Any) -> str:
    plain: list[str] = []
    html: list[str] = []
    total = 0

    def visit(part: Any) -> None:
        nonlocal total
        if not isinstance(part, dict):
            return
        body = part.get("body")
        data = body.get("data") if isinstance(body, dict) else None
        mime_type = str(part.get("mimeType", "")).lower()
        if isinstance(data, str) and data:
            try:
                padded = data + "=" * (-len(data) % 4)
                raw = base64.b64decode(padded, altchars=b"-_", validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ProviderError(
                    "invalid_provider_response", "Gmail body encoding was invalid"
                ) from exc
            total += len(raw)
            if total > _MAX_TEXT_BYTES:
                raise ProviderError(
                    "response_too_large", "Gmail text exceeded the 512 KiB read limit"
                )
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ProviderError(
                    "unsupported_media_type", "Gmail body is not UTF-8 text"
                ) from exc
            if mime_type == "text/plain":
                plain.append(text)
            elif mime_type == "text/html":
                html.append(text)
        for child in (
            part.get("parts", []) if isinstance(part.get("parts", []), list) else []
        ):
            visit(child)

    visit(payload)
    return "".join(plain if plain else html)


def _notion_block_text(block: dict[str, Any]) -> str:
    block_type = block.get("type")
    data = block.get(block_type, {}) if isinstance(block_type, str) else {}
    if not isinstance(data, dict):
        return ""
    rich_text = data.get("rich_text") or data.get("title") or data.get("caption") or []
    if not isinstance(rich_text, list):
        return ""
    pieces = []
    for item in rich_text:
        if not isinstance(item, dict):
            continue
        text = item.get("plain_text")
        if not isinstance(text, str):
            nested = item.get("text", {})
            text = nested.get("content", "") if isinstance(nested, dict) else ""
        if isinstance(text, str):
            pieces.append(text)
    return "".join(pieces)


async def _read_dropbox(
    request: ReadRequest,
    client: httpx.AsyncClient,
    settings: Settings,
    vault: CredentialVault,
) -> dict[str, Any]:
    path = _dropbox_path(request.resource_id)
    spec = SPECS[ConnectorId.DROPBOX]
    metadata = await request_json(
        client,
        spec,
        settings,
        vault,
        "POST",
        "files/get_metadata",
        json_body={"path": path},
    )
    if not isinstance(metadata, dict):
        raise ProviderError(
            "invalid_provider_response", "Dropbox metadata response was malformed"
        )
    name = str(metadata.get("name", ""))
    size = metadata.get("size")
    if isinstance(size, int) and size > _MAX_TEXT_BYTES:
        raise ProviderError(
            "response_too_large", "Dropbox file exceeds the 512 KiB text-read limit"
        )
    if PurePosixPath(name).suffix.lower() not in _TEXT_EXTENSIONS:
        raise ProviderError(
            "unsupported_media_type",
            "Dropbox read currently supports UTF-8 text files only",
        )
    raw = await request_raw(
        client,
        ConnectorId.DROPBOX,
        vault,
        "POST",
        "https://content.dropboxapi.com/2/files/download",
        allowed_hosts=frozenset({"content.dropboxapi.com"}),
        extra_headers={
            "Dropbox-API-Arg": json.dumps({"path": path}, separators=(",", ":"))
        },
        max_bytes=_MAX_TEXT_BYTES,
    )
    return {"metadata": metadata, "text": _read_text(raw.content)}


async def _read_github(
    request: ReadRequest,
    client: httpx.AsyncClient,
    settings: Settings,
    vault: CredentialVault,
) -> Any:
    spec = SPECS[ConnectorId.GITHUB]
    if request.resource_type == "repository":
        owner, repo = _github_parts(request.resource_id)
        return await request_json(
            client,
            spec,
            settings,
            vault,
            "GET",
            f"repos/{quote(owner, safe='')}/{quote(repo, safe='')}",
        )
    if request.resource_type == "issue":
        match = re.fullmatch(r"([^/]+)/([^/#]+)#([1-9][0-9]{0,8})", request.resource_id)
        if not match:
            raise ProviderError(
                "invalid_resource_id",
                "GitHub issue ID must use owner/repository#number format",
            )
        owner, repo, number = match.groups()
        _github_parts(f"{owner}/{repo}")
        return await request_json(
            client,
            spec,
            settings,
            vault,
            "GET",
            f"repos/{quote(owner, safe='')}/{quote(repo, safe='')}/issues/{number}",
        )
    parts = _github_parts(request.resource_id, file=True)
    owner, repo, *file_path = parts
    encoded_path = "/".join(quote(part, safe="") for part in file_path)
    response = await request_json(
        client,
        spec,
        settings,
        vault,
        "GET",
        f"repos/{quote(owner, safe='')}/{quote(repo, safe='')}/contents/{encoded_path}",
    )
    if (
        not isinstance(response, dict)
        or response.get("encoding") != "base64"
        or not isinstance(response.get("content"), str)
    ):
        raise ProviderError(
            "unsupported_media_type",
            "GitHub file is not available as a small text file",
        )
    size = response.get("size")
    if isinstance(size, int) and size > _MAX_TEXT_BYTES:
        raise ProviderError(
            "response_too_large", "GitHub file exceeds the 512 KiB text-read limit"
        )
    try:
        raw = base64.b64decode(
            response["content"].replace("\n", "").replace("\r", ""), validate=True
        )
    except (ValueError, binascii.Error) as exc:
        raise ProviderError(
            "invalid_provider_response", "GitHub file encoding was invalid"
        ) from exc
    return {
        "name": response.get("name"),
        "path": response.get("path"),
        "text": _read_text(raw),
    }


async def _read_gmail(
    request: ReadRequest,
    client: httpx.AsyncClient,
    settings: Settings,
    vault: CredentialVault,
) -> dict[str, Any]:
    message_id = _identifier(request.resource_id, "Gmail message ID")
    data = await request_json(
        client,
        SPECS[ConnectorId.GMAIL],
        settings,
        vault,
        "GET",
        f"messages/{message_id}",
        params={
            "format": "full",
            "fields": "id,threadId,labelIds,snippet,internalDate,payload",
        },
    )
    if not isinstance(data, dict):
        raise ProviderError(
            "invalid_provider_response", "Gmail message response was malformed"
        )
    payload = data.get("payload", {})
    header_rows = payload.get("headers", []) if isinstance(payload, dict) else []
    headers = {
        str(header.get("name", "")).lower(): header.get("value")
        for header in header_rows
        if isinstance(header, dict) and isinstance(header.get("value"), str)
    }
    return {
        "id": data.get("id"),
        "thread_id": data.get("threadId"),
        "labels": data.get("labelIds", []),
        "snippet": data.get("snippet"),
        "internal_date": data.get("internalDate"),
        "headers": {
            key: headers[key]
            for key in ("from", "to", "cc", "subject", "date")
            if key in headers
        },
        "body_text": _gmail_body(payload),
    }


async def _read_google_calendar(
    request: ReadRequest,
    client: httpx.AsyncClient,
    settings: Settings,
    vault: CredentialVault,
) -> Any:
    event_id = _identifier(request.resource_id, "Google Calendar event ID")
    return await request_json(
        client,
        SPECS[ConnectorId.GOOGLE_CALENDAR],
        settings,
        vault,
        "GET",
        f"calendars/primary/events/{event_id}",
    )


async def _read_google_drive(
    request: ReadRequest,
    client: httpx.AsyncClient,
    settings: Settings,
    vault: CredentialVault,
) -> dict[str, Any]:
    file_id = _identifier(request.resource_id, "Google Drive file ID")
    spec = SPECS[ConnectorId.GOOGLE_DRIVE]
    metadata = await request_json(
        client,
        spec,
        settings,
        vault,
        "GET",
        f"files/{file_id}",
        params={"fields": "id,name,mimeType,size,modifiedTime,webViewLink,description"},
    )
    if not isinstance(metadata, dict):
        raise ProviderError(
            "invalid_provider_response", "Google Drive metadata response was malformed"
        )
    mime_type = str(metadata.get("mimeType", ""))
    if mime_type.startswith("application/vnd.google-apps."):
        return {
            "metadata": metadata,
            "text": None,
            "note": "Use the Docs, Sheets, or Slides reader for Google-native content.",
        }
    if not _is_text_mime(mime_type):
        return {
            "metadata": metadata,
            "text": None,
            "note": "Binary content is not downloaded by the read-only text endpoint.",
        }
    size = metadata.get("size")
    if isinstance(size, str) and size.isdigit() and int(size) > _MAX_TEXT_BYTES:
        raise ProviderError(
            "response_too_large",
            "Google Drive file exceeds the 512 KiB text-read limit",
        )
    raw = await request_raw(
        client,
        ConnectorId.GOOGLE_DRIVE,
        vault,
        "GET",
        f"https://www.googleapis.com/drive/v3/files/{file_id}",
        allowed_hosts=frozenset({"www.googleapis.com"}),
        params={"alt": "media"},
        max_bytes=_MAX_TEXT_BYTES,
    )
    return {"metadata": metadata, "text": _read_text(raw.content)}


async def _read_google_docs(
    request: ReadRequest,
    client: httpx.AsyncClient,
    settings: Settings,
    vault: CredentialVault,
) -> dict[str, Any]:
    document_id = _identifier(request.resource_id, "Google Docs document ID")
    spec = replace(
        SPECS[ConnectorId.GOOGLE_DOCS], api_base="https://docs.googleapis.com/v1"
    )
    document = await request_json(
        client, spec, settings, vault, "GET", f"documents/{document_id}"
    )
    if not isinstance(document, dict):
        raise ProviderError(
            "invalid_provider_response", "Google Docs document response was malformed"
        )
    return {
        "document_id": document.get("documentId"),
        "title": document.get("title"),
        "text": _extract_google_text(document.get("body", {})),
    }


async def _read_google_sheets(
    request: ReadRequest,
    client: httpx.AsyncClient,
    settings: Settings,
    vault: CredentialVault,
) -> Any:
    spreadsheet_id = _identifier(request.resource_id, "Google Sheets spreadsheet ID")
    spec = replace(
        SPECS[ConnectorId.GOOGLE_SHEETS], api_base="https://sheets.googleapis.com/v4"
    )
    data = await request_json(
        client,
        spec,
        settings,
        vault,
        "GET",
        f"spreadsheets/{spreadsheet_id}/values/A1:Z100",
        params={"valueRenderOption": "FORMATTED_VALUE", "majorDimension": "ROWS"},
    )
    return data


async def _read_google_slides(
    request: ReadRequest,
    client: httpx.AsyncClient,
    settings: Settings,
    vault: CredentialVault,
) -> dict[str, Any]:
    presentation_id = _identifier(request.resource_id, "Google Slides presentation ID")
    spec = replace(
        SPECS[ConnectorId.GOOGLE_SLIDES], api_base="https://slides.googleapis.com/v1"
    )
    presentation = await request_json(
        client, spec, settings, vault, "GET", f"presentations/{presentation_id}"
    )
    if not isinstance(presentation, dict):
        raise ProviderError(
            "invalid_provider_response",
            "Google Slides presentation response was malformed",
        )
    slide_rows = presentation.get("slides", [])
    if not isinstance(slide_rows, list):
        raise ProviderError(
            "invalid_provider_response", "Google Slides slide list was malformed"
        )
    slides = []
    for index, slide in enumerate(slide_rows, start=1):
        if isinstance(slide, dict):
            slides.append(
                {
                    "slide": index,
                    "object_id": slide.get("objectId"),
                    "text": _extract_google_text(slide),
                }
            )
    return {
        "presentation_id": presentation.get("presentationId"),
        "title": presentation.get("title"),
        "slides": slides,
    }


async def _read_linear(
    request: ReadRequest,
    client: httpx.AsyncClient,
    settings: Settings,
    vault: CredentialVault,
) -> Any:
    issue_id = request.resource_id
    if not _SAFE_ID.fullmatch(issue_id):
        raise ProviderError(
            "invalid_resource_id",
            "Linear issue ID or identifier contains unsupported characters",
        )
    query = """query ReadIssue($id: String!) {
      issue(id: $id) {
        id identifier title description url createdAt updatedAt
        state { name } assignee { name } team { name }
      }
    }"""
    data = await request_json(
        client,
        SPECS[ConnectorId.LINEAR],
        settings,
        vault,
        "POST",
        "graphql",
        json_body={"query": query, "variables": {"id": issue_id}},
    )
    if isinstance(data, dict) and data.get("errors"):
        raise ProviderError("provider_query_error", "Linear could not read this issue")
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        raise ProviderError(
            "invalid_provider_response", "Linear issue response was malformed"
        )
    return data["data"].get("issue")


async def _read_notion(
    request: ReadRequest,
    client: httpx.AsyncClient,
    settings: Settings,
    vault: CredentialVault,
) -> dict[str, Any]:
    page_id = _identifier(request.resource_id, "Notion page ID")
    spec = SPECS[ConnectorId.NOTION]
    page = await request_json(client, spec, settings, vault, "GET", f"pages/{page_id}")
    if not isinstance(page, dict):
        raise ProviderError(
            "invalid_provider_response", "Notion page response was malformed"
        )
    blocks: list[dict[str, Any]] = []
    cursor: str | None = None
    has_more = False
    for _ in range(5):
        params: dict[str, Any] = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        response = await request_json(
            client,
            spec,
            settings,
            vault,
            "GET",
            f"blocks/{page_id}/children",
            params=params,
        )
        if not isinstance(response, dict):
            raise ProviderError(
                "invalid_provider_response", "Notion block response was malformed"
            )
        block_rows = response.get("results", [])
        if not isinstance(block_rows, list):
            raise ProviderError(
                "invalid_provider_response", "Notion block list was malformed"
            )
        for block in block_rows:
            if isinstance(block, dict):
                blocks.append(
                    {
                        "id": block.get("id"),
                        "type": block.get("type"),
                        "text": _notion_block_text(block),
                        "has_children": bool(block.get("has_children", False)),
                    }
                )
        has_more = bool(response.get("has_more", False))
        next_cursor = response.get("next_cursor")
        if has_more and not isinstance(next_cursor, str):
            raise ProviderError(
                "invalid_provider_response", "Notion pagination cursor was malformed"
            )
        cursor = next_cursor if has_more else None
        if not cursor:
            break
    return {
        "id": page.get("id"),
        "url": page.get("url"),
        "last_edited_time": page.get("last_edited_time"),
        "properties": page.get("properties", {}),
        "blocks": blocks,
        "has_more_blocks": has_more and bool(cursor),
    }


async def _read_supabase(
    request: ReadRequest,
    client: httpx.AsyncClient,
    settings: Settings,
    vault: CredentialVault,
) -> Any:
    table = request.resource
    if not table or table not in settings.supabase_search_tables:
        raise ProviderError(
            "resource_not_allowlisted",
            "Supply a table explicitly allowlisted for Supabase reads",
        )
    if not _SAFE_TABLE.fullmatch(table):
        raise ProviderError("invalid_resource", "Supabase table name is invalid")
    columns = settings.supabase_search_columns
    if not columns or any(not _SAFE_TABLE.fullmatch(column) for column in columns):
        raise ProviderError(
            "resource_not_configured", "No safe Supabase read columns are configured"
        )
    if not _SAFE_ID.fullmatch(request.resource_id):
        raise ProviderError(
            "invalid_resource_id", "Supabase row ID contains unsupported characters"
        )
    data = await request_json(
        client,
        SPECS[ConnectorId.SUPABASE],
        settings,
        vault,
        "GET",
        f"rest/v1/{table}",
        params={
            "select": ",".join(columns),
            "id": f"eq.{request.resource_id}",
            "limit": 1,
        },
    )
    if not isinstance(data, list):
        raise ProviderError(
            "invalid_provider_response", "Supabase row response was malformed"
        )
    return data[0] if data else None


async def _read_vercel(
    request: ReadRequest,
    client: httpx.AsyncClient,
    settings: Settings,
    vault: CredentialVault,
) -> Any:
    project_id = _identifier(request.resource_id, "Vercel project ID")
    return await request_json(
        client,
        SPECS[ConnectorId.VERCEL],
        settings,
        vault,
        "GET",
        f"v9/projects/{project_id}",
    )


async def read_one(
    request: ReadRequest,
    *,
    client: httpx.AsyncClient,
    settings: Settings,
    vault: CredentialVault,
) -> ReadResponse:
    """Read a single resource using a fixed provider-native route and safe ID encoding."""
    if request.resource_type not in _ALLOWED_READ_TYPES[request.connector]:
        raise ProviderError(
            "unsupported_resource_type",
            "This resource type is not supported by the selected connector",
        )
    if request.connector != ConnectorId.SUPABASE and request.resource is not None:
        raise ProviderError(
            "invalid_resource",
            "The resource field is only used for Supabase table reads",
        )

    dispatch = {
        ConnectorId.DROPBOX: _read_dropbox,
        ConnectorId.GITHUB: _read_github,
        ConnectorId.GMAIL: _read_gmail,
        ConnectorId.GOOGLE_CALENDAR: _read_google_calendar,
        ConnectorId.GOOGLE_DRIVE: _read_google_drive,
        ConnectorId.GOOGLE_DOCS: _read_google_docs,
        ConnectorId.GOOGLE_SHEETS: _read_google_sheets,
        ConnectorId.GOOGLE_SLIDES: _read_google_slides,
        ConnectorId.LINEAR: _read_linear,
        ConnectorId.NOTION: _read_notion,
        ConnectorId.SUPABASE: _read_supabase,
        ConnectorId.VERCEL: _read_vercel,
    }
    data = await dispatch[request.connector](request, client, settings, vault)
    try:
        encoded_size = len(
            json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise ProviderError(
            "invalid_provider_response",
            "Provider resource could not be normalized safely",
        ) from exc
    if encoded_size > _MAX_READ_RESPONSE_BYTES:
        raise ProviderError(
            "response_too_large", "Read result exceeded the 2 MiB response limit"
        )
    return ReadResponse(
        connector=request.connector,
        resource_type=request.resource_type,
        resource_id=request.resource_id,
        data=data,
    )
