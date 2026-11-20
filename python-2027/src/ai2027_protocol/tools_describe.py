"""The tool that makes every file family visible to the model.

`workspace.index_file` maps *text*. Measured on a Parquet file it refused -- by
design, because a columnar body has no headings. That refusal was honest but
useless: the pipeline still had no way to learn that a file contained three
tables and 4,000 rows without handing the whole binary to a parser.

This module is the missing half: one tool, `workspace.describe_file`, that asks
the IR layer for whatever the family honestly allows, and returns a report that
costs a fraction of the file. A model that calls it learns a database's schema
or a container's directory for ~200 characters, then queries the slice it needs.

Lives here rather than in `tools.py` for the reason recorded in `tools_text.py`:
the repository's own rule 2 caps a file near 650 lines, and adding handlers to
one module is exactly how that rule got broken before.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .ir import describe_file
from .security import SecurityValidationError, redact_secrets, resolve_safe_path
from .tools import (
    _SECRET_FILENAMES,
    ToolContext,
    ToolOutcome,
)


async def describe_file_tool(args: Mapping[str, Any], ctx: ToolContext) -> ToolOutcome:
    """Describe a file's structure whatever its family, without loading it.

    The work runs in a thread because a SQLite probe does real I/O: a 182 MB
    database took 49 ms, which is fine off the event loop and not fine on it.
    """
    rel = str(args.get("path", "")).strip()
    if not rel:
        raise SecurityValidationError("workspace.describe_file requires non-empty path.")
    if Path(rel).name in _SECRET_FILENAMES or Path(rel).name.startswith(".env"):
        raise SecurityValidationError("Direct reading of secret environment files is forbidden.")
    base = _select_base(args.get("scope"), ctx)
    target = resolve_safe_path(base, rel)

    described = await asyncio.to_thread(describe_file, str(target), display_path=rel)
    rendered = described.render()
    ok = described.kind != "missing"

    if described.text_index is not None:
        summary = (
            f"Described {rel}: {described.kind}, "
            f"{described.text_index.section_count} section(s), "
            f"{described.text_index.total_lines} lines"
        )
    elif described.tables:
        total = sum(table.rows or 0 for table in described.tables)
        summary = (
            f"Described {rel}: {described.kind}, {len(described.tables)} table(s), {total:,} row(s)"
        )
    else:
        summary = f"Described {rel}: {described.kind} ({described.family.value})"

    return ToolOutcome(
        tool="workspace.describe_file",
        ok=ok,
        summary=summary,
        output=redact_secrets(rendered),
        artifact_path=rel,
    )


def _select_base(scope: object, ctx: ToolContext) -> Path:
    """Resolve the root a tool call is allowed to touch."""
    return ctx.sandbox_dir if str(scope or "").strip() == "sandbox" else ctx.workspace_root
