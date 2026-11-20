"""Sandboxed execution, split out of `tools.py` to keep one file under the size rule.

`tools.py` was 610 lines before the ingest work and 662 after it. The
repository's compliance rule 2 refuses any file over 650, so the project's
self-audit score fell from 100% to 97% — caught by
`test_auditor_scores_python_2027_at_full_compliance`, which is a better guard
than a reviewer's eye.

The rule's reasoning is that a 650-line file of unrelated handlers cannot be
held in one head. Moving the 83-line execution handler out is the fix that
respects the rule rather than the fix that relaxes it.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from .security import SecurityValidationError, redact_secrets, resolve_safe_path
from .tools import ToolContext, ToolOutcome

_MAX_PROCESS_OUTPUT_BYTES = 20_000


#: The artifact writer this handler delegates to, typed as what it is: a
#: coroutine function taking args and a context. `Any` would have silenced
#: ruff but lost the check that the caller passes the right thing.
WriteArtifact = Callable[[Mapping[str, Any], ToolContext], Awaitable[ToolOutcome]]


async def run_python_tool(
    args: Mapping[str, Any], ctx: ToolContext, write_artifact: WriteArtifact
) -> ToolOutcome:
    """Run a script inside the sandbox with a sanitized environment."""
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
        write_outcome = await write_artifact({"path": rel_path, "content": str(code_raw)}, ctx)
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
