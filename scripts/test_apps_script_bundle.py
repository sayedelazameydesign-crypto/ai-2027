#!/usr/bin/env python3
"""Tests for scripts/apps_script_bundle.py + Python <-> Apps Script SHA-256 interop."""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import apps_script_bundle as asb  # noqa: E402


def _ensure_stub(name: str, **attrs: object) -> None:
    try:
        __import__(name)
        return
    except ImportError:
        pass
    parts = name.split(".")
    for i in range(1, len(parts) + 1):
        pkg = ".".join(parts[:i])
        if pkg not in sys.modules:
            sys.modules[pkg] = types.ModuleType(pkg)
        if i > 1:
            setattr(sys.modules[".".join(parts[: i - 1])], parts[i - 1], sys.modules[pkg])
    for k, v in attrs.items():
        setattr(sys.modules[name], k, v)


def _load_approval_bot():
    _ensure_stub("gspread", authorize=lambda creds: None)
    _ensure_stub("google.oauth2.credentials", Credentials=object)
    _ensure_stub("googleapiclient.discovery", build=lambda *a, **k: None)
    spec = importlib.util.spec_from_file_location(
        "approval_bot_interop", ROOT / "src" / "watchers" / "approval_bot.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class AppsScriptBundleTests(unittest.TestCase):
    def test_build_and_verify_round_trip(self) -> None:
        bundle = asb.build_bundle(
            manifest_file_id="76f0ad06-25cf-479d-bdd2-9e828b75537a",
            code_file_id="7f4259ac-3398-4eed-97dd-f3b15b9ccc06",
        )
        self.assertEqual([f["name"] for f in bundle["files"]], ["appsscript", "Code"])
        self.assertEqual(bundle["files"][0]["id"], "76f0ad06-25cf-479d-bdd2-9e828b75537a")
        self.assertEqual(bundle["files"][1]["id"], "7f4259ac-3398-4eed-97dd-f3b15b9ccc06")

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "exported.json"
            out.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
            result = asb.verify_exported_bundle(out)
            self.assertTrue(result["ok"], result)

    def test_rejects_write_gmail_scope(self) -> None:
        bad = {
            "runtimeVersion": "V8",
            "timeZone": "Africa/Cairo",
            "oauthScopes": sorted(asb.EXPECTED_SCOPES | {"https://www.googleapis.com/auth/gmail.send"}),
            "urlFetchWhitelist": sorted(asb.EXPECTED_WHITELIST),
        }
        errors = asb.validate_manifest(bad)
        self.assertTrue(any("gmail.send" in e or "oauthScopes" in e for e in errors))

    @unittest.skipUnless(shutil.which("node"), "node is required for JS <-> Python SHA-256 interop proof")
    def test_python_and_apps_script_sha256_and_approval_checksum_match_byte_for_byte(self) -> None:
        ab = _load_approval_bot()
        sample_payload = {
            "id": "01APPRINTEROP0000000000001",
            "action": "gmail.send_draft",
            "subjects": ["01SRC1", "01TASK2"],
            "arabic": "موافقة فورية — Celia Fashion Design ✔",
            "nested": {"b": 2, "a": [True, False, None, "نص عربي"]},
        }
        sample_approval = {
            "id": "01APPRINTEROP0000000000001",
            "created_at": "2026-11-28T08:00:00.000Z",
            "updated_at": "2026-11-28T08:05:00.000Z",
            "version": "2",
            "action": "gmail.send_draft",
            "payload_hash": "0123456789abcdef" * 4,
            "summary": "إرسال عرض سعر إلى العميل",
            "status": "granted",
            "issued_at": "2026-11-28T08:00:00.000Z",
            "expires_at": "2026-11-29T08:00:00.000Z",
            "consumed_at": "2026-11-28T08:05:00.000Z",
            "single_use": "true",
        }
        py_payload_hash = ab.compute_sha256(sample_payload)
        py_appr_checksum = ab.approval_checksum(sample_approval)

        node_probe = """
const crypto = require('node:crypto');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync(%s, 'utf8');
const sandbox = {
  Date, Math, JSON, Number, String, Boolean, Array, Object, RegExp, Error,
  parseInt, isNaN, isFinite, encodeURIComponent,
  Utilities: {
    DigestAlgorithm: { SHA_256: 'SHA_256' },
    Charset: { UTF_8: 'UTF_8' },
    computeDigest: (_a, val) => Array.from(
      crypto.createHash('sha256').update(String(val), 'utf8').digest(),
      (b) => (b > 127 ? b - 256 : b)
    )
  }
};
vm.createContext(sandbox);
vm.runInContext(code, sandbox);
const payload = %s;
const appr = %s;
console.log(JSON.stringify({
  payload_hash: sandbox.computeSha256_(payload),
  appr_checksum: sandbox.approvalChecksum_(appr)
}));
""" % (
            json.dumps(str(asb.DEFAULT_BRIDGE_DIR / "Code.gs")),
            json.dumps(sample_payload, ensure_ascii=False),
            json.dumps(sample_approval, ensure_ascii=False),
        )
        out = subprocess.check_output(["node", "-e", node_probe], text=True).strip()
        js_hashes = json.loads(out)
        self.assertEqual(js_hashes["payload_hash"], py_payload_hash)
        self.assertEqual(js_hashes["appr_checksum"], py_appr_checksum)


if __name__ == "__main__":
    unittest.main()
