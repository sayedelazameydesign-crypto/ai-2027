"""Sandboxed autonomous tool runtime bridging L0 Policy, L1 Memory, and L5 Connectors."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import Field

from .auditor import ProtocolAuditor, format_audit_markdown
from .auditor_ast import SKIP_DIRS
from .bridges import ConnectorsBridge, KernelSandboxBridge, MemoryBridge
from .connector_hub_bridge import LiveConnectorClient
from .contracts import StrictModel
from .security import SecurityValidationError, redact_secrets, resolve_safe_path
from .tools_advanced import (
    connector_read_tool,
    connector_search_tool,
    run_sandbox_tests,
    verify_sandbox_artifact,
    workspace_git_diff,
    workspace_grep_tool,
)

_MAX_READ_BYTES = 48_000
_MAX_PROCESS_OUTPUT_BYTES = 24_000
_SECRET_FILENAMES = frozenset({".env", ".env.local", "env.local", "local-pairings.json"})


class ToolSpec(StrictModel):
    """Metadata descriptor advertised to the planner and Studio UI."""

    name: str
    description: str
    read_only: bool
    parameters_hint: str


class ToolInvocation(StrictModel):
    """Validated tool call issued by an autonomous planning step."""

    tool: str = Field(min_length=2, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)


class ToolOutcome(StrictModel):
    """Structured result emitted by executing one sandboxed tool."""

    tool: str
    ok: bool
    summary: str
    output: str = ""
    latency_ms: float = Field(default=0.0, ge=0.0)
    verified_sha256: str | None = None
    artifact_path: str | None = None
    artifact_kind: str | None = None
    approval_id: str | None = None
    policy_checks: dict[str, bool] = Field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class ToolContext:
    """Isolated per-session execution environment passed to tool handlers."""

    session_id: str
    workspace_root: Path
    sandbox_dir: Path
    ledger_path: Path
    sessions_root: Path
    kernel_bridge: KernelSandboxBridge
    memory_bridge: MemoryBridge
    connectors_bridge: ConnectorsBridge
    auditor: ProtocolAuditor
    connector_client: LiveConnectorClient = field(default_factory=LiveConnectorClient)
    read_only: bool = False


@runtime_checkable
class ToolExecutor(Protocol):
    """Replaceable contract for autonomous tool execution and parallel read batches."""

    def specs(self) -> tuple[ToolSpec, ...]:
        """Return tool descriptors understood by this runtime."""
        ...

    async def execute(self, invocation: ToolInvocation, ctx: ToolContext) -> ToolOutcome:
        """Execute a single tool call inside the bounded session context."""
        ...

    async def execute_parallel(
        self, invocations: Sequence[ToolInvocation], ctx: ToolContext
    ) -> list[ToolOutcome]:
        """Execute multiple read-only tool calls concurrently with asyncio.TaskGroup."""
        ...


def infer_artifact_kind(rel_path: str) -> str:
    """Classify an artifact extension for live Studio preview rendering."""
    suffix = Path(rel_path).suffix.lower()
    mapping = {
        ".html": "html",
        ".htm": "html",
        ".svg": "svg",
        ".md": "markdown",
        ".markdown": "markdown",
        ".py": "python",
        ".json": "json",
        ".js": "javascript",
        ".mjs": "javascript",
        ".css": "css",
        ".csv": "csv",
    }
    return mapping.get(suffix, "text")


_TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="workspace.list_files",
        description="List files inside a workspace or sandbox subdirectory.",
        read_only=True,
        parameters_hint='{"path": ".", "scope": "workspace|sandbox", "limit": 60}',
    ),
    ToolSpec(
        name="workspace.read_file",
        description="Read text lines from a workspace or sandbox file safely.",
        read_only=True,
        parameters_hint='{"path": "README.md", "scope": "workspace|sandbox", "max_lines": 120}',
    ),
    ToolSpec(
        name="workspace.grep",
        description="Search repository source files concurrently for a pattern.",
        read_only=True,
        parameters_hint='{"query": "Kernel", "path": ".", "limit": 25}',
    ),
    ToolSpec(
        name="sandbox.write_artifact",
        description="Write HTML/SVG/Markdown/Python/JSON deliverable verified by L0 SHA-256.",
        read_only=False,
        parameters_hint='{"path": "reports/summary.html", "content": "..."}',
    ),
    ToolSpec(
        name="sandbox.edit_file",
        description="Replace a unique substring inside an existing sandbox artifact.",
        read_only=False,
        parameters_hint='{"path": "app.py", "old_text": "...", "new_text": "..."}',
    ),
    ToolSpec(
        name="sandbox.run_python",
        description="Execute an isolated Python 3 script inside the sandbox with scrubbed env.",
        read_only=False,
        parameters_hint='{"path": "analyze.py", "code": "print(1+1)", "timeout_s": 5.0}',
    ),
    ToolSpec(
        name="sandbox.run_tests",
        description="Execute unittest tests inside the isolated sandbox with scrubbed env.",
        read_only=False,
        parameters_hint='{"path": ".", "pattern": "test_*.py", "timeout_s": 8.0}',
    ),
    ToolSpec(
        name="sandbox.verify_artifact",
        description="Independently re-read artifact bytes, SHA-256 hash, and L0 ledger chain.",
        read_only=True,
        parameters_hint='{"path": "deliverable.html"}',
    ),
    ToolSpec(
        name="workspace.git_diff",
        description="Inspect repository git status and diff --stat cleanly (read-only).",
        read_only=True,
        parameters_hint='{"path": "."}',
    ),
    ToolSpec(
        name="memory.search",
        description="Query L1 EvidenceMemoryStore for verified past findings.",
        read_only=True,
        parameters_hint='{"query": "agi-system verification", "k": 5}',
    ),
    ToolSpec(
        name="repo.audit",
        description="Audit a relative repository module against the 10 Python 2027 rules.",
        read_only=True,
        parameters_hint='{"path": "python-2027"}',
    ),
    ToolSpec(
        name="connector.inspect",
        description="List L5 workspace connectors and sanitized readiness without secrets.",
        read_only=True,
        parameters_hint="{}",
    ),
    ToolSpec(
        name="connector.search",
        description="Search a configured L5 connector (read-only, bounded).",
        read_only=True,
        parameters_hint='{"connector": "github", "query": "ai-2027", "limit": 5}',
    ),
    ToolSpec(
        name="connector.read",
        description="Read bounded UTF-8 metadata/content for one L5 connector resource.",
        read_only=True,
        parameters_hint='{"connector": "github", "resource_id": "owner/repo"}',
    ),
    ToolSpec(
        name="connector.propose_action",
        description="Queue an external connector mutation for explicit human approval.",
        read_only=False,
        parameters_hint=(
            '{"connector": "gmail", "summary": "...", "method": "POST", '
            '"path": "/drafts", "payload": {}}'
        ),
    ),
)


class SandboxToolRuntime:
    """Default async tool executor enforcing L0 Policy, L1 Memory, and L5 Approvals."""

    def specs(self) -> tuple[ToolSpec, ...]:
        """Return all registered workspace, sandbox, memory, and connector tool specs."""
        return _TOOL_SPECS

    async def execute(self, invocation: ToolInvocation, ctx: ToolContext) -> ToolOutcome:
        """Dispatch one validated tool invocation inside ctx with elapsed timing."""
        start_ns = time.perf_counter_ns()
        try:
            outcome = await self._dispatch(invocation, ctx)
        except SecurityValidationError as exc:
            outcome = ToolOutcome(
                tool=invocation.tool,
                ok=False,
                summary=f"Security policy rejected {invocation.tool}",
                error=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - tools must degrade cleanly without crashing agent
            outcome = ToolOutcome(
                tool=invocation.tool,
                ok=False,
                summary=f"Tool {invocation.tool} failed",
                error=redact_secrets(f"{type(exc).__name__}: {exc}"),
            )
        elapsed_ms = round((time.perf_counter_ns() - start_ns) / 1e6, 3)
        return outcome.model_copy(update={"latency_ms": elapsed_ms})

    async def execute_parallel(
        self, invocations: Sequence[ToolInvocation], ctx: ToolContext
    ) -> list[ToolOutcome]:
        """Run independent read-only tool calls concurrently using asyncio.TaskGroup."""
        if not invocations:
            return []
        read_only_names = {spec.name for spec in _TOOL_SPECS if spec.read_only}
        if any(inv.tool not in read_only_names for inv in invocations):
            return [await self.execute(inv, ctx) for inv in invocations]

        results: list[ToolOutcome | None] = [None] * len(invocations)

        async def _worker(idx: int, inv: ToolInvocation) -> None:
            results[idx] = await self.execute(inv, ctx)

        async with asyncio.TaskGroup() as group:
            for idx, inv in enumerate(invocations):
                group.create_task(_worker(idx, inv))

        return [item for item in results if item is not None]

    async def _dispatch(self, invocation: ToolInvocation, ctx: ToolContext) -> ToolOutcome:
        tool = invocation.tool
        args = invocation.args
        if tool == "workspace.list_files":
            return await self._list_files(args, ctx)
        if tool == "workspace.read_file":
            return await self._read_file(args, ctx)
        if tool == "workspace.grep":
            return await workspace_grep_tool(args, ctx)
        if tool == "sandbox.write_artifact":
            return await self._write_artifact(args, ctx)
        if tool == "sandbox.edit_file":
            return await self._edit_file(args, ctx)
        if tool == "sandbox.run_python":
            return await self._run_python(args, ctx)
        if tool == "sandbox.run_tests":
            return await run_sandbox_tests(args, ctx)
        if tool == "sandbox.verify_artifact":
            return await verify_sandbox_artifact(args, ctx)
        if tool == "workspace.git_diff":
            return await workspace_git_diff(args, ctx)
        if tool == "memory.search":
            return await self._memory_search(args, ctx)
        if tool == "repo.audit":
            return await self._repo_audit(args, ctx)
        if tool == "connector.inspect":
            return await self._connector_inspect(ctx)
        if tool == "connector.search":
            return await connector_search_tool(args, ctx)
        if tool == "connector.read":
            return await connector_read_tool(args, ctx)
        if tool == "connector.propose_action":
            return await self._connector_propose(args, ctx)
        return ToolOutcome(
            tool=tool,
            ok=False,
            summary=f"Unknown tool {tool!r}",
            error=f"Unsupported tool name: {tool}",
        )

    def _select_base(self, scope: object, ctx: ToolContext) -> Path:
        if str(scope or "workspace").lower() == "sandbox":
            ctx.sandbox_dir.mkdir(parents=True, exist_ok=True)
            return ctx.sandbox_dir
        return ctx.workspace_root

    async def _list_files(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolOutcome:
        rel = str(args.get("path", ".")).strip() or "."
        base = self._select_base(args.get("scope"), ctx)
        limit = max(1, min(int(args.get("limit", 60)), 150))
        target = resolve_safe_path(base, rel)

        def _scan() -> list[str]:
            if not target.is_dir():
                return []
            entries: list[str] = []
            for root_str, dirs, files in os.walk(target):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
                root_path = Path(root_str)
                for fname in sorted(files):
                    if fname in _SECRET_FILENAMES or fname.startswith(".env"):
                        continue
                    full = root_path / fname
                    entries.append(str(full.relative_to(base)))
                    if len(entries) >= limit:
                        return entries
            return entries

        found = await asyncio.to_thread(_scan)
        return ToolOutcome(
            tool="workspace.list_files",
            ok=True,
            summary=f"Listed {len(found)} file(s) under {rel}",
            output="\n".join(found),
        )

    async def _read_file(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolOutcome:
        rel = str(args.get("path", "")).strip()
        if not rel:
            raise SecurityValidationError("workspace.read_file requires non-empty path.")
        if Path(rel).name in _SECRET_FILENAMES or Path(rel).name.startswith(".env"):
            raise SecurityValidationError(
                "Direct reading of secret environment files is forbidden."
            )
        base = self._select_base(args.get("scope"), ctx)
        target = resolve_safe_path(base, rel)
        offset = max(1, int(args.get("offset", 1)))
        max_lines = max(1, min(int(args.get("max_lines", 120)), 300))

        def _read() -> tuple[str, int]:
            if not target.is_file():
                raise FileNotFoundError(f"File not found: {rel}")
            raw = target.read_bytes()[:_MAX_READ_BYTES].decode("utf-8", errors="replace")
            lines = raw.splitlines()
            slice_lines = lines[offset - 1 : offset - 1 + max_lines]
            formatted = "\n".join(f"{offset + idx}: {line}" for idx, line in enumerate(slice_lines))
            return redact_secrets(formatted), len(lines)

        text, total_lines = await asyncio.to_thread(_read)
        return ToolOutcome(
            tool="workspace.read_file",
            ok=True,
            summary=f"Read {rel} ({total_lines} total lines)",
            output=text,
        )

    async def _write_artifact(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolOutcome:
        if ctx.read_only:
            return ToolOutcome(
                tool="sandbox.write_artifact",
                ok=False,
                summary="Blocked by AI2027_READ_ONLY",
                error="Service is running in read-only mode.",
            )
        rel_path = str(args.get("path", "")).strip()
        content = str(args.get("content", ""))
        _ = resolve_safe_path(ctx.sandbox_dir, rel_path)
        kernel_res, raw_rec = await ctx.kernel_bridge.write_verified_file(
            sandbox_dir=ctx.sandbox_dir,
            ledger_path=ctx.ledger_path,
            rel_path=rel_path,
            content=content,
            session_id=ctx.session_id,
            sessions_root=ctx.sessions_root,
        )
        ok = kernel_res.status == "VERIFIED"
        admitted = False
        verdict_msg = ""
        if ok:
            admitted, verdict_msg = await ctx.memory_bridge.admit_record(raw_rec)
        return ToolOutcome(
            tool="sandbox.write_artifact",
            ok=ok,
            summary=(
                f"Verified artifact {rel_path} ({kernel_res.byte_size} B, "
                f"SHA-256 {(kernel_res.sha256 or '')[:12]}…)"
                if ok
                else f"L0 rejected artifact {rel_path}: {kernel_res.status}"
            ),
            output=verdict_msg or f"L0 status={kernel_res.status}, memory_admitted={admitted}",
            verified_sha256=kernel_res.sha256,
            artifact_path=rel_path if ok else None,
            artifact_kind=infer_artifact_kind(rel_path) if ok else None,
            policy_checks=kernel_res.checks,
            error=kernel_res.error,
        )

    async def _edit_file(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolOutcome:
        if ctx.read_only:
            return ToolOutcome(
                tool="sandbox.edit_file",
                ok=False,
                summary="Blocked by AI2027_READ_ONLY",
                error="Service is running in read-only mode.",
            )
        rel_path = str(args.get("path", "")).strip()
        old_text = str(args.get("old_text", ""))
        new_text = str(args.get("new_text", ""))
        if not old_text:
            raise SecurityValidationError("sandbox.edit_file requires non-empty old_text.")
        target = resolve_safe_path(ctx.sandbox_dir, rel_path)

        def _read_existing() -> str:
            if not target.is_file():
                raise FileNotFoundError(f"Sandbox file does not exist: {rel_path}")
            return target.read_text(encoding="utf-8")

        existing = await asyncio.to_thread(_read_existing)
        occurrences = existing.count(old_text)
        if occurrences != 1:
            raise SecurityValidationError(
                f"old_text must match exactly once in {rel_path} (matched {occurrences} times)."
            )
        updated = existing.replace(old_text, new_text, 1)
        res = await self._write_artifact({"path": rel_path, "content": updated}, ctx)
        return res.model_copy(update={"tool": "sandbox.edit_file"})

    async def _run_python(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolOutcome:
        if ctx.read_only:
            return ToolOutcome(
                tool="sandbox.run_python",
                ok=False,
                summary="Blocked by AI2027_READ_ONLY",
                error="Service is running in read-only mode.",
            )
        rel_path = str(args.get("path", "run_script.py")).strip() or "run_script.py"
        if not rel_path.endswith(".py"):
            raise SecurityValidationError("sandbox.run_python path must end with .py")
        timeout_s = max(0.2, min(float(args.get("timeout_s", 5.0)), 10.0))
        code_raw = args.get("code")
        target = resolve_safe_path(ctx.sandbox_dir, rel_path)

        sha: str | None = None
        if code_raw is not None:
            write_outcome = await self._write_artifact(
                {"path": rel_path, "content": str(code_raw)}, ctx
            )
            if not write_outcome.ok:
                return write_outcome.model_copy(update={"tool": "sandbox.run_python"})
            sha = write_outcome.verified_sha256
        elif not await asyncio.to_thread(target.is_file):
            raise FileNotFoundError(f"Sandbox script not found: {rel_path}")

        safe_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-B",
            str(target),
            cwd=str(ctx.sandbox_dir),
            env=safe_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(timeout_s):
                stdout_b, stderr_b = await proc.communicate()
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return ToolOutcome(
                tool="sandbox.run_python",
                ok=False,
                summary=f"Python execution timed out after {timeout_s:.1f}s",
                artifact_path=rel_path,
                artifact_kind="python",
                verified_sha256=sha,
                error=f"Execution exceeded {timeout_s:.1f}s limit.",
            )

        stdout_txt = redact_secrets(
            stdout_b[:_MAX_PROCESS_OUTPUT_BYTES].decode("utf-8", errors="replace")
        )
        stderr_txt = redact_secrets(
            stderr_b[:_MAX_PROCESS_OUTPUT_BYTES].decode("utf-8", errors="replace")
        )
        combined = stdout_txt.strip()
        if stderr_txt.strip():
            combined = f"{combined}\n[stderr]\n{stderr_txt.strip()}".strip()
        ok = proc.returncode == 0
        return ToolOutcome(
            tool="sandbox.run_python",
            ok=ok,
            summary=(
                f"Executed {rel_path} (exit={proc.returncode})"
                if ok
                else f"{rel_path} exited with code {proc.returncode}"
            ),
            output=combined,
            verified_sha256=sha,
            artifact_path=rel_path,
            artifact_kind="python",
            error=None if ok else (stderr_txt.strip() or f"Process exited {proc.returncode}"),
        )

    async def _memory_search(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolOutcome:
        query = str(args.get("query", "")).strip()
        if not query:
            raise SecurityValidationError("memory.search requires a non-empty query.")
        k = max(1, min(int(args.get("k", 5)), 12))
        hits = await ctx.memory_bridge.search(query, k=k)
        lines = [
            f"[{hit.score:.3f} | above_noise={hit.above_noise}] {hit.run_id}: {hit.text[:160]}"
            for hit in hits
        ]
        return ToolOutcome(
            tool="memory.search",
            ok=True,
            summary=f"Recalled {len(hits)} verified memory hit(s)",
            output="\n".join(lines) if lines else "No verified memories matched yet.",
        )

    async def _repo_audit(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolOutcome:
        rel = str(args.get("path", "python-2027")).strip() or "python-2027"
        _ = resolve_safe_path(ctx.workspace_root, rel)
        report = await ctx.auditor.audit_projects([rel])
        return ToolOutcome(
            tool="repo.audit",
            ok=True,
            summary=f"Audited {rel}: {report.average_score_pct}% protocol score",
            output=format_audit_markdown(report),
        )

    async def _connector_inspect(self, ctx: ToolContext) -> ToolOutcome:
        items = await ctx.connectors_bridge.list_connectors()
        lines = [
            f"{item.connector_id:<16} configured={str(item.configured):<5} "
            f"env={item.credential_env_hint} state={item.credential_state}"
            for item in items
        ]
        return ToolOutcome(
            tool="connector.inspect",
            ok=True,
            summary=f"Inspected {len(items)} L5 workspace connector(s)",
            output="\n".join(lines),
        )

    async def _connector_propose(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolOutcome:
        if ctx.read_only:
            return ToolOutcome(
                tool="connector.propose_action",
                ok=False,
                summary="Blocked by AI2027_READ_ONLY",
                error="Service is running in read-only mode.",
            )
        connector = str(args.get("connector", "")).strip().lower()
        summary = str(args.get("summary", "Proposed external connector mutation")).strip()
        method_raw = str(args.get("method", "POST")).strip().upper()
        method: Literal["POST", "PUT", "PATCH", "DELETE"] = (
            method_raw if method_raw in {"POST", "PUT", "PATCH", "DELETE"} else "POST"  # type: ignore[assignment]
        )
        path = str(args.get("path", "/items")).strip() or "/items"
        payload = args.get("payload")
        payload_dict = dict(payload) if isinstance(payload, Mapping) else {}
        proposal = await ctx.connectors_bridge.propose_action(
            session_id=ctx.session_id,
            connector=connector,
            summary=summary,
            method=method,
            path=path,
            payload=payload_dict,
        )
        return ToolOutcome(
            tool="connector.propose_action",
            ok=True,
            summary=(
                f"Queued approval {proposal.action_id} for {proposal.connector} (status=pending)"
            ),
            output=(
                f"Action requires human approval in Studio/CLI before execution.\n"
                f"id={proposal.action_id} sha256={proposal.request_sha256}"
            ),
            verified_sha256=proposal.request_sha256,
            approval_id=proposal.action_id,
        )
