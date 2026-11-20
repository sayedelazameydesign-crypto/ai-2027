"""The tool that refuses to call an artifact done because a writer said so.

`tools_advanced.verify_sandbox_artifact` answers a different question: did the
file change since we hashed it, and is the hash chain intact? That is integrity
against tampering. It says nothing about whether the bytes are a *valid* docx,
which is the failure that actually reaches a user: a ZIP with a perfect
directory and a corrupt body passes every hash check ever written.

This module exposes the layer-5 verifier as a tool, so the same check that runs
in the pipeline can be run on demand by a model that just wrote something.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from .security import SecurityValidationError, redact_secrets, resolve_safe_path
from .tools import ToolContext, ToolOutcome
from .verify import verify_file


async def validate_artifact_tool(args: Mapping[str, Any], ctx: ToolContext) -> ToolOutcome:
    """Re-open a sandbox artifact and report what each check actually found."""
    rel = str(args.get("path", "")).strip()
    if not rel:
        raise SecurityValidationError("sandbox.validate_artifact requires non-empty path.")
    target = resolve_safe_path(ctx.sandbox_dir, rel)
    kind = str(args.get("kind", "")).strip()

    verdict = await asyncio.to_thread(verify_file, target, display_path=rel, kind=kind)
    if verdict.ok:
        summary = f"{rel} is valid as {verdict.kind}"
    else:
        first = verdict.failures[0].name if verdict.failures else "no check ran"
        summary = f"{rel} FAILED validation: {first}"
    return ToolOutcome(
        tool="sandbox.validate_artifact",
        ok=verdict.ok,
        summary=summary,
        output=redact_secrets(verdict.render()),
        verified_sha256=verdict.sha256 or None,
        artifact_path=rel,
        artifact_kind=verdict.kind,
        error=None if verdict.ok else "; ".join(f"{c.name}: {c.detail}" for c in verdict.failures),
    )
