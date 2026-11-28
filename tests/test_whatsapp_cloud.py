#!/usr/bin/env python3
"""Hermetic stdlib tests for src/connectors/whatsapp_cloud.py ($0, zero network).

Covers:
- Constant-time webhook GET challenge verification (valid, bad mode, mismatch, bad challenge).
- Constant-time HMAC-SHA256 X-Hub-Signature-256 verification (valid, empty, malformed, forged).
- Inbound event parsing: text extraction, deterministic kernel2027.effect_key deduplication,
  non-text message filtering, and 24-hour customer-service free window enforcement.
- Outbound send_reply fail-closed gates: local preflight before approval, 24h window guard,
  kernel2027.decide approval gate (pending/denied/granted), HTTP transport errors, and zero
  secret/credential leakage in return payloads.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "connectors" / "whatsapp_cloud.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("whatsapp_cloud", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["whatsapp_cloud"] = module
    spec.loader.exec_module(module)
    return module


wc = _load_module()

FIXTURE_ACCESS = "fixture-" + "wa-cloud-bearer"
FIXTURE_VERIFY = "fixture-" + "webhook-verify"
FIXTURE_SIGNING = b"fixture-signing-bytes-2027"
PHONE_ID = "109876543210987"
SAMPLE_WAMID = "wamid.HBgMMjAxMDEyMzQ1Njc4FQIAEhggMTIzNDU2Nzg5MA=="


class FakeCloudTransport:
    """Captures outbound Graph API calls without touching the network."""

    def __init__(self, result):
        self.calls = []
        self.result = result

    def __call__(self, method, url, body, bearer_value):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "body": body,
                "has_bearer": bool(bearer_value),
            }
        )
        return self.result


def _sample_webhook_payload(*, wamid=SAMPLE_WAMID, timestamp="1700000100", msg_type="text", body="مرحبا، أريد الاستفسار عن الطلب"):
    msg = {
        "from": "201012345678",
        "id": wamid,
        "timestamp": timestamp,
        "type": msg_type,
    }
    if msg_type == "text":
        msg["text"] = {"body": body}
    else:
        msg["image"] = {"id": "img_123"}
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba_1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": PHONE_ID},
                            "messages": [msg],
                        },
                    }
                ],
            }
        ],
    }


class WhatsAppCloudTests(unittest.TestCase):
    def setUp(self):
        self._saved_env = dict(os.environ)
        self._saved_transport = wc._transport
        os.environ["WHATSAPP_CLOUD_ACCESS_TOKEN"] = FIXTURE_ACCESS
        os.environ["WHATSAPP_CLOUD_PHONE_NUMBER_ID"] = PHONE_ID

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved_env)
        wc._transport = self._saved_transport

    def test_webhook_challenge_constant_time_validation(self):
        ok, ch, stage = wc.verify_webhook_challenge(
            "subscribe", FIXTURE_VERIFY, FIXTURE_VERIFY, "192837465"
        )
        self.assertTrue(ok)
        self.assertEqual(ch, "192837465")
        self.assertEqual(stage, "ok")

        self.assertEqual(
            wc.verify_webhook_challenge("unsubscribe", FIXTURE_VERIFY, FIXTURE_VERIFY, "123"),
            (False, "", "invalid_mode"),
        )
        self.assertEqual(
            wc.verify_webhook_challenge("subscribe", "wrong", FIXTURE_VERIFY, "123"),
            (False, "", "challenge_mismatch"),
        )
        self.assertEqual(
            wc.verify_webhook_challenge("subscribe", FIXTURE_VERIFY, "", "123"),
            (False, "", "missing_verify_config"),
        )
        self.assertEqual(
            wc.verify_webhook_challenge(
                "subscribe", FIXTURE_VERIFY, FIXTURE_VERIFY, "<script>bad</script>"
            ),
            (False, "", "invalid_challenge"),
        )

    def test_hub_signature_hmac_sha256_verification(self):
        raw = json.dumps(_sample_webhook_payload()).encode("utf-8")
        digest = hmac.new(FIXTURE_SIGNING, raw, hashlib.sha256).hexdigest()

        self.assertEqual(
            wc.verify_hub_signature(raw, "sha256=" + digest, FIXTURE_SIGNING),
            (True, "ok"),
        )
        self.assertEqual(
            wc.verify_hub_signature(raw, "", FIXTURE_SIGNING),
            (False, "missing_signature"),
        )
        self.assertEqual(
            wc.verify_hub_signature(raw, "sha1=" + digest, FIXTURE_SIGNING),
            (False, "malformed_signature"),
        )
        self.assertEqual(
            wc.verify_hub_signature(raw, "sha256=not-hex", FIXTURE_SIGNING),
            (False, "malformed_signature"),
        )
        self.assertEqual(
            wc.verify_hub_signature(raw, "sha256=" + ("0" * 64), FIXTURE_SIGNING),
            (False, "invalid_signature"),
        )
        self.assertEqual(
            wc.verify_hub_signature(raw, "sha256=" + digest, b""),
            (False, "missing_signing_config"),
        )

    def test_inbound_events_deduplicate_wamid_filter_nontext_and_check_24h_window(self):
        seen = set()
        payload_ok = _sample_webhook_payload(timestamp="1700000100")
        events1 = wc.parse_inbound_events(
            payload_ok, now_epoch_s=1700003700, seen_effect_keys=seen
        )
        self.assertEqual(len(events1), 1)
        self.assertEqual(events1[0].stage, "accepted_text")
        self.assertTrue(events1[0].within_24h_window)
        self.assertTrue(events1[0].idempotency_key.startswith("whatsapp_cloud:"))

        # Replaying the identical webhook delivery is flagged as duplicate_event.
        events_dup = wc.parse_inbound_events(
            payload_ok, now_epoch_s=1700003700, seen_effect_keys=seen
        )
        self.assertEqual(events_dup[0].stage, "duplicate_event")

        # Non-text payload is ignored safely.
        events_img = wc.parse_inbound_events(
            _sample_webhook_payload(
                wamid="wamid.HBgMMjAxMDEyMzQ1Njc4FQIAEhggIMAGE0001==",
                msg_type="image",
            ),
            now_epoch_s=1700003700,
            seen_effect_keys=seen,
        )
        self.assertEqual(events_img[0].stage, "non_text_ignored")

        # Message older than 24h (86,400s) is marked outside_24h_service_window ($0 guard).
        events_expired = wc.parse_inbound_events(
            _sample_webhook_payload(
                wamid="wamid.HBgMMjAxMDEyMzQ1Njc4FQIAEhggEXPIRED01==",
                timestamp="1700000000",
            ),
            now_epoch_s=1700090000,
            seen_effect_keys=seen,
        )
        self.assertFalse(events_expired[0].within_24h_window)
        self.assertEqual(events_expired[0].stage, "outside_24h_service_window")

    def test_send_reply_fail_closed_gates_and_never_leaks_credentials(self):
        fake = FakeCloudTransport(
            wc.CloudTransportResult(
                "ok",
                200,
                {"messages": [{"id": "wamid.OUTBOUND_OK_01"}]},
            )
        )
        wc._transport = fake

        # 1. Without explicit approval -> approval_pending, zero HTTP calls!
        pending = wc.send_reply(
            recipient_phone="01012345678",
            message_text="تم تأكيد طلبك",
            inbound_timestamp_s=1700000000,
            reply_to_wamid=SAMPLE_WAMID,
            now_epoch_s=1700001000,
        )
        self.assertEqual(pending, {"ok": False, "stage": "approval_pending"})
        self.assertEqual(len(fake.calls), 0)

        # 2. Explicit denial -> approval_denied, zero HTTP calls!
        denied_decision = wc.evaluate_outbound_approval(
            recipient_wa_id="201012345678",
            reply_to_wamid=SAMPLE_WAMID,
            granted=False,
        )
        denied = wc.send_reply(
            recipient_phone="01012345678",
            message_text="تم تأكيد طلبك",
            inbound_timestamp_s=1700000000,
            reply_to_wamid=SAMPLE_WAMID,
            approval=denied_decision,
            now_epoch_s=1700001000,
        )
        self.assertEqual(denied, {"ok": False, "stage": "approval_denied"})
        self.assertEqual(len(fake.calls), 0)

        # 3. Outside 24h free window -> outside_24h_service_window even if approved!
        approved_decision = wc.evaluate_outbound_approval(
            recipient_wa_id="201012345678",
            reply_to_wamid=SAMPLE_WAMID,
            granted=True,
        )
        expired = wc.send_reply(
            recipient_phone="+20 101 234 5678",
            message_text="تم تأكيد طلبك",
            inbound_timestamp_s=1700000000,
            reply_to_wamid=SAMPLE_WAMID,
            approval=approved_decision,
            now_epoch_s=1700095000,
        )
        self.assertEqual(expired, {"ok": False, "stage": "outside_24h_service_window"})
        self.assertEqual(len(fake.calls), 0)

        # 4. Missing credential fails preflight locally without touching transport!
        os.environ.pop("WHATSAPP_CLOUD_ACCESS_TOKEN", None)
        missing = wc.send_reply(
            recipient_phone="01012345678",
            message_text="تم تأكيد طلبك",
            inbound_timestamp_s=1700000000,
            reply_to_wamid=SAMPLE_WAMID,
            approval=approved_decision,
            now_epoch_s=1700001000,
        )
        self.assertEqual(missing, {"ok": False, "stage": "missing_access_credential"})
        self.assertEqual(len(fake.calls), 0)

        # 5. All gates green -> sends normalized E.164 digits and never echoes bearer!
        os.environ["WHATSAPP_CLOUD_ACCESS_TOKEN"] = FIXTURE_ACCESS
        sent = wc.send_reply(
            recipient_phone="01012345678",
            message_text="تم تأكيد طلبك بنجاح",
            inbound_timestamp_s=1700000000,
            reply_to_wamid=SAMPLE_WAMID,
            approval=approved_decision,
            now_epoch_s=1700001000,
        )
        self.assertTrue(sent["ok"])
        self.assertEqual(sent["stage"], "ok")
        self.assertEqual(sent["outbound_message_id"], "wamid.OUTBOUND_OK_01")
        self.assertNotIn(FIXTURE_ACCESS, json.dumps(sent))
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0]["body"]["to"], "201012345678")


if __name__ == "__main__":
    unittest.main()
