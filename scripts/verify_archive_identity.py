#!/usr/bin/env python3
"""Verify that projects/ is a byte-identical mirror of its 48 upstream repos.

Compares git blob SHAs: the local tree (via ``git ls-tree``) against the
upstream tree (via the GitHub API). A blob SHA is the SHA-1 of the content, so
"same SHA" means "same bytes" — no downloads, no heuristics.

Three outcomes are kept apart, because collapsing them is how an archive starts
lying about itself:

* **byte-identical** — same blob SHAs every file.
* **documented deviation** — a file we changed on purpose (``D-1``, ``D-2``).
* **generated** — a file the *upstream* regenerates on its own (timestamps,
  ``run_id``). Its blob can differ within hours without anybody touching it, so
  it is reported, not counted as drift. The 2026-11-18 run caught exactly this:
  ``easy-ai/data/manifest.json`` was re-generated upstream 9 hours after we
  synced it.
* **drifted** — anything else. This is the only outcome that is a defect.
* **blocked** — the API could not answer *and* preflight proved this
  environment cannot reach it. Blindness is not drift (``network_preflight.py``).

The register of documented deviations and generated files is machine-readable:
``docs/deviations.json``. If it is missing the built-in defaults apply, so an
archived copy of this repository still verifies.

    python3 scripts/verify_archive_identity.py            # summary table
    python3 scripts/verify_archive_identity.py --json     # machine-readable
    python3 scripts/verify_archive_identity.py --strict   # exit 1 on drift
    python3 scripts/verify_archive_identity.py --strict-all  # + blocked/failed

Requires: git, the ``gh`` CLI (authenticated), network access to api.github.com.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTER = ROOT / "docs" / "deviations.json"
sys.path.insert(0, str(ROOT / "scripts"))
import network_preflight  # noqa: E402  (same-directory helper, stdlib only)

# Fallback if docs/deviations.json is unreadable (kept in sync with that file).
DEFAULT_REGISTER = {
    "deviations": {
        "agi-system": ["core/policy.py"],   # D-1 cross-platform hardening
        "ai-store2": ["env.local"],         # D-2 exposed secret redacted
    },
    "generated": {
        "easy-ai": ["data/manifest.json"],  # regenerated upstream (timestamps)
    },
}

IDENTICAL, DEVIATION, GENERATED, DRIFTED, BLOCKED = (
    "identical", "deviation", "generated", "drifted", "blocked")


def load_register(path: Path = REGISTER) -> tuple[dict, str]:
    """(register, source) — never raises: a broken register must not stop a check."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return DEFAULT_REGISTER, f"built-in defaults ({type(exc).__name__})"
    if not isinstance(data, dict):
        return DEFAULT_REGISTER, "built-in defaults (register is not an object)"
    reg = {
        "deviations": {k: list(v) for k, v in (data.get("deviations") or {}).items()},
        "generated": {k: list(v) for k, v in (data.get("generated") or {}).items()},
    }
    return reg, str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def classify(name: str, diff: list[str], gone: list[str], extra: list[str],
             register: dict) -> dict:
    """One project's verdict. Pure function — every branch is unit-tested."""
    deviations = set(register.get("deviations", {}).get(name, []))
    generated = set(register.get("generated", {}).get(name, []))
    unexpected = [k for k in diff if k not in deviations and k not in generated]

    result = {"project": name, "status": IDENTICAL, "changed": list(diff),
              "gone": list(gone), "upstream_only": list(extra),
              "documented": sorted(k for k in diff if k in deviations),
              "generated": sorted(k for k in diff if k in generated),
              "unexpected": unexpected}

    if gone or extra or unexpected:
        result["status"] = DRIFTED
        return result
    if not diff:
        return result
    if all(k in deviations for k in diff):
        result["status"] = DEVIATION
    elif all(k in generated for k in diff):
        result["status"] = GENERATED
    else:                      # a mix of documented and generated files
        result["status"] = DEVIATION
        result["mixed"] = True
    return result


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


def failure_kind(exc: Exception) -> tuple[str, str]:
    """'blocked' when this environment is blind, 'failed' when upstream erred."""
    report = network_preflight.cached_preflight()
    if isinstance(exc, urllib.error.HTTPError):
        # A 5xx means the API *did* answer. That is an upstream fault, not blindness.
        return "failed", f"HTTP {exc.code} from api.github.com"
    if not report.allows("https://api.github.com/"):
        return "blocked", f"{type(exc).__name__}: no egress ({report.egress})"
    return "failed", f"{type(exc).__name__}: {str(exc)[:60]}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 on unexpected drift (generated/documented excluded)")
    ap.add_argument("--strict-all", action="store_true",
                    help="exit 1 also on blocked/API-failed projects")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--no-preflight", action="store_true",
                    help="do not measure egress when an API call fails")
    args = ap.parse_args(argv)

    catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
    register, source = load_register(REGISTER)
    tok = token()

    results: list[dict] = []
    blocked: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []

    for entry in catalog["projects"]:
        name = entry["name"]
        repo = entry["source"].rstrip("/").split("/")[-1]
        try:
            info = api(f"https://api.github.com/repos/sayedelazameydesign-crypto/"
                       f"{urllib.parse.quote(repo)}", tok)
            tree = api(f"https://api.github.com/repos/sayedelazameydesign-crypto/"
                       f"{urllib.parse.quote(repo)}/git/trees/"
                       f"{info['default_branch']}?recursive=1", tok)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
            if args.no_preflight and not isinstance(exc, urllib.error.HTTPError):
                blocked.append((name, f"{type(exc).__name__}: {str(exc)[:60]}"))
            else:
                kind, why = failure_kind(exc)
                (blocked if kind == "blocked" else failed).append((name, why))
            results.append({"project": name, "status": BLOCKED, "reason": why,
                            "changed": [], "gone": [], "upstream_only": [],
                            "documented": [], "generated": [], "unexpected": []})
            continue

        upstream = {t["path"]: t["sha"] for t in tree.get("tree", []) if t["type"] == "blob"}
        local = local_blobs(name)

        diff = sorted(k for k, v in local.items() if k in upstream and upstream[k] != v)
        gone = sorted(k for k in local if k not in upstream)
        # upstream-only junk (e.g. __pycache__ pushed by accident) is not our drift
        extra = sorted(k for k in upstream
                       if k not in local and "__pycache__" not in k and not k.endswith(".pyc"))
        results.append(classify(name, diff, gone, extra, register))

    counts = {k: 0 for k in (IDENTICAL, DEVIATION, GENERATED, DRIFTED, BLOCKED)}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    total = len(catalog["projects"])
    drifted = [r for r in results if r["status"] == DRIFTED]
    generated = [r for r in results if r["status"] == GENERATED]

    if args.json:
        print(json.dumps({"total": total, "counts": counts, "register": source,
                          "results": results,
                          "blocked": [{"project": n, "reason": w} for n, w in blocked],
                          "failed": [{"project": n, "reason": w} for n, w in failed]},
                         ensure_ascii=False, indent=2))
    else:
        print(f"\n{'='*70}\nARCHIVE IDENTITY — {total} projects\n{'='*70}")
        print(f"  register            : {source}")
        print(f"  byte-identical      : {counts[IDENTICAL]}")
        dev = [r['project'] for r in results if r['status'] == DEVIATION]
        print(f"  documented deviation: {counts[DEVIATION]}"
              + (f"  ({', '.join(dev)})" if dev else ""))
        gen = [f"{r['project']}: {', '.join(r['generated'])}" for r in generated]
        print(f"  generated (volatile): {counts[GENERATED]}"
              + (f"  ({'; '.join(gen)})" if gen else ""))
        print(f"  drifted             : {counts[DRIFTED]}")
        print(f"  blocked (no egress) : {counts[BLOCKED]}")
        print(f"  API failures        : {len(failed)}")
        for r in drifted:
            print(f"\n  ! {r['project']}: {len(r['changed'])} changed, "
                  f"{len(r['gone'])} gone, {len(r['upstream_only'])} upstream-only")
            for label, items in (("changed", r["unexpected"]), ("gone", r["gone"]),
                                 ("upstream-only", r["upstream_only"])):
                for k in items[:3]:
                    print(f"      {label}: {k}")
        for name, why in failed:
            print(f"\n  ! {name}: {why}")
        for name, why in blocked:
            print(f"\n  ~ {name}: BLOCKED — {why}")
        accounted = counts[IDENTICAL] + counts[DEVIATION] + counts[GENERATED] \
            + counts[DRIFTED] + counts[BLOCKED]
        head = "no unexpected drift" if not drifted else "DRIFT"
        print(f"\n  => {accounted}/{total} accounted for · {head}")

    if args.strict and drifted:
        return 1
    if args.strict_all and (drifted or failed or blocked):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
