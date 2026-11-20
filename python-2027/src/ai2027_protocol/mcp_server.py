"""Minimal, read-only Model Context Protocol server over newline-delimited stdio.

This deliberately exposes one bounded workspace-inspection tool rather than
letting an MCP client reach arbitrary shell, model, filesystem-write, or connector
operations. Results carry an HMAC-SHA256 attestation using a secret supplied by
the operator. MCP itself does not define result signing; the HMAC is an AI-2027
extension and requires the client to hold the same key to verify it.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeGuard

from .mcp_attestation import McpConfigurationError, sign_mcp_result, signing_key_from_env
from .mcp_attestation import canonical_json as _canonical_json
from .mcp_contracts import JsonRpcId, McpTool, McpToolError, ProgressReporter, SendMessage
from .mcp_inspector import (
    MAX_CONCURRENT_FILE_INSPECTIONS,
    MAX_DESCRIPTION_CHARS,
    MAX_INSPECT_FILE_BYTES,
    _build_tools,
)
from .security import SecurityValidationError, redact_secrets

MCP_PROTOCOL_VERSION = "2025-06-18"
MCP_SERVER_NAME = "ai-2027"
MCP_SERVER_VERSION = "0.1.0"
MAX_RESULT_BYTES = 32 * 1024
MAX_PROGRESS_UPDATES = 32
MAX_PENDING_CALLS = 16
MCP_TOOL_TIMEOUT_S = 15.0

__all__ = [
    "MAX_CONCURRENT_FILE_INSPECTIONS",
    "MAX_DESCRIPTION_CHARS",
    "MAX_INSPECT_FILE_BYTES",
    "McpConfigurationError",
    "McpProtocolServer",
    "McpTool",
    "McpToolError",
    "MAX_PENDING_CALLS",
    "MAX_PROGRESS_UPDATES",
    "MAX_RESULT_BYTES",
    "MCP_PROTOCOL_VERSION",
    "MCP_SERVER_NAME",
    "MCP_SERVER_VERSION",
    "MCP_TOOL_TIMEOUT_S",
    "ProgressReporter",
    "SendMessage",
]


@dataclass
class _PendingCall:
    """Cancellation state for one in-flight tools/call request."""

    request_id: JsonRpcId
    task: asyncio.Task[None] | None = None
    cancelled: bool = False


class McpProtocolServer:
    """MCP tools subset over stdio, with cancellation, progress, and signed results."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        signing_key: bytes,
        tools: tuple[McpTool, ...] | None = None,
    ) -> None:
        """Create a server rooted at one workspace and guarded by a 256-bit HMAC key."""
        root = Path(workspace_root).expanduser().resolve()
        if not root.is_dir():
            raise McpConfigurationError("MCP workspace root must be an existing directory.")
        if len(signing_key) != 32:
            raise McpConfigurationError("MCP result signing key must be exactly 32 bytes.")
        registered = _build_tools(root) if tools is None else tools
        by_name = {tool.name: tool for tool in registered}
        if len(by_name) != len(registered):
            raise McpConfigurationError("MCP tool names must be unique.")
        self.workspace_root = root
        self.signing_key = signing_key
        self.tools = by_name
        self.initialized = False
        self.ready = False
        self._pending: dict[JsonRpcId, _PendingCall] = {}

    @classmethod
    def from_environment(cls, *, workspace_root: Path) -> McpProtocolServer:
        """Build the server with the operator-managed HMAC secret from its environment."""
        return cls(workspace_root=workspace_root, signing_key=signing_key_from_env())

    async def handle_message(self, message: object, send: SendMessage) -> None:
        """Validate and dispatch one JSON-RPC request or notification."""
        if not isinstance(message, dict):
            await self._send_error(send, None, -32600, "Invalid Request")
            return
        if message.get("jsonrpc") != "2.0":
            request_id = message.get("id")
            await self._send_error(
                send, request_id if self._valid_id(request_id) else None, -32600, "Invalid Request"
            )
            return
        method = message.get("method")
        if not isinstance(method, str):
            # A response from the client is harmless; this server never initiates requests.
            return
        params = message.get("params", {})
        if not isinstance(params, dict):
            if "id" in message:
                request_id = message.get("id")
                await self._send_error(
                    send,
                    request_id if self._valid_id(request_id) else None,
                    -32602,
                    "Params must be an object",
                )
            return
        if "id" not in message:
            await self._handle_notification(method, params, send)
            return
        request_id = message.get("id")
        if not self._valid_id(request_id):
            await self._send_error(send, None, -32600, "Invalid Request id")
            return
        await self._handle_request(method, params, request_id, send)

    async def _handle_request(
        self, method: str, params: dict[str, Any], request_id: JsonRpcId, send: SendMessage
    ) -> None:
        if method == "initialize":
            if self.initialized:
                await self._send_error(send, request_id, -32600, "Already initialized")
                return
            requested_version = params.get("protocolVersion")
            if not isinstance(requested_version, str):
                await self._send_error(send, request_id, -32602, "protocolVersion is required")
                return
            selected_version = (
                requested_version
                if requested_version in {"2025-06-18", "2025-03-26", "2024-11-05"}
                else MCP_PROTOCOL_VERSION
            )
            self.initialized = True
            await send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": selected_version,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION},
                        "instructions": (
                            "Read-only workspace inspection only. Tool results include an "
                            "AI-2027 HMAC-SHA256 attestation using the configured shared key."
                        ),
                    },
                }
            )
            return

        if method == "ping":
            await send({"jsonrpc": "2.0", "id": request_id, "result": {}})
            return

        if method in {"tools/list", "tools/call"} and not self.ready:
            await self._send_error(send, request_id, -32002, "Server not initialized")
            return

        if method == "tools/list":
            await send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"tools": [tool.descriptor() for tool in self.tools.values()]},
                }
            )
            return

        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(name, str) or not isinstance(arguments, dict):
                await self._send_error(
                    send, request_id, -32602, "tools/call needs name and object arguments"
                )
                return
            if request_id in self._pending:
                await self._send_error(send, request_id, -32600, "Duplicate in-flight request id")
                return
            if len(self._pending) >= MAX_PENDING_CALLS:
                await self._send_error(send, request_id, -32000, "Too many in-flight tool requests")
                return
            pending = _PendingCall(request_id=request_id)
            self._pending[request_id] = pending
            pending.task = asyncio.create_task(
                self._execute_tool_call(name, arguments, params, pending, send),
                name=f"mcp-tool-{name}",
            )
            pending.task.add_done_callback(lambda _task: self._forget_pending(pending))
            return

        await self._send_error(send, request_id, -32601, "Method not found")

    async def _handle_notification(
        self, method: str, params: dict[str, Any], send: SendMessage
    ) -> None:
        if method == "notifications/initialized":
            self.ready = self.initialized
            return
        if method != "notifications/cancelled":
            return
        request_id = params.get("requestId")
        if not self._valid_id(request_id):
            return
        pending = self._pending.get(request_id)
        if pending is None or pending.task is None or pending.task.done():
            return
        pending.cancelled = True
        pending.task.cancel()
        # Answer immediately, including the race where cancellation arrives before
        # the newly-created coroutine gets its first timeslice.
        await self._send_error(send, request_id, -32800, "Request cancelled")

    def _forget_pending(self, pending: _PendingCall) -> None:
        """Remove a completed call only if this generation still owns its ID."""
        if self._pending.get(pending.request_id) is pending:
            self._pending.pop(pending.request_id, None)

    async def _execute_tool_call(
        self,
        name: str,
        arguments: dict[str, Any],
        params: dict[str, Any],
        pending: _PendingCall,
        send: SendMessage,
    ) -> None:
        request_id = pending.request_id
        raw_request_path = arguments.get("path")
        request_path = raw_request_path if isinstance(raw_request_path, str) else None
        token = self._progress_token(params)
        progress_count = 0
        last_progress = 0.0

        async def report_progress(progress: float, message: str) -> None:
            nonlocal progress_count, last_progress
            if token is None or pending.cancelled or progress_count >= MAX_PROGRESS_UPDATES:
                return
            if not isinstance(progress, int | float) or isinstance(progress, bool):
                return
            try:
                numeric_progress = float(progress)
            except OverflowError:
                return
            if not math.isfinite(numeric_progress):
                return
            bounded = max(last_progress, min(1.0, max(0.0, numeric_progress)))
            last_progress = bounded
            progress_count += 1
            await send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/progress",
                    "params": {
                        "progressToken": token,
                        "progress": bounded,
                        "total": 1.0,
                        "message": redact_secrets(str(message))[:160],
                    },
                }
            )

        try:
            if token is not None:
                await report_progress(0.0, "Tool call started")
            tool = self.tools.get(name)
            if tool is None:
                result = {"ok": False, "error": f"Unknown tool: {name[:128]}"}
                is_error = True
            else:
                result = await asyncio.wait_for(
                    tool.handler(arguments, report_progress), timeout=MCP_TOOL_TIMEOUT_S
                )
                is_error = False
            if pending.cancelled:
                return
            envelope = sign_mcp_result(
                tool=name,
                request_id=request_id,
                result=result,
                key=self.signing_key,
                request_path=request_path,
            )
            text = _canonical_json(envelope).decode("utf-8")
            if len(text.encode("utf-8")) > MAX_RESULT_BYTES:
                is_error = True
                envelope = sign_mcp_result(
                    tool=name,
                    request_id=request_id,
                    result={"ok": False, "error": "Signed MCP result exceeded its byte limit."},
                    key=self.signing_key,
                    request_path=request_path,
                )
                text = _canonical_json(envelope).decode("utf-8")
            await send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"content": [{"type": "text", "text": text}], "isError": is_error},
                }
            )
        except TimeoutError:
            envelope = sign_mcp_result(
                tool=name,
                request_id=request_id,
                result={
                    "ok": False,
                    "error": f"Tool timed out after {MCP_TOOL_TIMEOUT_S:g} seconds.",
                },
                key=self.signing_key,
                request_path=request_path,
            )
            await send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": _canonical_json(envelope).decode()}],
                        "isError": True,
                    },
                }
            )
        except asyncio.CancelledError:
            if not pending.cancelled:
                await self._send_error(send, request_id, -32800, "Request cancelled")
        except (McpToolError, SecurityValidationError, FileNotFoundError) as exc:
            safe_error = redact_secrets(str(exc))[:1000] or "Tool request was refused."
            envelope = sign_mcp_result(
                tool=name,
                request_id=request_id,
                result={"ok": False, "error": safe_error},
                key=self.signing_key,
                request_path=request_path,
            )
            await send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": _canonical_json(envelope).decode()}],
                        "isError": True,
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001 - MCP boundary must fail soft and redact.
            safe_error = redact_secrets(f"Tool failed ({type(exc).__name__}).")
            envelope = sign_mcp_result(
                tool=name,
                request_id=request_id,
                result={"ok": False, "error": safe_error},
                key=self.signing_key,
                request_path=request_path,
            )
            await send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": _canonical_json(envelope).decode()}],
                        "isError": True,
                    },
                }
            )
        finally:
            self._forget_pending(pending)

    async def close(self) -> None:
        """Cancel outstanding requests when the stdio transport closes."""
        pending_calls = tuple(self._pending.values())
        self._pending.clear()
        tasks: list[asyncio.Task[None]] = []
        for pending in pending_calls:
            pending.cancelled = True
            if pending.task is not None and not pending.task.done():
                pending.task.cancel()
                tasks.append(pending.task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    async def _send_error(
        send: SendMessage, request_id: JsonRpcId | None, code: int, message: str
    ) -> None:
        await send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            }
        )

    @staticmethod
    def _valid_id(value: object) -> TypeGuard[JsonRpcId]:
        """Accept only JSON-RPC string or integer IDs, never booleans or null."""
        return isinstance(value, str | int) and not isinstance(value, bool)

    @staticmethod
    def _progress_token(params: Mapping[str, Any]) -> str | int | float | None:
        """Return a valid MCP string/number progress token from request metadata."""
        meta = params.get("_meta")
        if not isinstance(meta, dict):
            return None
        token = meta.get("progressToken")
        if isinstance(token, str):
            return token
        if isinstance(token, int) and not isinstance(token, bool):
            return token
        if isinstance(token, float) and math.isfinite(token):
            return token
        return None
