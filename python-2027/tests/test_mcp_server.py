from __future__ import annotations

import asyncio
import io
import json
import os
import secrets
import sys
import threading
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from ai2027_protocol import mcp_inspector as mcp_inspector_module
from ai2027_protocol import mcp_server as mcp_server_module
from ai2027_protocol import mcp_stdio as mcp_stdio_module
from ai2027_protocol.cli import main
from ai2027_protocol.mcp_attestation import verify_signed_mcp_result
from ai2027_protocol.mcp_server import (
    McpProtocolServer,
    McpTool,
    ProgressReporter,
    SendMessage,
)

_KEY = secrets.token_bytes(32)
_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"


def _initialize(request_id: int = 1) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }


def _sender(messages: list[dict[str, Any]]) -> SendMessage:
    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    return send


def test_cli_fails_closed_without_signing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("AI2027_MCP_SIGNING_KEY", raising=False)
    assert main(["--root", str(tmp_path), "mcp"]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "AI2027_MCP_SIGNING_KEY" in output.err


async def test_stdio_protocol_discovery_call_progress_and_signed_inspection(
    tmp_path: Path,
) -> None:
    (tmp_path / "hello.txt").write_text("AI-2027 MCP evidence.\n", encoding="utf-8")
    server = McpProtocolServer(workspace_root=tmp_path, signing_key=_KEY)
    messages: list[dict[str, Any]] = []
    send = _sender(messages)

    await server.handle_message(
        {"jsonrpc": "2.0", "id": 0, "method": "tools/list", "params": {}}, send
    )
    assert messages[-1]["error"]["code"] == -32002

    await server.handle_message(_initialize(), send)
    assert messages[-1]["result"]["protocolVersion"] == "2025-06-18"
    await server.handle_message(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}}, send
    )
    assert messages[-1]["error"]["code"] == -32002
    await server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}, send)
    await server.handle_message(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, send
    )
    listed = messages[-1]["result"]["tools"]
    assert [tool["name"] for tool in listed] == ["ai2027.inspect_file"]
    assert listed[0]["inputSchema"]["required"] == ["path"]
    assert listed[0]["annotations"]["readOnlyHint"] is True
    assert listed[0]["annotations"]["destructiveHint"] is False

    await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "ai2027.inspect_file",
                "arguments": {"path": "hello.txt"},
                "_meta": {"progressToken": "inspect-3"},
            },
        },
        send,
    )
    for _ in range(100):
        if any(message.get("id") == 3 for message in messages):
            break
        await asyncio.sleep(0.01)
    response = next(message for message in messages if message.get("id") == 3)
    assert response["result"]["isError"] is False
    progress = [m for m in messages if m.get("method") == "notifications/progress"]
    assert len(progress) >= 3
    assert [m["params"]["progress"] for m in progress] == sorted(
        m["params"]["progress"] for m in progress
    )
    envelope = json.loads(response["result"]["content"][0]["text"])
    assert verify_signed_mcp_result(
        envelope,
        _KEY,
        expected_tool="ai2027.inspect_file",
        expected_request_id=3,
        expected_path="hello.txt",
    )
    assert envelope["tool"] == "ai2027.inspect_file"
    assert envelope["request_id"] == 3
    assert envelope["result"]["verification"]["sha256"]
    assert envelope["result"]["path"] == "hello.txt"
    await server.close()


async def test_mcp_refuses_traversal_and_secret_paths_with_signed_errors(tmp_path: Path) -> None:
    (tmp_path / ".env.local").write_text("DO_NOT_READ=secret", encoding="utf-8")
    (tmp_path / "secret-link").symlink_to(tmp_path / ".env.local")
    private = tmp_path / "private"
    private.mkdir()
    (private / ".env.production").write_text("DO_NOT_READ=secret", encoding="utf-8")
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("DO_NOT_READ=credential", encoding="utf-8")
    (tmp_path / "large.bin").write_bytes(b"x" * (mcp_server_module.MAX_INSPECT_FILE_BYTES + 1))
    server = McpProtocolServer(workspace_root=tmp_path, signing_key=_KEY)
    messages: list[dict[str, Any]] = []
    send = _sender(messages)
    await server.handle_message(_initialize(), send)
    await server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}, send)

    paths = (
        "../outside.txt",
        ".env.local",
        "secret-link",
        "private/.env.production",
        ".git/config",
        "large.bin",
    )
    for request_id, path in enumerate(paths, start=2):
        await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": "ai2027.inspect_file", "arguments": {"path": path}},
            },
            send,
        )
        for _ in range(100):
            if any(message.get("id") == request_id for message in messages):
                break
            await asyncio.sleep(0.01)
        response = next(message for message in messages if message.get("id") == request_id)
        assert response["result"]["isError"] is True
        envelope = json.loads(response["result"]["content"][0]["text"])
        assert verify_signed_mcp_result(
            envelope,
            _KEY,
            expected_tool="ai2027.inspect_file",
            expected_request_id=request_id,
            expected_path=path,
        )
        assert envelope["result"]["ok"] is False
        assert "DO_NOT_READ" not in envelope["result"].get("error", "")
    await server.close()


def test_mcp_description_omits_file_text_and_identifiers(tmp_path: Path) -> None:
    secret = "nvapi-" + "A" * 24
    marker = "MCP_REDTEAM_CONTENT_SENTINEL_DO_NOT_DISCLOSE_83917"
    (tmp_path / "notes.md").write_text(
        f"# Provider key {secret}\n\n## {marker}\n", encoding="utf-8"
    )
    result = mcp_inspector_module._inspect_file_sync(tmp_path, "notes.md")
    rendered = json.dumps(result, ensure_ascii=False)
    assert secret not in rendered
    assert marker not in rendered
    assert "Free-form text" in result["description"]


def test_mcp_zip_inspection_does_not_inflate_archive_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path = tmp_path / "compressed.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("repetitive.txt", b"0" * (512 * 1024))

    def unexpected_deep_verification(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("MCP ZIP inspection must not decompress archive members")

    monkeypatch.setattr(mcp_inspector_module, "verify_file", unexpected_deep_verification)
    result = mcp_inspector_module._inspect_file_sync(tmp_path, "compressed.zip")
    verification = result["verification"]
    assert verification["ok"] is False
    assert len(verification["sha256"]) == 64
    assert verification["checks"][1]["name"] == "archive member integrity"
    assert verification["checks"][1]["passed"] is False
    assert "not inflated" in verification["notes"][0]


async def test_mcp_cancellation_returns_error_and_emits_progress(tmp_path: Path) -> None:
    started = asyncio.Event()
    never = asyncio.Event()

    async def slow_tool(
        arguments: Mapping[str, Any], report_progress: ProgressReporter
    ) -> dict[str, Any]:
        started.set()
        await report_progress(0.25, "waiting safely")
        await never.wait()
        return {"ok": True}

    tool = McpTool(
        name="test.slow",
        description="Test cancellation.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=slow_tool,
    )
    server = McpProtocolServer(workspace_root=tmp_path, signing_key=_KEY, tools=(tool,))
    messages: list[dict[str, Any]] = []
    send = _sender(messages)
    await server.handle_message(_initialize(), send)
    await server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}, send)
    await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": "cancel-me",
            "method": "tools/call",
            "params": {
                "name": "test.slow",
                "arguments": {},
                "_meta": {"progressToken": 91.5},
            },
        },
        send,
    )
    await asyncio.wait_for(started.wait(), timeout=1.0)
    assert any(
        m.get("method") == "notifications/progress" and m["params"]["progressToken"] == 91.5
        for m in messages
    )
    await server.handle_message(
        {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": "cancel-me", "reason": "test"},
        },
        send,
    )
    response = next(message for message in messages if message.get("id") == "cancel-me")
    assert response["error"]["code"] == -32800
    for _ in range(100):
        if "cancel-me" not in server._pending:
            break
        await asyncio.sleep(0.01)
    assert "cancel-me" not in server._pending
    await server.close()


async def test_mcp_bounds_in_flight_calls(tmp_path: Path) -> None:
    never = asyncio.Event()

    async def slow_tool(
        arguments: Mapping[str, Any], report_progress: ProgressReporter
    ) -> dict[str, Any]:
        await never.wait()
        return {"ok": True}

    tool = McpTool(
        name="test.slow",
        description="Pending-call bound test.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=slow_tool,
    )
    server = McpProtocolServer(workspace_root=tmp_path, signing_key=_KEY, tools=(tool,))
    messages: list[dict[str, Any]] = []
    send = _sender(messages)
    await server.handle_message(_initialize(), send)
    await server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}, send)

    for request_id in range(2, mcp_server_module.MAX_PENDING_CALLS + 2):
        await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": "test.slow", "arguments": {}},
            },
            send,
        )
    assert len(server._pending) == mcp_server_module.MAX_PENDING_CALLS
    rejected_id = mcp_server_module.MAX_PENDING_CALLS + 2
    await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": rejected_id,
            "method": "tools/call",
            "params": {"name": "test.slow", "arguments": {}},
        },
        send,
    )
    response = next(message for message in messages if message.get("id") == rejected_id)
    assert response["error"]["code"] == -32000
    await server.close()


async def test_stdio_rejects_invalid_and_overlong_json_lines(tmp_path: Path) -> None:
    reader = io.StringIO(
        "{not-json}\n" + "x" * (mcp_stdio_module.MAX_MCP_LINE_CHARS + 5) + "\nNaN\n\n"
    )
    writer = io.StringIO()
    server = McpProtocolServer(workspace_root=tmp_path, signing_key=_KEY)
    await mcp_stdio_module.serve_stdio(server, reader=reader, writer=writer)
    responses = [json.loads(line) for line in writer.getvalue().splitlines()]
    assert len(responses) == 3
    assert all(response["error"]["code"] == -32700 for response in responses)
    assert responses[1]["error"]["message"].startswith("Message exceeds")


async def test_mcp_tool_result_is_bounded_and_signed(tmp_path: Path) -> None:
    async def large_tool(
        arguments: Mapping[str, Any], report_progress: ProgressReporter
    ) -> dict[str, Any]:
        return {"ok": True, "payload": "x" * mcp_server_module.MAX_RESULT_BYTES}

    tool = McpTool(
        name="test.large",
        description="Oversized-result test.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=large_tool,
    )
    server = McpProtocolServer(workspace_root=tmp_path, signing_key=_KEY, tools=(tool,))
    messages: list[dict[str, Any]] = []
    send = _sender(messages)
    await server.handle_message(_initialize(), send)
    await server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}, send)
    await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "test.large", "arguments": {}},
        },
        send,
    )
    for _ in range(100):
        if any(message.get("id") == 2 for message in messages):
            break
        await asyncio.sleep(0.01)
    response = next(message for message in messages if message.get("id") == 2)
    assert response["result"]["isError"] is True
    envelope = json.loads(response["result"]["content"][0]["text"])
    assert verify_signed_mcp_result(envelope, _KEY)
    assert envelope["result"]["error"] == "Signed MCP result exceeded its byte limit."
    await server.close()


async def test_mcp_tool_deadline_returns_signed_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    never = asyncio.Event()

    async def slow_tool(
        arguments: Mapping[str, Any], report_progress: ProgressReporter
    ) -> dict[str, Any]:
        await never.wait()
        return {"ok": True}

    tool = McpTool(
        name="test.timeout",
        description="Tool-deadline test.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=slow_tool,
    )
    monkeypatch.setattr(mcp_server_module, "MCP_TOOL_TIMEOUT_S", 0.01)
    server = McpProtocolServer(workspace_root=tmp_path, signing_key=_KEY, tools=(tool,))
    messages: list[dict[str, Any]] = []
    send = _sender(messages)
    await server.handle_message(_initialize(), send)
    await server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}, send)
    await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "test.timeout", "arguments": {}},
        },
        send,
    )
    for _ in range(100):
        if any(message.get("id") == 2 for message in messages):
            break
        await asyncio.sleep(0.01)
    response = next(message for message in messages if message.get("id") == 2)
    envelope = json.loads(response["result"]["content"][0]["text"])
    assert response["result"]["isError"] is True
    assert verify_signed_mcp_result(envelope, _KEY)
    assert "timed out" in envelope["result"]["error"]
    await server.close()


async def test_file_inspections_are_concurrency_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    release = threading.Event()
    guard = threading.Lock()
    active = 0
    peak_active = 0

    def blocked_inspection(root: Path, relative_path: str) -> dict[str, Any]:
        nonlocal active, peak_active
        with guard:
            active += 1
            peak_active = max(peak_active, active)
            if peak_active == mcp_server_module.MAX_CONCURRENT_FILE_INSPECTIONS:
                started.set()
        release.wait(timeout=2.0)
        with guard:
            active -= 1
        return {"ok": True, "path": relative_path, "root": str(root)}

    server = McpProtocolServer(workspace_root=tmp_path, signing_key=_KEY)
    messages: list[dict[str, Any]] = []
    send = _sender(messages)
    await server.handle_message(_initialize(), send)
    await server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}, send)

    monkeypatch.setattr(mcp_inspector_module, "_inspect_file_sync", blocked_inspection)
    try:
        request_ids = tuple(range(2, mcp_server_module.MAX_CONCURRENT_FILE_INSPECTIONS + 6))
        for request_id in request_ids:
            await server.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {
                        "name": "ai2027.inspect_file",
                        "arguments": {"path": "fixture.txt"},
                    },
                },
                send,
            )
        assert await asyncio.wait_for(asyncio.to_thread(started.wait, 1.0), timeout=2.0)
        await asyncio.sleep(0.02)
        assert peak_active == mcp_server_module.MAX_CONCURRENT_FILE_INSPECTIONS
        release.set()
        for _ in range(100):
            if all(
                any(message.get("id") == request_id for message in messages)
                for request_id in request_ids
            ):
                break
            await asyncio.sleep(0.01)
        assert all(
            any(message.get("id") == request_id for message in messages)
            for request_id in request_ids
        )
    finally:
        release.set()
        await server.close()


async def test_stdio_subprocess_is_usable_by_an_external_mcp_client(tmp_path: Path) -> None:
    (tmp_path / "sample.md").write_text("# MCP\nEvidence first.\n", encoding="utf-8")
    env = os.environ.copy()
    env["AI2027_MCP_SIGNING_KEY"] = _KEY.hex()
    env["PYTHONPATH"] = str(_SOURCE_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "ai2027_protocol",
        "--root",
        str(tmp_path),
        "mcp",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdin = process.stdin
    stdout = process.stdout
    stderr = process.stderr
    assert stdin is not None
    assert stdout is not None
    assert stderr is not None

    async def send(message: dict[str, Any]) -> None:
        stdin.write(json.dumps(message).encode("utf-8") + b"\n")
        await stdin.drain()

    async def read_message() -> dict[str, Any]:
        line = await asyncio.wait_for(stdout.readline(), timeout=3.0)
        assert line, "MCP server closed stdout before returning a response"
        payload = json.loads(line)
        assert isinstance(payload, dict)
        return payload

    try:
        await send(_initialize())
        initialize_response = await read_message()
        assert initialize_response["result"]["serverInfo"]["name"] == "ai-2027"
        await send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        await send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tool_list = await read_message()
        assert tool_list["result"]["tools"][0]["name"] == "ai2027.inspect_file"

        await send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "ai2027.inspect_file",
                    "arguments": {"path": "sample.md"},
                    "_meta": {"progressToken": "external-3"},
                },
            }
        )
        progress_seen = False
        while True:
            message = await read_message()
            if message.get("method") == "notifications/progress":
                progress_seen = True
                continue
            if message.get("id") == 3:
                break
        assert progress_seen
        envelope = json.loads(message["result"]["content"][0]["text"])
        assert verify_signed_mcp_result(
            envelope,
            _KEY,
            expected_tool="ai2027.inspect_file",
            expected_request_id=3,
            expected_path="sample.md",
        )
        assert envelope["result"]["verification"]["ok"] is True
        assert (tmp_path / "sample.md").read_text(encoding="utf-8") == "# MCP\nEvidence first.\n"
    finally:
        stdin.close()
        await stdin.wait_closed()
        await asyncio.wait_for(process.wait(), timeout=3.0)
    assert process.returncode == 0
    assert await stderr.read() == b""
