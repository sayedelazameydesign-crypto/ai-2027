"""Advanced coding, verification, git inspection, and connector tools for the agent."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .auditor_ast import SKIP_DIRS
from .celia_memory import CeliaMemoryStore, OllamaEmbeddingBackend
from .ollama_layer import OllamaRuntimeClient
from .security import SecurityValidationError, redact_secrets, resolve_safe_path

if TYPE_CHECKING:
    from .tools import ToolContext, ToolOutcome

_MAX_OUTPUT_BYTES = 24_000
_MAX_READ_BYTES = 48_000
_SECRET_FILENAMES = frozenset({".env", ".env.local", "env.local", "local-pairings.json"})
_UNITTEST_SUMMARY_RE = re.compile(r"Ran\s+(\d+)\s+tests?\s+in", re.IGNORECASE)


def _outcome(
    tool: str,
    *,
    ok: bool,
    summary: str,
    output: str = "",
    verified_sha256: str | None = None,
    artifact_path: str | None = None,
    artifact_kind: str | None = None,
    policy_checks: dict[str, bool] | None = None,
    error: str | None = None,
) -> ToolOutcome:
    from .tools import ToolOutcome

    return ToolOutcome(
        tool=tool,
        ok=ok,
        summary=summary,
        output=output,
        verified_sha256=verified_sha256,
        artifact_path=artifact_path,
        artifact_kind=artifact_kind,
        policy_checks=policy_checks or {},
        error=error,
    )


async def run_sandbox_tests(args: Mapping[str, Any], ctx: ToolContext) -> ToolOutcome:
    """Execute unittest discovery inside the isolated sandbox with scrubbed env."""
    if ctx.read_only:
        return _outcome(
            "sandbox.run_tests",
            ok=False,
            summary="Blocked by AI2027_READ_ONLY",
            error="Service is running in read-only mode.",
        )
    target_rel = str(args.get("path", ".")).strip() or "."
    pattern = str(args.get("pattern", "test_*.py")).strip() or "test_*.py"
    if not re.fullmatch(r"[A-Za-z0-9_*.-]{1,64}", pattern):
        raise SecurityValidationError("Test discovery pattern contains invalid characters.")
    timeout_s = max(0.5, min(float(args.get("timeout_s", 8.0)), 15.0))
    target = resolve_safe_path(ctx.sandbox_dir, target_rel)

    if target.is_file():
        cmd = [
            sys.executable,
            "-E",
            "-s",
            "-B",
            "-m",
            "unittest",
            "discover",
            "-s",
            str(target.parent),
            "-t",
            str(ctx.sandbox_dir),
            "-p",
            target.name,
        ]
    elif target.is_dir():
        cmd = [
            sys.executable,
            "-E",
            "-s",
            "-B",
            "-m",
            "unittest",
            "discover",
            "-s",
            str(target),
            "-t",
            str(ctx.sandbox_dir),
            "-p",
            pattern,
        ]
    else:
        raise FileNotFoundError(f"Sandbox test target not found: {target_rel}")

    safe_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    proc = await asyncio.create_subprocess_exec(
        *cmd,
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
        return _outcome(
            "sandbox.run_tests",
            ok=False,
            summary=f"Sandbox tests timed out after {timeout_s:.1f}s",
            error=f"Test run exceeded {timeout_s:.1f}s limit.",
        )

    out_txt = redact_secrets(stdout_b[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"))
    err_txt = redact_secrets(stderr_b[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"))
    combined = "\n".join(part for part in (out_txt.strip(), err_txt.strip()) if part)
    match = _UNITTEST_SUMMARY_RE.search(combined)
    ran_count = int(match.group(1)) if match else 0
    ok = proc.returncode == 0 and ran_count > 0
    if proc.returncode == 0 and ran_count == 0:
        summary = "No matching unit tests executed (0 tests ran)"
        err_msg: str | None = "Refusing silent pass on 0 discovered tests."
    elif ok:
        summary = f"Sandbox tests passed ({ran_count} test(s), exit=0)"
        err_msg = None
    else:
        summary = f"Sandbox tests failed ({ran_count} test(s), exit={proc.returncode})"
        err_msg = err_txt.strip() or f"unittest exited {proc.returncode}"

    return _outcome(
        "sandbox.run_tests",
        ok=ok,
        summary=summary,
        output=combined,
        policy_checks={"exit_zero": proc.returncode == 0, "ran_tests": ran_count > 0},
        error=err_msg,
    )


async def verify_sandbox_artifact(args: Mapping[str, Any], ctx: ToolContext) -> ToolOutcome:
    """Independently re-read a sandbox file, compute SHA-256, and check L0 ledger."""
    rel_path = str(args.get("path", "")).strip()
    if not rel_path:
        raise SecurityValidationError("sandbox.verify_artifact requires non-empty path.")
    target = resolve_safe_path(ctx.sandbox_dir, rel_path)

    def _verify_on_disk() -> dict[str, Any]:
        exists = target.is_file()
        disk_bytes = target.read_bytes() if exists else b""
        disk_sha = hashlib.sha256(disk_bytes).hexdigest() if exists else None
        recorded_sha: str | None = None
        ledger_entries = 0
        chain_valid = True
        if ctx.ledger_path.is_file():
            for line in ctx.ledger_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                ledger_entries += 1
                try:
                    entry = json.loads(line)
                except ValueError:
                    chain_valid = False
                    continue
                if not isinstance(entry, dict):
                    continue
                payload = entry.get("payload")
                if not isinstance(payload, dict):
                    continue
                obs = payload.get("observation")
                data = obs.get("data") if isinstance(obs, dict) else payload
                if isinstance(data, dict):
                    obs_path = str(data.get("path") or "")
                    if (
                        obs_path == rel_path
                        or obs_path == str(target)
                        or obs_path.endswith(f"/{rel_path}")
                    ):
                        cand = data.get("sha256")
                        if isinstance(cand, str):
                            recorded_sha = cand
            fsck_res = ctx.kernel_bridge.fsck_ledger_sync(ctx.ledger_path)
            if fsck_res is not None:
                chain_valid = bool(fsck_res.get("chain_valid", True))
        hash_matches = bool(exists and disk_sha and (recorded_sha in {None, disk_sha}))
        tampered = bool(exists and recorded_sha and disk_sha != recorded_sha)
        return {
            "path": rel_path,
            "exists": exists,
            "byte_size": len(disk_bytes),
            "disk_sha256": disk_sha,
            "recorded_ledger_sha256": recorded_sha,
            "ledger_entries": ledger_entries,
            "ledger_chain_valid": chain_valid,
            "hash_matches": hash_matches,
            "tamper_detected": tampered,
        }

    report = await asyncio.to_thread(_verify_on_disk)
    ok = bool(
        report["exists"]
        and report["hash_matches"]
        and report["ledger_chain_valid"]
        and not report["tamper_detected"]
    )
    summary = (
        f"Independently verified {rel_path} (SHA-256 {str(report['disk_sha256'])[:12]}…)"
        if ok
        else f"Artifact verification FAILED for {rel_path}"
    )
    from .tools import infer_artifact_kind

    return _outcome(
        "sandbox.verify_artifact",
        ok=ok,
        summary=summary,
        output=json.dumps(report, indent=2),
        verified_sha256=report["disk_sha256"],
        artifact_path=rel_path if report["exists"] else None,
        artifact_kind=infer_artifact_kind(rel_path) if report["exists"] else None,
        policy_checks={
            "file_exists": bool(report["exists"]),
            "hash_matches": bool(report["hash_matches"]),
            "ledger_chain_valid": bool(report["ledger_chain_valid"]),
            "not_tampered": not bool(report["tamper_detected"]),
        },
        error=None if ok else "Disk SHA-256 or ledger chain did not verify.",
    )


async def workspace_git_diff(args: Mapping[str, Any], ctx: ToolContext) -> ToolOutcome:
    """Inspect repository working-tree status and diff statistics safely (read-only)."""
    rel_path = str(args.get("path", ".")).strip() or "."
    _ = resolve_safe_path(ctx.workspace_root, rel_path)
    safe_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}

    async def _run_git(*git_args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *git_args,
            cwd=str(ctx.workspace_root),
            env=safe_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        async with asyncio.timeout(5.0):
            out_b, _ = await proc.communicate()
        return redact_secrets(out_b[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")).strip()

    try:
        status_out, diff_out = await asyncio.gather(
            _run_git("status", "--short", "--", rel_path),
            _run_git("diff", "--stat", "--", rel_path),
        )
    except (OSError, TimeoutError) as exc:
        return _outcome(
            "workspace.git_diff",
            ok=False,
            summary="git diff inspection unavailable",
            error=redact_secrets(str(exc)),
        )

    combined = "\n".join(
        p for p in (f"[status]\n{status_out or '(clean)'}", f"[diff --stat]\n{diff_out}") if p
    )
    return _outcome(
        "workspace.git_diff",
        ok=True,
        summary=f"Inspected git status/diff under {rel_path}",
        output=combined,
    )


async def connector_search_tool(args: Mapping[str, Any], ctx: ToolContext) -> ToolOutcome:
    """Execute bounded read-only search against a supported L5 connector."""
    connector = str(args.get("connector", "github")).strip().lower()
    query = str(args.get("query", "")).strip()
    limit = int(args.get("limit", 5))
    res = await ctx.connector_client.search(connector, query, limit=limit)
    if not res.ok:
        return _outcome(
            "connector.search",
            ok=False,
            summary=f"Connector search {connector}: unavailable or failed",
            output=res.model_dump_json(indent=2),
            error=res.error,
        )
    lines = [
        f"[{item.connector}] {item.item_id} — {item.title}" + (f" ({item.url})" if item.url else "")
        for item in res.items
    ]
    return _outcome(
        "connector.search",
        ok=True,
        summary=f"Found {len(res.items)} hit(s) in {connector}",
        output="\n".join(lines) if lines else "No matching items returned.",
    )


async def connector_read_tool(args: Mapping[str, Any], ctx: ToolContext) -> ToolOutcome:
    """Fetch bounded UTF-8 text from one explicit L5 connector resource."""
    connector = str(args.get("connector", "github")).strip().lower()
    resource_id = str(args.get("resource_id", "")).strip()
    res = await ctx.connector_client.read(connector, resource_id)
    if not res.ok:
        return _outcome(
            "connector.read",
            ok=False,
            summary=f"Connector read {connector}/{resource_id}: unavailable or failed",
            error=res.error,
        )
    return _outcome(
        "connector.read",
        ok=True,
        summary=f"Read {connector}:{res.resource_id} ({len(res.content)} chars)",
        output=res.content,
    )


async def workspace_grep_tool(args: Mapping[str, Any], ctx: ToolContext) -> ToolOutcome:
    """Search repository or sandbox source files concurrently for a substring query."""
    query = str(args.get("query", "")).strip()
    if not query or len(query) > 120:
        raise SecurityValidationError("workspace.grep requires query of 1..120 characters.")
    rel = str(args.get("path", ".")).strip() or "."
    scope = str(args.get("scope") or "workspace").lower()
    if scope == "sandbox":
        ctx.sandbox_dir.mkdir(parents=True, exist_ok=True)
        base = ctx.sandbox_dir
    else:
        base = ctx.workspace_root
    limit = max(1, min(int(args.get("limit", 25)), 60))
    target = resolve_safe_path(base, rel)
    pattern = re.compile(re.escape(query), re.IGNORECASE)

    def _collect_files() -> list[Path]:
        if target.is_file():
            return [target]
        if not target.is_dir():
            return []
        candidates: list[Path] = []
        for root_str, dirs, files in os.walk(target):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            root_path = Path(root_str)
            for fname in sorted(files):
                if fname in _SECRET_FILENAMES or fname.startswith(".env"):
                    continue
                if Path(fname).suffix.lower() in {
                    ".py",
                    ".md",
                    ".js",
                    ".mjs",
                    ".ts",
                    ".html",
                    ".json",
                    ".toml",
                    ".yml",
                }:
                    candidates.append(root_path / fname)
                    if len(candidates) >= 250:
                        return candidates
        return candidates

    files = await asyncio.to_thread(_collect_files)
    matches: list[str] = []

    def _scan_chunk(chunk: list[Path]) -> list[str]:
        found_local: list[str] = []
        for path in chunk:
            try:
                raw = path.read_bytes()[:_MAX_READ_BYTES].decode("utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(raw.splitlines(), start=1):
                if pattern.search(line):
                    rel_file = str(path.relative_to(base))
                    found_local.append(f"{rel_file}:{lineno}: {redact_secrets(line.strip())}")
                    if len(found_local) >= limit:
                        return found_local
        return found_local

    chunk_size = max(1, (len(files) + 3) // 4)
    chunks = [files[i : i + chunk_size] for i in range(0, len(files), chunk_size)]
    chunk_outputs: list[list[str]] = [[] for _ in chunks]

    async def _run_chunk(idx: int, chunk: list[Path]) -> None:
        chunk_outputs[idx] = await asyncio.to_thread(_scan_chunk, chunk)

    async with asyncio.TaskGroup() as group:
        for idx, chunk in enumerate(chunks):
            group.create_task(_run_chunk(idx, chunk))

    for out in chunk_outputs:
        for line in out:
            matches.append(line)
            if len(matches) >= limit:
                break
        if len(matches) >= limit:
            break

    return _outcome(
        "workspace.grep",
        ok=True,
        summary=f"Found {len(matches)} match(es) for {query!r}",
        output="\n".join(matches),
    )


async def memory_rag_search_tool(args: Mapping[str, Any], ctx: ToolContext) -> ToolOutcome:
    """Query persistent Celia Memory SQLite RAG, indexing requested workspace docs if empty."""
    query = str(args.get("query", "")).strip()
    if not query:
        raise SecurityValidationError("memory.rag_search requires a non-empty query.")
    k = max(1, min(int(args.get("k", 5)), 12))
    raw_paths = args.get("paths")
    index_paths = (
        [str(p).strip() for p in raw_paths if str(p).strip()]
        if isinstance(raw_paths, list | tuple) and raw_paths
        else ["docs", "README.md"]
    )
    db_path = (ctx.sessions_root.parent / "celia_memory.sqlite3").resolve()
    ollama_client = OllamaRuntimeClient()
    backend = OllamaEmbeddingBackend(ollama_client) if ollama_client.is_configured() else None
    store = CeliaMemoryStore(db_path, backend=backend, allow_lexical_fallback=True)
    stats = await store.stats()
    if stats.total_chunks == 0:
        valid_targets = [p for p in index_paths if (ctx.workspace_root / p).exists()] or [
            "README.md"
        ]
        await store.index_paths(ctx.workspace_root, valid_targets, max_files=30)
    hits = await store.search(query, k=k)
    lines = [
        f"[{h.score:.3f} | {h.retrieval_mode} | {h.source_path} § {h.heading}] {h.snippet}"
        for h in hits
    ]
    return _outcome(
        "memory.rag_search",
        ok=True,
        summary=f"Celia RAG returned {len(hits)} hit(s)",
        output="\n".join(lines) if lines else "No matching Celia RAG chunks found.",
    )


__all__ = [
    "connector_read_tool",
    "connector_search_tool",
    "memory_rag_search_tool",
    "run_sandbox_tests",
    "verify_sandbox_artifact",
    "workspace_git_diff",
    "workspace_grep_tool",
]
