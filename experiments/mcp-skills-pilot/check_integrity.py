#!/usr/bin/env python3
"""Verify the pinned npm integrity against the registry's dist.integrity (F-07).

Compares the exact sha512 recorded in README.md and both SKILL.md files
with what https://registry.npmjs.org publishes for
@modelcontextprotocol/server-filesystem@2.4.0. Full-value comparison
(never truncated). Requires network access to registry.npmjs.org.

Run:  python3 experiments/mcp-skills-pilot/check_integrity.py
Exit: 0 = PASS, 1 = FAIL, 2 = registry unreachable (treated as NOT verified).
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

PACKAGE = "@modelcontextprotocol/server-filesystem"
VERSION = "2.4.0"
PINNED = (
    "sha512-BoT8Gws5MsetfAey4vDyuzy8WYQES6mGYLSShuqSNGAC0X0NBV4p78/"
    "R78KfW4RkxtWz+zxhS9jwhzJrQUTt2Q=="
)
REGISTRY_URL = (
    "https://registry.npmjs.org/"
    + PACKAGE.replace("/", "%2f")
)


def main() -> int:
    request = urllib.request.Request(REGISTRY_URL, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            metadata = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"NOT VERIFIED: registry unreachable: {type(exc).__name__}: {exc}")
        return 2
    try:
        live = metadata["versions"][VERSION]["dist"]["integrity"]
    except KeyError:
        print(f"NOT VERIFIED: {PACKAGE}@{VERSION} missing from registry metadata")
        return 2
    if live == PINNED:
        print(f"PASS: {PACKAGE}@{VERSION} dist.integrity matches the pinned value")
        return 0
    print("FAIL: integrity mismatch")
    print(f"  pinned: {PINNED}")
    print(f"  live:   {live}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
