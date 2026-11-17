#!/usr/bin/env python3
"""CycloneDX SBOM for a zero-dependency repository — honest about what "empty" means.

Most SBOM tools answer "which third-party packages do you ship?". This repository
ships none, so the useful question is different:

  1. **Which GitHub Actions run in CI, at which commit SHA?** Every action is a
     supply-chain dependency with a mutable ref if it is used by tag. The repo
     policy pins to full SHAs; this script proves it mechanically.
  2. **What is the artifact itself?** (name, version, commit, build timestamp)

Output: CycloneDX 1.5 JSON on stdout (or `--out`). Deterministic: no clock except
`--timestamp`, no network, no third-party imports.

    python3 scripts/sbom.py --out sbom.json
    python3 scripts/sbom.py --assert-pinned     # exits 1 if any action is not SHA-pinned

Exit codes: 0 ok, 1 policy violation (unpinned action / missing file).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
VERSION = "1.0.0"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
USE_RE = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.MULTILINE)


def repo_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def python_inventory() -> dict:
    files = sorted(p for p in ROOT.rglob("*.py") if ".git" not in p.parts)
    total_bytes = sum(p.stat().st_size for p in files)
    h = hashlib.sha256()
    for p in files:
        h.update(str(p.relative_to(ROOT)).encode())
        h.update(p.read_bytes())
    return {"files": len(files), "bytes": total_bytes, "sha256": h.hexdigest()}


def actions_inventory() -> tuple[list[dict], list[dict]]:
    """Every `uses:` in every workflow, with an explicit pinned/unpinned verdict."""
    pinned, unpinned = [], []
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        text = wf.read_text(encoding="utf-8")
        for ref in USE_RE.findall(text):
            if ref.startswith("./"):          # local action — no supply chain
                continue
            name, _, version = ref.partition("@")
            entry = {"workflow": wf.name, "name": name,
                     "ref": version or "", "pinned": bool(SHA_RE.match(version or ""))}
            (pinned if entry["pinned"] else unpinned).append(entry)
    return pinned, unpinned


def build(timestamp: str) -> dict:
    pinned, unpinned = actions_inventory()
    commit = repo_commit()
    inv = python_inventory()
    components = [{
        "type": "application",
        "bom-ref": f"pkg:github/sayedelazameydesign-crypto/ai-2027@{commit}",
        "name": "ai-2027",
        "version": VERSION,
        "hashes": [{"alg": "SHA-256", "content": inv["sha256"]}],
        "properties": [
            {"name": "python_files", "value": str(inv["files"])},
            {"name": "python_bytes", "value": str(inv["bytes"])},
            {"name": "third_party_python_dependencies", "value": "0"},
        ],
    }]
    seen = set()
    for a in pinned:
        key = (a["name"], a["ref"])
        if key in seen:
            continue
        seen.add(key)
        components.append({
            "type": "library",
            "bom-ref": f"pkg:githubactions/{a['name']}@{a['ref']}",
            "name": a["name"],
            "version": a["ref"],
            "properties": [{"name": "pinned_to_sha", "value": "true"}],
        })
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "timestamp": timestamp,
            "tools": [{"vendor": "ai-2027", "name": "scripts/sbom.py", "version": "1.0.0"}],
            "component": components[0],
            "properties": [
                {"name": "repo_commit", "value": commit},
                {"name": "actions_pinned", "value": str(len(pinned))},
                {"name": "actions_unpinned", "value": str(len(unpinned))},
            ],
        },
        "components": components[1:],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="CycloneDX SBOM (stdlib only).")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    ap.add_argument("--timestamp", default="1970-01-01T00:00:00Z",
                    help="fixed by default so the SBOM is reproducible")
    ap.add_argument("--assert-pinned", action="store_true",
                    help="fail if any GitHub Action is not pinned to a full SHA")
    args = ap.parse_args()

    _, unpinned = actions_inventory()
    if args.assert_pinned and unpinned:
        for a in unpinned:
            print(f"UNPINNED ACTION  {a['workflow']}: {a['name']}@{a['ref']}",
                  file=sys.stderr)
        print(f"FAIL: {len(unpinned)} action reference(s) not pinned to a full SHA",
              file=sys.stderr)
        return 1

    body = json.dumps(build(args.timestamp), indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(body, encoding="utf-8")
        pinned, _ = actions_inventory()
        print(f"wrote {args.out} · {len(pinned)} pinned action components · "
              f"0 third-party python dependencies")
    else:
        sys.stdout.write(body)
    if args.assert_pinned:
        print("PASS: every GitHub Action is pinned to a full-length SHA")
    return 0


if __name__ == "__main__":
    sys.exit(main())
