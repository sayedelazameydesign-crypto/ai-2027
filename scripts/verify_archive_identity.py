#!/usr/bin/env python3
"""Verify that projects/ is a byte-identical mirror of its 48 upstream repos.

Compares git blob SHAs: the local tree (via ``git ls-tree``) against the
upstream tree (via the GitHub API). A blob SHA is the SHA-1 of the content, so
"same SHA" means "same bytes" — no downloads, no heuristics.

Documented deviations (docs/DEVIATIONS.md) are reported separately instead of
being hidden in the "identical" count.

    python3 scripts/verify_archive_identity.py            # summary table
    python3 scripts/verify_archive_identity.py --strict   # exit 1 on any drift

Requires: git, the ``gh`` CLI (authenticated), network access to api.github.com.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Files we deliberately changed and therefore expect to differ (docs/DEVIATIONS.md).
EXPECTED_DEVIATIONS = {
    "agi-system": {"core/policy.py"},          # D-1 cross-platform hardening
    "ai-store2": {"env.local"},                # D-2 exposed secret redacted
}


def token() -> str:
    p = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit("gh CLI not authenticated — run: gh auth login")
    return p.stdout.strip()


def api(url: str, tok: str) -> dict:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {tok}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "ai-2027-identity-check",
    })
    with urllib.request.urlopen(req, timeout=60) as f:
        return json.load(f)


def local_blobs(project: str) -> dict[str, str]:
    out = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-tree", "-r", "HEAD", "--", f"projects/{project}"],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout
    blobs: dict[str, str] = {}
    for line in out.splitlines():
        meta, path = line.split("\t", 1)
        blobs[path[len(f"projects/{project}/"):]] = meta.split()[2]
    return blobs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="exit 1 if any unexpected drift")
    args = ap.parse_args()

    catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
    tok = token()

    identical, deviated, drifted, failed = [], [], [], []
    for entry in catalog["projects"]:
        name = entry["name"]
        repo = entry["source"].rstrip("/").split("/")[-1]
        try:
            info = api(f"https://api.github.com/repos/sayedelazameydesign-crypto/"
                       f"{urllib.parse.quote(repo)}", tok)
            tree = api(f"https://api.github.com/repos/sayedelazameydesign-crypto/"
                       f"{urllib.parse.quote(repo)}/git/trees/"
                       f"{info['default_branch']}?recursive=1", tok)
        except Exception as exc:  # noqa: BLE001
            failed.append((name, str(exc)[:70]))
            continue
        upstream = {t["path"]: t["sha"] for t in tree.get("tree", []) if t["type"] == "blob"}
        local = local_blobs(name)

        diff = sorted(k for k, v in local.items() if k in upstream and upstream[k] != v)
        gone = sorted(k for k in local if k not in upstream)
        # upstream-only junk (e.g. __pycache__ pushed by accident) is not our drift
        extra = sorted(k for k in upstream
                       if k not in local and "__pycache__" not in k and not k.endswith(".pyc"))

        expected = EXPECTED_DEVIATIONS.get(name, set())
        unexpected_diff = [k for k in diff if k not in expected]
        if not diff and not gone and not extra:
            identical.append(name)
        elif diff and set(diff) <= expected and not gone and not extra:
            deviated.append((name, diff))
        else:
            drifted.append((name, diff, gone, extra))

    total = len(catalog["projects"])
    print(f"\n{'='*66}\nARCHIVE IDENTITY — {total} projects\n{'='*66}")
    print(f"  byte-identical      : {len(identical)}")
    print(f"  documented deviation: {len(deviated)}"
          + (f"  ({', '.join(n for n, _ in deviated)})" if deviated else ""))
    print(f"  drifted             : {len(drifted)}")
    print(f"  API failures        : {len(failed)}")
    for name, diff, gone, extra in drifted:
        print(f"\n  ! {name}: {len(diff)} changed, {len(gone)} gone, {len(extra)} upstream-only")
        for label, items in (("changed", diff), ("gone", gone), ("upstream-only", extra)):
            for k in items[:3]:
                print(f"      {label}: {k}")
    for name, err in failed:
        print(f"\n  ! {name}: {err}")

    matches = not drifted and not failed
    print(f"\n  => {'48/48 accounted for' if matches else 'DRIFT/BLOCKED — see above'}")
    if args.strict and not matches:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
