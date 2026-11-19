#!/usr/bin/env python3
"""One manual E2B sandbox smoke test; the secret is never read from argv/output.

The only executed code is the constant ``print(40 + 2)``. E2BBackend is the same
fail-closed backend used by the project and asks E2B to kill the sandbox in its
``finally`` block. This proves one create/execute response, not production
isolation, resource limits, billing, or availability.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for package_parent in ("os_core", "os_tools"):
    sys.path.insert(0, str(PROJECT_ROOT / "packages" / package_parent))

from os_core.types import ToolStatus  # noqa: E402
from os_tools.code_exec import E2BBackend  # noqa: E402

SMOKE_CODE = "print(40 + 2)"
OK, FAILED, NO_KEY = 0, 1, 2


def check(
    *,
    env: dict[str, str] | None = None,
    backend_factory: Callable[[], E2BBackend] | None = None,
    runner: Callable = asyncio.run,
) -> dict:
    """Create one temporary sandbox, run a fixed expression, and report metadata."""
    env = os.environ if env is None else env
    key_present = bool(env.get("E2B_API_KEY", "").strip())
    result = {
        "provider": "e2b",
        "credential": "present" if key_present else "absent",
        "ok": False,
        "status": None,
        "error_kind": None,
        "latency_ms": None,
        "expected_output_matched": False,
        "claim": (
            "one E2B sandbox smoke test only; not a production isolation, billing, "
            "or availability certification"
        ),
    }
    if not key_present:
        result["error_kind"] = "no_key"
        return result

    backend = backend_factory() if backend_factory is not None else E2BBackend()
    started = time.monotonic()
    try:
        tool_result = runner(backend.execute(SMOKE_CODE, language="python", timeout=30.0))
    except Exception:
        # Do not print SDK exceptions: diagnostics can contain credential material.
        result["error_kind"] = "sandbox_error"
        result["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
        return result

    result["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
    result["status"] = getattr(tool_result.status, "value", str(tool_result.status))
    output_matches = (
        tool_result.status is ToolStatus.SUCCESS
        and tool_result.output.strip() == "42"
        and tool_result.metadata.get("isolation") == "e2b-microvm"
        and tool_result.metadata.get("executed") is True
    )
    result["expected_output_matched"] = output_matches
    result["ok"] = output_matches
    if not output_matches:
        result["error_kind"] = "sandbox_failed"
    return result


def main(
    argv: list[str] | None = None,
    env: dict[str, str] | None = None,
    backend_factory: Callable[[], E2BBackend] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="One fixed E2B microVM smoke test.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = check(env=env, backend_factory=backend_factory)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("E2B live check — one fixed expression in a temporary sandbox")
        print(f"  credential : {result['credential']}")
        if result["ok"]:
            print("  result     : sandbox ran the expected check")
            print(f"  latency    : {result['latency_ms']} ms")
        else:
            print(f"  result     : {result['error_kind']}")
            if result["status"]:
                print(f"  status     : {result['status']}")
        print(result["claim"])
    return OK if result["ok"] else (NO_KEY if result["error_kind"] == "no_key" else FAILED)


if __name__ == "__main__":
    raise SystemExit(main())
