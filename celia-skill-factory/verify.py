#!/usr/bin/env python3
"""One-command verification for the CELIA reference core. $0, stdlib only.

    python3 celia-skill-factory/verify.py

Runs the test suite, checks the SVG parses, and prints a summary suitable
for pasting into a report. Exit code 0 only if every check passes.
"""
from __future__ import annotations

import os
import sys
import unittest
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    sys.path.insert(0, ROOT)

    print("CELIA Skill Factory — verification")
    print("=" * 56)

    # 1) tests
    loader = unittest.TestLoader()
    suite = loader.discover(os.path.join(ROOT, "tests"), top_level_dir=ROOT)
    result = unittest.TextTestRunner(verbosity=1).run(suite)

    tests_ok = result.wasSuccessful()
    ran = result.testsRun
    failed = len(result.failures)
    errored = len(result.errors)

    # 2) SVG parses
    svg_path = os.path.join(ROOT, "docs", "celia-skill-factory-architecture.svg")
    try:
        ET.parse(svg_path)
        svg_ok = True
    except Exception as exc:  # noqa: BLE001 - report, do not crash
        svg_ok = False
        print(f"SVG INVALID: {exc}")

    # 3) declared surface: the four required artefacts exist
    required = [
        "CELIASKILL.md",
        "docs/adr/0006-celia-skill-factory.md",
        "docs/celia-skill-factory-architecture.svg",
        "docs/celia-skill-factory-acceptance.md",
    ]
    present = [p for p in required if os.path.isfile(os.path.join(ROOT, p))]
    files_ok = len(present) == len(required)

    print()
    print("=" * 56)
    print(f"tests      : {ran} run · {failed} failed · {errored} errors  -> {'OK' if tests_ok else 'FAIL'}")
    print(f"svg        : {'valid XML' if svg_ok else 'INVALID'}")
    print(f"artefacts  : {len(present)}/{len(required)} present -> {'OK' if files_ok else 'MISSING'}")
    ok = tests_ok and svg_ok and files_ok
    print(f"result     : {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
