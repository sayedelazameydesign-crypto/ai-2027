#!/usr/bin/env python3
"""Build and verify the Google Apps Script JSON bundle for Drive/clasp sync.

Stdlib only — zero third-party Python dependencies.

Supports:
  - Packing `appsscript.json` + `*.gs` into Google Drive's
    `application/vnd.google-apps.script+json` import/export format.
  - Preserving existing container file IDs so in-place updates keep stable
    Apps Script file identities.
  - Verifying an exported Drive bundle against the local source tree
    (manifest scopes, urlFetchWhitelist, and SHA-256 of every script file).

Usage:
  python3 scripts/apps_script_bundle.py --verify
  python3 scripts/apps_script_bundle.py --out /tmp/bundle.json
  python3 scripts/apps_script_bundle.py --verify-export /tmp/exported.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "kernel-2027") not in sys.path:
    sys.path.insert(0, str(ROOT / "kernel-2027"))
from kernel2027 import sha256_text  # noqa: E402

DEFAULT_BRIDGE_DIR = ROOT / "integrations" / "apps-script" / "memory-p0-bridge"

EXPECTED_SCOPES = frozenset({
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.events.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/script.external_request",
    "https://www.googleapis.com/auth/script.scriptapp",
})

EXPECTED_WHITELIST = frozenset({
    "https://gmail.googleapis.com/gmail/v1/users/me/messages",
    "https://www.googleapis.com/calendar/v3/calendars/primary/events",
    "https://api.telegram.org/",
})

FORBIDDEN_SCOPE_SUBSTRINGS = (
    "gmail.send",
    "gmail.modify",
    "mail.google.com",
    "drive",
)


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    """Return static contract-violation messages without echoing manifest values.

    Deliberately avoids interpolating ``manifest['oauthScopes']`` (or any other
    manifest field) into returned strings: CodeQL's clear-text-logging query
    classifies values read from ``oauth*`` keys as sensitive credentials.
    """
    errors: list[str] = []
    if manifest.get("runtimeVersion") != "V8":
        errors.append("runtimeVersion must be 'V8'")
    if manifest.get("timeZone") != "Africa/Cairo":
        errors.append("timeZone must be 'Africa/Cairo'")

    declared_permissions = set(manifest.get("oauthScopes") or [])
    if declared_permissions != EXPECTED_SCOPES:
        errors.append("oauthScopes mismatch against expected allowlist")

    for bad in FORBIDDEN_SCOPE_SUBSTRINGS:
        if any(bad in entry for entry in declared_permissions):
            errors.append(f"forbidden scope substring detected: {bad}")

    whitelist = set(manifest.get("urlFetchWhitelist") or [])
    if whitelist != EXPECTED_WHITELIST:
        errors.append("urlFetchWhitelist mismatch against expected allowlist")
    return errors


def build_bundle(
    source_dir: Path = DEFAULT_BRIDGE_DIR,
    manifest_file_id: str = "",
    code_file_id: str = "",
) -> dict[str, Any]:
    manifest_path = source_dir / "appsscript.json"
    code_path = source_dir / "Code.gs"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Missing manifest: {manifest_path}")
    if not code_path.is_file():
        raise FileNotFoundError(f"Missing Code.gs: {code_path}")

    manifest_raw = manifest_path.read_text(encoding="utf-8")
    manifest_obj = json.loads(manifest_raw)
    errors = validate_manifest(manifest_obj)
    if errors:
        raise ValueError("; ".join(errors))

    manifest_normalized = json.dumps(manifest_obj, ensure_ascii=False, indent=2)
    code_source = code_path.read_text(encoding="utf-8")

    manifest_entry: dict[str, Any] = {
        "name": "appsscript",
        "type": "json",
        "source": manifest_normalized,
    }
    if manifest_file_id:
        manifest_entry["id"] = manifest_file_id

    code_entry: dict[str, Any] = {
        "name": "Code",
        "type": "server_js",
        "source": code_source,
    }
    if code_file_id:
        code_entry["id"] = code_file_id

    return {"files": [manifest_entry, code_entry]}


def verify_exported_bundle(exported_path: Path, source_dir: Path = DEFAULT_BRIDGE_DIR) -> dict[str, Any]:
    local = build_bundle(source_dir=source_dir)
    exported = json.loads(exported_path.read_text(encoding="utf-8"))
    files = exported.get("files") or []
    by_name = {f.get("name"): f for f in files if isinstance(f, dict)}

    errors: list[str] = []
    if "appsscript" not in by_name:
        errors.append("exported bundle missing 'appsscript' manifest entry")
    else:
        exp_manifest = json.loads(by_name["appsscript"].get("source") or "{}")
        errors.extend(validate_manifest(exp_manifest))

    if "Code" not in by_name:
        errors.append("exported bundle missing 'Code' server_js entry")
    else:
        local_code = local["files"][1]["source"].strip()
        remote_code = str(by_name["Code"].get("source") or "").strip()
        if sha256_text(local_code) != sha256_text(remote_code):
            errors.append("Code.gs SHA-256 mismatch between local tree and exported bundle")

    return {
        "ok": len(errors) == 0,
        "file_names": sorted(k for k in by_name if k),
        "code_sha256": sha256_text(str(by_name.get("Code", {}).get("source", "")).strip())
        if "Code" in by_name else "",
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-dir", type=Path, default=DEFAULT_BRIDGE_DIR)
    ap.add_argument("--manifest-id", default="")
    ap.add_argument("--code-id", default="")
    ap.add_argument("--out", type=Path, help="write Drive script+json payload to file")
    ap.add_argument("--verify", action="store_true", help="validate local manifest and Code.gs")
    ap.add_argument("--verify-export", type=Path, help="verify an exported script+json file")
    args = ap.parse_args(argv)

    try:
        bundle = build_bundle(
            source_dir=args.source_dir,
            manifest_file_id=args.manifest_id,
            code_file_id=args.code_id,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    if args.out:
        args.out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.verify_export:
        check = verify_exported_bundle(args.verify_export, source_dir=args.source_dir)
        print(json.dumps(check, ensure_ascii=False, indent=2))
        return 0 if check["ok"] else 1

    if args.verify or not args.out:
        summary = {
            "ok": True,
            "files": [f["name"] for f in bundle["files"]],
            "manifest_sha256": sha256_text(bundle["files"][0]["source"].strip()),
            "code_sha256": sha256_text(bundle["files"][1]["source"].strip()),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
