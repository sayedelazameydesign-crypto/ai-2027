"""Newline-delimited stdio transport and executable entry support for MCP."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from .mcp_attestation import McpConfigurationError
from .mcp_attestation import canonical_json as _canonical_json
from .mcp_server import McpProtocolServer

MAX_MCP_LINE_CHARS = 64 * 1024


async def serve_stdio(
    server: McpProtocolServer,
    *,
    reader: TextIO | None = None,
    writer: TextIO | None = None,
) -> None:
    """Run the MCP newline-delimited stdio transport; stdout is protocol-only."""
    source = sys.stdin if reader is None else reader
    destination = sys.stdout if writer is None else writer
    output_lock = asyncio.Lock()

    async def send(message: dict[str, Any]) -> None:
        line = _canonical_json(message).decode("utf-8") + "\n"
        async with output_lock:
            destination.write(line)
            destination.flush()

    try:
        while True:
            line = await asyncio.to_thread(source.readline, MAX_MCP_LINE_CHARS + 1)
            if line == "":
                break
            too_long = len(line) > MAX_MCP_LINE_CHARS
            while too_long and line and not line.endswith("\n"):
                line = await asyncio.to_thread(source.readline, 4096)
            if too_long:
                await McpProtocolServer._send_error(
                    send,
                    None,
                    -32700,
                    f"Message exceeds the {MAX_MCP_LINE_CHARS}-character limit.",
                )
                continue
            if not line.strip():
                continue
            try:
                message = json.loads(line, parse_constant=_reject_json_constant)
            except (json.JSONDecodeError, ValueError, RecursionError):
                await McpProtocolServer._send_error(send, None, -32700, "Parse error")
                continue
            await server.handle_message(message, send)
    finally:
        await server.close()


def _reject_json_constant(value: str) -> None:
    """Reject Python json's non-standard NaN/Infinity extensions."""
    raise ValueError(f"Invalid JSON constant: {value}")


def run_stdio_from_environment(workspace_root: Path) -> int:
    """Start the configured MCP server without printing banners on stdout."""
    try:
        server = McpProtocolServer.from_environment(workspace_root=workspace_root)
        asyncio.run(serve_stdio(server))
    except McpConfigurationError as exc:
        print(f"MCP: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover - terminal-only shutdown path
        return 130
    return 0
