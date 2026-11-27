#!/usr/bin/env python3
"""Cross-language interop: Python verifies what the JS worker signs.

The passkey loop only works if three implementations agree byte-for-byte:
the Python executor (RFC 8032 module + canonical message), the Worker's
WebCrypto signing, and Node's independent node:crypto. These tests run the
worker's own code via `node` and feed the results into the real executor.

Skipped when node is unavailable (bare environments); CI images ship node.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import shutil
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEROP = ROOT / "worker" / "approval-worker" / "test" / "interop.js"
NODE = shutil.which("node")

SECRET_HEX = ("c5aa8df43f9f837bedb7442f31dcb7b166d38535"
              "076f094b85ce3a2e0b4458f7")
PUBLIC_HEX = ("fc51cd8e6218a1a38da47ed00230f0580816ed13"
              "ba3303ac5deb911548908025")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ed25519 = _load("ed25519", ROOT / "src" / "executors" / "ed25519.py")
ge = _load("governed_executor", ROOT / "src" / "executors" /
           "governed_executor.py")


@unittest.skipUnless(NODE, "node not available — interop skipped")
class TestPasskeyInterop(unittest.TestCase):
    def node(self, *argv: str) -> str:
        result = subprocess.run(
            [NODE, str(INTEROP), *argv],
            capture_output=True, text=True, check=True)
        return result.stdout

    def test_python_and_node_build_identical_canonical_message(self):
        parts = ("01SMOKEM1489KN08P02TBPEBTK", "agent.plan_day",
                 "5f7b98b83faa21b645fc6a093d84450aad40eeec2578bb49daa4b9e08482ca94",
                 "grant", "2026-11-26T20:00:00.000Z")
        from_node = self.node("canonical", *parts)
        from_python = ge.canonical_assertion_message(*parts).decode("utf-8")
        self.assertEqual(from_node, from_python)

    def test_python_signature_verifies_in_node(self):
        message = "cross-language message from python"
        signature = ed25519.sign(message.encode("utf-8"),
                                 bytes.fromhex(SECRET_HEX))
        verdict = self.node("verify", PUBLIC_HEX, message,
                            base64.b64encode(signature).decode())
        self.assertEqual(verdict, "OK")

    def test_node_signature_verifies_in_python(self):
        message = "cross-language message from node"
        signature_b64 = self.node("sign", SECRET_HEX, message)
        ok = ed25519.verify(base64.b64decode(signature_b64),
                            message.encode("utf-8"),
                            bytes.fromhex(PUBLIC_HEX))
        self.assertTrue(ok)

    def test_worker_signed_envelope_passes_the_real_executor_gate(self):
        # End-to-end: the Worker's WebCrypto signEnvelope() output executes a
        # pending approval through GovernedExecutor (four gates + consume).
        action = "agent.plan_day"
        payload = {"smoke": True, "action": action}
        payload_hash = ge.compute_sha256(payload)
        exp = "2026-11-26T20:00:00.000Z"
        approval_id = "01SMOKEJSINTEROP0000001"

        envelope = json.loads(self.node(
            "sign-envelope", SECRET_HEX, approval_id, action, payload_hash,
            "grant", exp))

        # In-memory sheet: pending approval + active grant.
        class WS:
            def __init__(self, rows):
                self.rows = [list(r) for r in rows]

            def get_all_values(self):
                return [list(r) for r in self.rows]

            def batch_update(self, data):
                for item in data:
                    n = int(item["range"].split(":")[0][1:])
                    self.rows[n - 1] = list(item["values"][0])

        class Book:
            def __init__(self, sheets):
                self.s = {k: WS(v) for k, v in sheets.items()}

            def worksheet(self, name):
                return self.s[name]

        approval = [""] * 22
        approval[0] = approval_id
        approval[1] = approval[2] = "2026-11-26T12:00:00.000Z"
        approval[3] = "1"
        approval[8] = "not_granted"
        approval[13] = "ab" * 32
        approval[14] = action
        approval[15] = payload_hash
        approval[16] = "js interop"
        approval[17] = "pending"
        approval[18] = approval[1] = "2026-11-26T12:00:00.000Z"
        approval[19] = exp
        approval[21] = "true"

        grant = [""] * 23
        grant[14] = "agent.write"
        grant[15] = "active"
        grant[17] = json.dumps([action])
        grant[18] = json.dumps(["gmail.send"])
        grant[19] = json.dumps({"max_per_day": 5})

        audits = []
        executor = ge.GovernedExecutor(
            Book({"pa_approvals": [ge.APPROVAL_HEADER, approval],
                  "pa_delegation_grants": [ge.GRANT_HEADER, grant],
                  "pa_audit_logs_202611": [["h"] * 25]}),
            lambda **kw: audits.append(kw),
            now_fn=lambda: datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc),
            verification_public_key=PUBLIC_HEX,
        )
        executor.register_tool(action, lambda p: {"from": "js-signed"})

        result = executor.execute(approval_id, action, payload,
                                 assertion=envelope)
        self.assertTrue(result.ok, getattr(result, "reason", ""))
        self.assertEqual(result.output, {"from": "js-signed"})
        self.assertEqual(audits[-1]["outcome"], "allowed")
        # Single-use consume applied after the JS-signed execution.
        stored = executor.sheet.worksheet("pa_approvals").rows[1]
        self.assertEqual(stored[17], "consumed")


if __name__ == "__main__":
    unittest.main()
