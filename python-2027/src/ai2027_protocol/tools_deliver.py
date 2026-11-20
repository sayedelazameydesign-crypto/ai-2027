"""The tool that answers "what do I hand the user, and in what form?".

Split out for the reason recorded in `tools_text.py`: `tools.py` is near the
repository's 650-line rule, and a handler added there is how that rule broke
before.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from .delivery import plan_delivery
from .ir import describe_file
from .security import SecurityValidationError, redact_secrets, resolve_safe_path
from .tools import ToolContext, ToolOutcome
from .verify import verify_file


async def plan_delivery_tool(args: Mapping[str, Any], ctx: ToolContext) -> ToolOutcome:
    """Describe an artifact, verify it, and say how to hand it over.

    Both steps run in the thread: describing a database and running SQLite's
    integrity check are real I/O, and the point of this tool is to be safe to
    call on whatever the system just produced.
    """
    rel = str(args.get("path", "")).strip()
    if not rel:
        raise SecurityValidationError("sandbox.plan_delivery requires non-empty path.")
    target = resolve_safe_path(ctx.sandbox_dir, rel)

    def _plan() -> tuple[str, bool, str, str]:
        described = describe_file(str(target), display_path=rel)
        verification = verify_file(target, display_path=rel, kind=described.kind)
        plan = plan_delivery(described, verification=verification)
        body = plan.render() + "\n\n" + verification.render()
        return plan.primary.form.value, plan.is_deliverable, plan.primary.reason, body

    form, deliverable, reason, body = await asyncio.to_thread(_plan)
    return ToolOutcome(
        tool="sandbox.plan_delivery",
        ok=deliverable,
        summary=f"{rel}: deliver as {form} — {reason}"
        if deliverable
        else f"{rel}: not deliverable — {reason}",
        output=redact_secrets(body),
        artifact_path=rel,
    )
