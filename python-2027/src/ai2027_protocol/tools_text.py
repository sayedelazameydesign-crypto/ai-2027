"""Tool handlers that describe files instead of reading them.

Split out of `tools.py` for a measured reason: that module was 610 lines, and
the repository's own compliance rule 2 flags any file over ~500. Adding the
index handler pushed it to 694 and dropped the project's self-audit score from
100% to 97% — caught by `test_auditor_scores_python_2027_at_full_compliance`,
not by reading the code.

The split is the fix, not an exemption: the rule is right that a 700-line
module of unrelated handlers is hard to hold in your head, so the handler that
is genuinely a different concern moved out.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .index import build_index
from .ingest import probe_bytes
from .security import SecurityValidationError, redact_secrets, resolve_safe_path
from .tools import (
    _MAX_READ_BYTES,
    _SECRET_FILENAMES,
    ToolContext,
    ToolOutcome,
)


async def index_file_tool(args: Mapping[str, Any], ctx: ToolContext) -> ToolOutcome:
    """Describe a file's structure so a read can be targeted, not blind."""
    rel = str(args.get("path", "")).strip()
    if not rel:
        raise SecurityValidationError("workspace.index_file requires non-empty path.")
    if Path(rel).name in _SECRET_FILENAMES or Path(rel).name.startswith(".env"):
        raise SecurityValidationError("Direct reading of secret environment files is forbidden.")
    base = _select_base(args.get("scope"), ctx)
    target = resolve_safe_path(base, rel)

    def _index() -> tuple[str, int, int]:
        if not target.is_file():
            raise FileNotFoundError(f"File not found: {rel}")
        head = target.read_bytes()[: _MAX_READ_BYTES * 8]
        probe = probe_bytes(head)
        if not probe.is_textual:
            raise SecurityValidationError(
                f"{rel} is not text ({probe.format}); a structural index needs "
                f"a textual body. Use the {probe.family.value} reader."
            )
        text = head.decode("utf-8", errors="replace")
        document = build_index(rel, text)
        return document.render(), document.total_lines, document.section_count

    rendered, total_lines, sections = await asyncio.to_thread(_index)
    return ToolOutcome(
        tool="workspace.index_file",
        ok=True,
        summary=f"Indexed {rel}: {total_lines} lines, {sections} section(s)",
        output=redact_secrets(rendered),
        artifact_path=rel,
    )


def _select_base(scope: object, ctx: ToolContext) -> Path:
    """Resolve the root a tool call is allowed to touch."""
    return ctx.sandbox_dir if str(scope or "").strip() == "sandbox" else ctx.workspace_root
