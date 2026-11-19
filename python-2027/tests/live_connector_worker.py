"""Worker: probe the real provider endpoints and print the evidence as JSON.

Used by ``scripts/live_connector_gate.py``. It exits 0 whenever it produced a
report — including "no key" and "provider error" — because the gate, not the
worker, decides the verdict. It exits 3 only when it cannot run at all.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # run as a script: make ``src`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai2027_protocol.ai_providers import (  # noqa: E402
    GeminiFreeProvider,
    create_groq_free_provider,
    create_nvidia_nim_provider,
    create_openrouter_free_provider,
)
from ai2027_protocol.live_probe import MARKER, ProbeableProvider, probe  # noqa: E402

CANDIDATES: dict[str, Callable[[Any], ProbeableProvider]] = {
    "gemini": lambda transport: GeminiFreeProvider(transport=transport),
    "nvidia": lambda transport: create_nvidia_nim_provider(transport=transport),
    "groq": lambda transport: create_groq_free_provider(transport=transport),
    "openrouter": lambda transport: create_openrouter_free_provider(transport=transport),
}


async def _run(providers: list[str], *, marker: str, timeout_s: float) -> dict[str, Any]:
    attempts = []
    for name in providers:
        attempt = await probe(name, CANDIDATES[name], marker=marker, timeout_s=timeout_s)
        attempts.append(attempt.as_dict())
    return {
        "marker": marker,
        "probed_providers": providers,
        "attempts": attempts,
        "python": sys.version.split()[0],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe live provider endpoints for the gate.")
    parser.add_argument("--provider", action="append", default=[], choices=sorted(CANDIDATES))
    parser.add_argument("--marker", default=MARKER)
    parser.add_argument("--timeout-s", type=float, default=45.0)
    args = parser.parse_args(argv)

    providers = args.provider or list(CANDIDATES)
    try:
        report = asyncio.run(_run(providers, marker=args.marker, timeout_s=args.timeout_s))
    except Exception as exc:  # noqa: BLE001 - the gate must see a reason, not a traceback
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 3
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
