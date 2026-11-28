#!/usr/bin/env python3
"""Emit parity vectors from the REFERENCE Python stack for the Supabase Edge port.

    python3 scripts/supabase_edge_parity_vectors.py > supabase/functions/ycloud-webhook/parity_vectors.json

The Deno test (index_test.ts) asserts the TypeScript port reproduces every value
byte-for-byte. Fixture secrets only — nothing here is a real credential.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import free_stack_runner as f  # noqa: E402

ge = f._load_repo_module("ai2027_ge_parity", "src/executors/governed_executor.py")
wc = f._load_repo_module("ai2027_wc_parity", "src/connectors/whatsapp_cloud.py")

NOW = 1795860000  # 2026-11-28T10:00:00Z


def events(payload):
    return [
        [e.wamid, e.sender_wa_id, e.phone_number_id, e.timestamp_s, e.stage, e.text_body]
        for e in wc.parse_inbound_events(payload, now_epoch_s=NOW, seen_effect_keys=set())
    ]


def main() -> int:
    reply = {
        "recipient_phone": "201012345678",
        "message_text": "أهلاً بك في Celia! \"q\" \n\t\u0001 ✓ / \\",
        "inbound_timestamp_s": 1764320000,
        "reply_to_wamid": "wamid.ABCD1234",
    }
    secret, body, ts = b"whsec_fixture_vector", b'{"type":"x","n":1}', 1764320000
    ysig = hmac.new(secret, f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    hsig = hmac.new(secret, body, hashlib.sha256).hexdigest()
    yc = {
        "type": "whatsapp.inbound_message.received",
        "whatsappInboundMessage": {
            "id": "wamid.HBgMYC01", "from": "+201012345678", "to": "+15550109999",
            "sendTime": "2026-11-28T09:00:00.000Z", "type": "text",
            "text": {"body": "  عايز خصم  "},
        },
    }
    yc_naive = json.loads(json.dumps(yc))
    yc_naive["whatsappInboundMessage"]["sendTime"] = "2026-11-28T09:00:00"
    msg = lambda i, t, **kw: {"id": i, "from": "201012345678", "timestamp": t, **kw}  # noqa: E731
    meta = {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"field": "messages", "value": {
            "metadata": {"phone_number_id": "1234567890"},
            "messages": [
                msg("wamid.META0001", "1795856400", type="text", text={"body": "hi"}),
                msg("wamid.META0001", "1795856400", type="text", text={"body": "hi"}),
                msg("wamid.META0002", "1795700000", type="text", text={"body": "old"}),
                msg("wamid.META0003", "1795856400", type="image"),
                msg("bad-id", "1795856400", type="text", text={"body": "x"}),
            ],
        }}]}],
    }
    vectors = {
        "now": NOW,
        "sha256": {"payload": reply, "expected": ge.compute_sha256(reply)},
        "ycloud": {
            "secret": secret.decode(), "body": body.decode(), "ts": ts, "sig": ysig,
            "ok": list(wc.verify_ycloud_signature(body, f"t={ts},s={ysig}", secret, now_epoch_s=ts + 10)),
            "expired": list(wc.verify_ycloud_signature(body, f"t={ts},s={ysig}", secret, now_epoch_s=ts + 301)),
            "tampered": list(wc.verify_ycloud_signature(body + b" ", f"t={ts},s={ysig}", secret, now_epoch_s=ts)),
            "malformed": list(wc.verify_ycloud_signature(body, "t=abc,s=00", secret, now_epoch_s=ts)),
        },
        "hub": {"sig": hsig, "ok": list(wc.verify_hub_signature(body, f"sha256={hsig}", secret))},
        "pbkdf2": {
            "args": ["201012345678", "123456", "00112233445566778899aabbccddeeff"],
            "expected": f._derive_code_digest("201012345678", "123456", "00112233445566778899aabbccddeeff"),
        },
        "normalize": {
            p: wc.normalize_recipient_digits(p)
            for p in ["01012345678", "+20 101 234 5678", "0020-1012345678", "123", "+1 (555) 010-9999", "01312345678"]
        },
        "window": {str(d): wc.is_within_free_service_window(NOW - d, now_epoch_s=NOW)
                   for d in [0, 86400, 86401, -300, -301]},
        "events": {"ycloud": events(yc), "ycloud_naive": events(yc_naive), "meta": events(meta)},
    }
    json.dump(vectors, sys.stdout, ensure_ascii=False, indent=1, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
