#!/usr/bin/env python3
"""Call one tool on the filesystem MCP server (mcpskills-cli style call script).

Stdlib only. Speaks newline-delimited JSON-RPC over stdio to:
    npx -y @modelcontextprotocol/server-filesystem@2.4.0 <ROOT>

Version pinned (2.4.0 includes the directory-listing symlink fix).
npm integrity: sha512-BoT8Gws5MsetfAey4vDyuzy8WYQES6mGYLSShuqSNGAC0X0NBV4p78/R78KfW4RkxtWz+zxhS9jwhzJrQUTt2Q==

Usage:
    python3 scripts/call.py <tool_name> '<json-arguments>' [ROOT]

Example:
    python3 scripts/call.py list_allowed_directories '{}' /tmp/mcp-fs-root

Write approval (F-03): the six mutating tools are refused unless the
execution layer sets AI2027_SKILL_WRITE_APPROVAL=1 after explicit user
approval. The gate lives in this wrapper, not in skill prose.

Bounded waits (F-01): initialize / tools/call / shutdown all have
deadlines (env-overridable: SKILL_MCP_TIMEOUT_INIT, SKILL_MCP_TIMEOUT_CALL,
SKILL_MCP_TIMEOUT_SHUTDOWN — seconds, positive floats).

Error hygiene (F-02/F-04): JSON-RPC error objects and tool error text are
path-redacted and length-bounded on stdout; raw payloads and the child
server's stderr go to this process's stderr only. Successful tool text is
passed through unchanged BY DESIGN (read_file must return file content;
it may contain paths as data).

The server binary/root are the skill's "credentials" (analogous to
mcpskills-cli's ~/.mcps/credentials): edit here or pass ROOT explicitly.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys

PROTOCOL_VERSION = "2025-06-18"

DEFAULT_INIT_TIMEOUT = 30.0
DEFAULT_CALL_TIMEOUT = 60.0
DEFAULT_SHUTDOWN_TIMEOUT = 5.0
MAX_KILL_WAIT = 2.0

WRITE_TOOLS = frozenset({
    "write_file", "edit_file", "append_file",
    "copy_file", "move_file", "create_directory",
})
APPROVAL_ENV = "AI2027_SKILL_WRITE_APPROVAL"

# Absolute-path-like tokens (POSIX /..., Windows C:\..., file://...).
_PATH_RE = re.compile(
    r"(?:file://[^\s\"']+"
    r"|[A-Za-z]:[\\/][^\s\"']+"
    r"|(?<![\w])/(?:[\w.@+-]+/)*[\w.@+-]+)"
)
_MAX_ERR_CHARS = 300
_MAX_TOOL_ERR_CHARS = 2000


class ServerTimeout(RuntimeError):
    """A bounded wait expired; reported as a generic, non-leaking error."""


def _timeout(env_name: str, default: float) -> float:
    try:
        value = float(os.environ.get(env_name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def _redact(text: str, limit: int) -> str:
    return _PATH_RE.sub("[redacted-path]", text)[:limit]


def _sanitize_error_obj(err: dict) -> dict:
    """Bounded, path-free view of a JSON-RPC error for stdout (F-02)."""
    message = err.get("message")
    return {
        "code": err.get("code"),
        "message": _redact(str(message), _MAX_ERR_CHARS) if message is not None else None,
        # `data` is dropped: it can embed arbitrary server-side detail.
    }


def _log_raw(label: str, payload) -> None:
    try:
        raw = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        raw = repr(payload)
    print(f"diagnostic: {label}: {raw[:500]}", file=sys.stderr)


async def _shutdown(proc: asyncio.subprocess.Process) -> None:
    """Bounded teardown (F-01): close stdin, wait, then terminate/kill."""
    try:
        if proc.stdin and not proc.stdin.is_closing():
            proc.stdin.close()
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    try:
        await asyncio.wait_for(
            proc.wait(), _timeout("SKILL_MCP_TIMEOUT_SHUTDOWN", DEFAULT_SHUTDOWN_TIMEOUT)
        )
    except TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), MAX_KILL_WAIT)
        except TimeoutError:
            pass
    except ProcessLookupError:
        pass


async def call_tool(tool_name: str, arguments: dict, root: str) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "npx", "-y", "@modelcontextprotocol/server-filesystem@2.4.0", root,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        # F-04: child stderr is inherited (local diagnostics only; it can
        # never reach the stdout JSON channel).
    )
    assert proc.stdin and proc.stdout

    async def send(msg: dict) -> None:
        proc.stdin.write((json.dumps(msg, separators=(",", ":")) + "\n").encode())
        await proc.stdin.drain()

    async def recv_until(req_id: int) -> dict:
        while True:
            line = await proc.stdout.readline()
            if not line:
                raise RuntimeError("server closed stdout before response")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == req_id:
                return msg

    try:
        await send({
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "filesystem-skill", "version": "1.0"},
            },
        })
        try:
            init = await asyncio.wait_for(
                recv_until(0), _timeout("SKILL_MCP_TIMEOUT_INIT", DEFAULT_INIT_TIMEOUT)
            )
        except TimeoutError as exc:
            raise ServerTimeout("timed out waiting for initialize response") from exc
        if "error" in init:
            _log_raw("initialize error", init["error"])
            return {"ok": False, "error": _sanitize_error_obj(init["error"])}
        await send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        await send({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        })
        try:
            result = await asyncio.wait_for(
                recv_until(1), _timeout("SKILL_MCP_TIMEOUT_CALL", DEFAULT_CALL_TIMEOUT)
            )
        except TimeoutError as exc:
            raise ServerTimeout("timed out waiting for tools/call response") from exc
        if "error" in result:
            _log_raw("tools/call error", result["error"])
            return {"ok": False, "error": _sanitize_error_obj(result["error"])}
        res = result.get("result", {})
        texts = [c.get("text", "") for c in res.get("content", []) if c.get("type") == "text"]
        ok = not res.get("isError", False)
        text = "\n".join(texts)
        if not ok:
            # Tool-level error text: redact paths + bound (F-02).
            _log_raw("tool error text", text)
            text = _redact(text, _MAX_TOOL_ERR_CHARS)
        # Success text passes through unchanged BY DESIGN (F-02): read_file
        # must return file content verbatim; README documents the trade-off.
        return {"ok": ok, "text": text}
    finally:
        await _shutdown(proc)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    tool = sys.argv[1][:64]
    if tool in WRITE_TOOLS and os.environ.get(APPROVAL_ENV) != "1":
        # F-03: enforced here, in the wrapper — not by skill prose.
        print(json.dumps({
            "ok": False,
            "error": (f"write approval required: {tool} refused; the execution "
                      f"layer must set {APPROVAL_ENV}=1 after explicit approval"),
        }, ensure_ascii=False, indent=2))
        return 1
    try:
        args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    except json.JSONDecodeError as exc:
        print(f"invalid JSON arguments: {exc}", file=sys.stderr)
        return 2
    root = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("SKILL_FS_ROOT", ".")
    try:
        outcome = asyncio.run(call_tool(tool, args, root))
    except ServerTimeout as exc:
        print(f"diagnostic: {type(exc).__name__}: {exc}", file=sys.stderr)
        outcome = {"ok": False,
                   "error": "server timeout: the MCP server did not respond within the deadline"}
    except (RuntimeError, OSError) as exc:
        # Server died before/while responding (bad root, crash, missing binary).
        # External response stays generic and bounded; diagnostics (which may
        # contain local paths) go to stderr only, never into the JSON result.
        print(f"diagnostic: {type(exc).__name__}: {exc}", file=sys.stderr)
        outcome = {"ok": False, "error": "server failure: the MCP server did not respond"}
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return 0 if outcome.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
