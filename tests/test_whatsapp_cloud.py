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

    def test_clock_skew_window_boundaries_and_transport_failure_stages(self):
        # Exact 86,400s boundary passes; 86,401s fails.
        self.assertTrue(wc.is_within_free_service_window(1_700_000_000, now_epoch_s=1_700_086_400))
        self.assertFalse(wc.is_within_free_service_window(1_700_000_000, now_epoch_s=1_700_086_401))
        # Small clock skew up to 300s passes; -301s fails.
        self.assertTrue(wc.is_within_free_service_window(1_700_000_300, now_epoch_s=1_700_000_000))
        self.assertFalse(wc.is_within_free_service_window(1_700_000_301, now_epoch_s=1_700_000_000))

        # Input validation edge cases.
        approved = wc.evaluate_outbound_approval(
            recipient_wa_id="201012345678", reply_to_wamid=SAMPLE_WAMID, granted=True
        )
        self.assertEqual(
            wc.send_reply(
                recipient_phone="bad-phone",
                message_text="ok",
                inbound_timestamp_s=1700000000,
                reply_to_wamid=SAMPLE_WAMID,
                approval=approved,
                now_epoch_s=1700000100,
            ),
            {"ok": False, "stage": "invalid_recipient"},
        )
        self.assertEqual(
            wc.send_reply(
                recipient_phone="01012345678",
                message_text="x" * (wc.MESSAGE_MAX_CHARS + 1),
                inbound_timestamp_s=1700000000,
                reply_to_wamid=SAMPLE_WAMID,
                approval=approved,
                now_epoch_s=1700000100,
            ),
            {"ok": False, "stage": "invalid_message"},
        )
        self.assertEqual(
            wc.send_reply(
                recipient_phone="01012345678",
                message_text="ok",
                inbound_timestamp_s=1700000000,
                reply_to_wamid="not-a-wamid",
                approval=approved,
                now_epoch_s=1700000100,
            ),
            {"ok": False, "stage": "invalid_reply_wamid"},
        )

        # Transport refusal stages never leak payload or credentials.
        for stage_name, code, data in [
            ("redirect_refused", 302, None),
            ("too_large", 200, None),
            ("not_json", 502, None),
            ("http_error", 401, None),
            ("shape_invalid", 200, {"messages": [{}]}),
        ]:
            wc._transport = FakeCloudTransport(wc.CloudTransportResult(stage_name, code, data))
            out = wc.send_reply(
                recipient_phone="00201012345678",
                message_text="اختبار حدود النقل",
                inbound_timestamp_s=1700000000,
                reply_to_wamid=SAMPLE_WAMID,
                approval=approved,
                now_epoch_s=1700000100,
            )
            expected_stage = "shape_invalid" if stage_name == "shape_invalid" else stage_name
            self.assertFalse(out["ok"])
            self.assertEqual(out["stage"], expected_stage)
            self.assertNotIn(FIXTURE_ACCESS, json.dumps(out))

        # Malformed webhook envelopes return empty event lists safely.
        self.assertEqual(wc.parse_inbound_events("not-a-dict"), [])
        self.assertEqual(wc.parse_inbound_events({"object": "page", "entry": []}), [])

    def test_governed_executor_integration_consumes_single_use_approval_and_audits(self):
        from datetime import datetime, timezone

        exec_path = ROOT / "src" / "executors" / "governed_executor.py"
        spec = importlib.util.spec_from_file_location("governed_executor_wc_test", exec_path)
        assert spec and spec.loader
        ge = importlib.util.module_from_spec(spec)
        sys.modules["governed_executor_wc_test"] = ge
        spec.loader.exec_module(ge)

        class _Worksheet:
            def __init__(self, rows):
                self.rows = [list(r) for r in rows]

            def get_all_values(self):
                return [list(r) for r in self.rows]

            def batch_update(self, items):
                for item in items:
                    idx = int(item["range"].split(":")[0][1:]) - 1
                    self.rows[idx] = list(item["values"][0])

        class _Sheet:
            def __init__(self, mapping):
                self._m = {k: _Worksheet(v) for k, v in mapping.items()}

            def worksheet(self, name):
                return self._m[name]

        wc._transport = FakeCloudTransport(
            wc.CloudTransportResult("ok", 200, {"messages": [{"id": "wamid.GOV_EXEC_01"}]})
        )
        now_dt = datetime(2026, 11, 28, 12, 0, 0, tzinfo=timezone.utc)
        now_epoch = int(now_dt.timestamp())

        payload = {
            "recipient_phone": "01012345678",
            "message_text": "تمت الموافقة عبر النواة",
            "inbound_timestamp_s": now_epoch - 600,
            "reply_to_wamid": SAMPLE_WAMID,
        }
        appr_row = [""] * 22
        appr_row[0] = "01APPR_WA_01"
        appr_row[1] = "2026-11-28T11:50:00.000Z"
        appr_row[2] = "2026-11-28T11:50:00.000Z"
        appr_row[3] = "1"
        appr_row[13] = "ab" * 32
        appr_row[14] = "whatsapp_cloud.send_reply"
        appr_row[15] = ge.compute_sha256(payload)
        appr_row[16] = "WhatsApp customer reply"
        appr_row[17] = "granted"
        appr_row[18] = "2026-11-28T11:50:00.000Z"
        appr_row[19] = "2026-11-28T13:00:00.000Z"
        appr_row[21] = "true"

        grant_row = [""] * 23
        grant_row[0] = "01GRANT_WA_01"
        grant_row[14] = "agent.write"
        grant_row[15] = "active"
        grant_row[17] = json.dumps(["whatsapp_cloud.send_reply"])
        grant_row[18] = json.dumps([])
        grant_row[21] = "owner"

        sheet = _Sheet(
            {
                "pa_approvals": [["h"] * 22, appr_row],
                "pa_delegation_grants": [["h"] * 23, grant_row],
                "pa_audit_logs_202611": [["h"] * 25],
            }
        )
        audit_events = []
        executor = ge.GovernedExecutor(
            sheet,
            lambda **kw: audit_events.append(kw),
            now_fn=lambda: now_dt,
        )
        executor.register_tool(
            "whatsapp_cloud.send_reply",
            wc.make_send_reply_handler(now_epoch_fn=lambda: now_epoch),
        )

        # 1st execution succeeds and consumes single-use approval.
        res1 = executor.execute("01APPR_WA_01", "whatsapp_cloud.send_reply", payload)
        self.assertTrue(res1.ok)
        self.assertEqual(res1.output["outbound_message_id"], "wamid.GOV_EXEC_01")
        self.assertEqual(audit_events[-1]["outcome"], "allowed")

        # 2nd execution on the same approval_id is denied fail-closed (single-use consumed).
        res2 = executor.execute("01APPR_WA_01", "whatsapp_cloud.send_reply", payload)
        self.assertFalse(res2.ok)
        self.assertEqual(res2.reason, "approval_already_consumed")

        # Even if caller mutates payload fields on a consumed approval, it stays blocked!
        mutated_after_consume = dict(payload, message_text="نص مختلف تماماً")
        res3 = executor.execute(
            "01APPR_WA_01", "whatsapp_cloud.send_reply", mutated_after_consume
        )
        self.assertFalse(res3.ok)
        self.assertEqual(res3.reason, "approval_already_consumed")

    def test_audit_invariants_entry_time_vs_msg_ts_and_message_sha256_and_preconsume(self):
        # 1. Fresh entry[].time must NEVER mask an expired messages[].timestamp (>86,400s).
        delayed_webhook = _sample_webhook_payload(
            wamid="wamid.HBgMMjAxMDEyMzQ1Njc4FQIAEhggDELAYED01==",
            timestamp="1700000000",
        )
        delayed_webhook["entry"][0]["time"] = 1700090000  # fresh webhook arrival!
        parsed = wc.parse_inbound_events(delayed_webhook, now_epoch_s=1700090000)
        self.assertEqual(len(parsed), 1)
        self.assertFalse(parsed[0].within_24h_window)
        self.assertEqual(parsed[0].stage, "outside_24h_service_window")

        # 2. Same-length messages produce distinct effect_keys; exact duplicate is blocked!
        fake = FakeCloudTransport(
            wc.CloudTransportResult("ok", 200, {"messages": [{"id": "wamid.OUT_DEDUP"}]})
        )
        wc._transport = fake
        approved = wc.evaluate_outbound_approval(
            recipient_wa_id="201012345678", reply_to_wamid=SAMPLE_WAMID, granted=True
        )
        sent_keys = set()
        r_yes = wc.send_reply(
            recipient_phone="01012345678",
            message_text="نعم موافق",  # 9 chars
            inbound_timestamp_s=1700000000,
            reply_to_wamid=SAMPLE_WAMID,
            approval=approved,
            now_epoch_s=1700000100,
            sent_effect_keys=sent_keys,
        )
        r_no = wc.send_reply(
            recipient_phone="01012345678",
            message_text="غير موافق",  # also 9 chars, different SHA-256!
            inbound_timestamp_s=1700000000,
            reply_to_wamid=SAMPLE_WAMID,
            approval=approved,
            now_epoch_s=1700000100,
            sent_effect_keys=sent_keys,
        )
        self.assertTrue(r_yes["ok"])
        self.assertTrue(r_no["ok"])
        self.assertNotEqual(r_yes["idempotency_key"], r_no["idempotency_key"])

        # Exact duplicate reply is blocked locally before touching HTTP transport!
        r_dup = wc.send_reply(
            recipient_phone="01012345678",
            message_text="نعم موافق",
            inbound_timestamp_s=1700000000,
            reply_to_wamid=SAMPLE_WAMID,
            approval=approved,
            now_epoch_s=1700000100,
            sent_effect_keys=sent_keys,
        )
        self.assertFalse(r_dup["ok"])
        self.assertEqual(r_dup["stage"], "duplicate_outbound_reply")
        self.assertEqual(len(fake.calls), 2)

        # 3. preflight_reply_payload catches expired 24h window BEFORE burning single-use approval!
        expired_payload = {
            "recipient_phone": "01012345678",
            "message_text": "رد متأخر",
            "inbound_timestamp_s": 1700000000,
            "reply_to_wamid": SAMPLE_WAMID,
        }
        self.assertEqual(
            wc.preflight_reply_payload(expired_payload, now_epoch_s=1700090000),
            {"ok": False, "stage": "outside_24h_service_window"},
        )
        self.assertEqual(
            wc.preflight_reply_payload(expired_payload, now_epoch_s=1700001000),
            {"ok": True, "stage": "ok"},
        )

        # 4. Alert/audit stage classification separates tamper, idempotency, policy, and transport.
        self.assertEqual(wc.classify_audit_stage("invalid_signature"), "security_tamper")
        self.assertEqual(wc.classify_audit_stage("payload_hash_mismatch"), "security_tamper")
        self.assertEqual(wc.classify_audit_stage("duplicate_outbound_reply"), "idempotency_guard")
        self.assertEqual(
            wc.classify_audit_stage("approval_already_consumed"), "idempotency_guard"
        )
        self.assertEqual(
            wc.classify_audit_stage("tool_execution_error:whatsapp_cloud_outside_24h_service_window"),
            "policy_budget_gate",
        )
        self.assertEqual(
            wc.classify_audit_stage("limits_exceeded:max_per_day:50>=50"),
            "policy_budget_gate",
        )
        self.assertEqual(wc.classify_audit_stage("http_error"), "protocol_or_transport")

    def test_ycloud_v2_webhook_signature_inbound_and_send_directly_zero_cost(self) -> None:
        """YCloud v2 (`cela`) + AI2027_WHATSAPP_WEBHOOK full $0 24h-window flow."""
        saved_env = dict(os.environ)
        saved_transport = wc._transport
        fake = FakeCloudTransport(
            wc.CloudTransportResult(
                "ok",
                200,
                {"id": "yim_out_2027_0001", "status": "accepted"},
            )
        )
        try:
            for k in list(os.environ):
                if k.startswith(("WHATSAPP_", "YCLOUD_", "AI2027_WHATSAPP_")):
                    os.environ.pop(k, None)

            # 1. AI2027_WHATSAPP_WEBHOOK mode classification without leaking value
            self.assertEqual(
                wc.resolve_webhook_binding(),
                {"configured": False, "mode": "absent"},
            )
            os.environ["AI2027_WHATSAPP_WEBHOOK"] = "http://insecure.example/webhook"
            self.assertEqual(
                wc.resolve_webhook_binding(),
                {"configured": False, "mode": "insecure_http_refused"},
            )
            os.environ["AI2027_WHATSAPP_WEBHOOK"] = "https://agent.example/v1/whatsapp/webhook"
            self.assertEqual(
                wc.resolve_webhook_binding(),
                {"configured": True, "mode": "endpoint_url"},
            )
            shared_hook_secret = "whsec_ycloud_cela_2027_fixture"
            os.environ["AI2027_WHATSAPP_WEBHOOK"] = shared_hook_secret
            self.assertEqual(
                wc.resolve_webhook_binding(),
                {"configured": True, "mode": "signing_secret"},
            )

            # 2. YCloud-Signature: t=<unix>,s=<hmac_sha256(secret, f"{t}.{raw_body}")>
            ycloud_event = {
                "id": "evt_ycloud_01",
                "type": "whatsapp.inbound_message.received",
                "apiVersion": "v2",
                "whatsappInboundMessage": {
                    "id": "yim_in_2027_0001",
                    "from": "+201012345678",
                    "to": "+15550109999",
                    "sendTime": "2026-11-28T15:00:00.000Z",
                    "type": "text",
                    "text": {"body": "هل فستان السهرة متاح اليوم؟"},
                },
            }
            raw_body = json.dumps(ycloud_event, ensure_ascii=False).encode("utf-8")
            event_ts = 1795878000  # 2026-11-28T15:00:00Z
            sig_hex = hmac.new(
                shared_hook_secret.encode("utf-8"),
                (f"{event_ts}.").encode("utf-8") + raw_body,
                hashlib.sha256,
            ).hexdigest()
            sig_header = f"t={event_ts},s={sig_hex}"

            self.assertEqual(
                wc.verify_ycloud_signature(raw_body, sig_header, now_epoch_s=event_ts + 30),
                (True, "ok"),
            )
            self.assertEqual(
                wc.verify_ycloud_signature(raw_body, sig_header, now_epoch_s=event_ts + 600),
                (False, "signature_timestamp_expired"),
            )
            self.assertEqual(
                wc.classify_audit_stage("signature_timestamp_expired"),
                "security_tamper",
            )

            # 3. Parse YCloud inbound event & enforce 24h free customer-care window
            events = wc.parse_inbound_events(ycloud_event, now_epoch_s=event_ts + 60)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].stage, "accepted_text")
            self.assertEqual(events[0].wamid, "yim_in_2027_0001")
            self.assertEqual(events[0].sender_wa_id, "201012345678")

            # 4. Auto-select YCloud provider when YCLOUD_API_KEY + YCLOUD_WHATSAPP_FROM are set
            os.environ["YCLOUD_API_KEY"] = "yc_fixture_secret_key_never_logged"
            os.environ["YCLOUD_WHATSAPP_FROM"] = "+15550109999"
            wc._transport = fake
            decision = wc.evaluate_outbound_approval(
                recipient_wa_id="201012345678",
                reply_to_wamid="yim_in_2027_0001",
                granted=True,
            )
            sent = wc.send_reply(
                recipient_phone="01012345678",
                message_text="نعم، الفستان متاح في أتيليه Celia اليوم.",
                inbound_timestamp_s=events[0].timestamp_s,
                reply_to_wamid=events[0].wamid,
                approval=decision,
                now_epoch_s=event_ts + 120,
            )
            self.assertTrue(sent["ok"])
            self.assertEqual(sent["outbound_message_id"], "yim_out_2027_0001")
            self.assertEqual(len(fake.calls), 1)
            self.assertEqual(
                fake.calls[0]["url"],
                "https://api.ycloud.com/v2/whatsapp/messages/sendDirectly",
            )
            self.assertEqual(fake.calls[0]["body"]["type"], "text")
            self.assertEqual(fake.calls[0]["body"]["from"], "+15550109999")
            self.assertEqual(fake.calls[0]["body"]["to"], "+201012345678")
            self.assertNotIn("yc_fixture_secret_key_never_logged", json.dumps(sent))
        finally:
            wc._transport = saved_transport
            os.environ.clear()
            os.environ.update(saved_env)


class YCloudSignatureHeaderTests(unittest.TestCase):
    """YCloud docs: one ``t``, one or more ``s`` (secret rotation) — ANY ``s`` may match."""

    TS = 1764320000
    BODY = b'{"id":"evt_rot","type":"whatsapp.inbound_message.received"}'

    def _sig(self, secret: bytes = FIXTURE_SIGNING) -> str:
        return hmac.new(secret, f"{self.TS}.".encode() + self.BODY, hashlib.sha256).hexdigest()

    def _verify(self, header: str):
        return wc.verify_ycloud_signature(self.BODY, header, FIXTURE_SIGNING, now_epoch_s=self.TS)

    def test_rotation_accepts_matching_signature_in_any_position(self) -> None:
        good, old = self._sig(), self._sig(b"fixture-previous-secret")
        self.assertEqual(self._verify(f"t={self.TS},s={good},s={old}"), (True, "ok"))
        self.assertEqual(self._verify(f"t={self.TS},s={old},s={good}"), (True, "ok"))
        self.assertEqual(self._verify(f"t={self.TS},s=zz,s={good}"), (True, "ok"))

    def test_rotation_rejects_when_no_signature_matches(self) -> None:
        old = self._sig(b"fixture-previous-secret")
        self.assertEqual(self._verify(f"t={self.TS},s={old},s={'cd' * 32}"), (False, "invalid_signature"))

    def test_duplicate_or_missing_timestamp_is_malformed(self) -> None:
        good = self._sig()
        self.assertEqual(self._verify(f"t={self.TS},t={self.TS},s={good}"), (False, "malformed_signature"))
        self.assertEqual(self._verify(f"t={self.TS - 1},t={self.TS},s={good}"), (False, "malformed_signature"))
        self.assertEqual(self._verify(f"s={good}"), (False, "malformed_signature"))
        self.assertEqual(self._verify(f"t={self.TS}"), (False, "malformed_signature"))

    def test_non_ascii_digits_in_timestamp_are_malformed(self) -> None:
        arabic = str(self.TS).translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
        self.assertTrue(arabic.isdigit())  # the trap the old parser fell into
        self.assertEqual(self._verify(f"t={arabic},s={self._sig()}"), (False, "malformed_signature"))

    def test_signature_count_is_capped(self) -> None:
        good, other = self._sig(), "ab" * 32
        self.assertEqual(self._verify(f"t={self.TS}," + ",".join([f"s={other}"] * 7 + [f"s={good}"])), (True, "ok"))
        self.assertEqual(self._verify(f"t={self.TS}," + ",".join([f"s={other}"] * 8 + [f"s={good}"])),
                         (False, "malformed_signature"))



class YCloudRealPayloadShapeTests(unittest.TestCase):
    """Real YCloud deliveries (docs.ycloud.com webhook examples) put YCloud's own
    24-hex record id in ``id`` and the WhatsApp message id in ``wamid``. Earlier
    fixtures put a wamid into ``id``, which hid a bug that dropped every real event
    while still answering 200 (so YCloud never retried)."""

    NOW = 1795856460  # 2026-11-28T09:01:00Z

    def _docs_event(self) -> dict:
        return {
            "id": "evt_eEkn26qar3nOB8md",
            "type": "whatsapp.inbound_message.received",
            "apiVersion": "v2",
            "createTime": "2026-11-28T09:00:00.000Z",
            "whatsappInboundMessage": {
                "id": "63f872f6741c165b4342a751",
                "wamid": "wamid.HBgNODi1MjAxMDEyMzQ1Njc4",
                "wabaId": "WABA-ID",
                "from": "+201012345678",
                "fromUserId": "US.13491208655302741918",
                "customerProfile": {"name": "Joe"},
                "to": "+15550109999",
                "sendTime": "2026-11-28T09:00:00.000Z",
                "type": "text",
                "text": {"body": "OK"},
                "context": {"from": "447901614024", "id": "wamid.HBgNODr"},
            },
        }

    def test_docs_payload_yields_one_accepted_event_keyed_by_wamid(self):
        events = wc.parse_inbound_events(self._docs_event(), now_epoch_s=self.NOW)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].wamid, "wamid.HBgNODi1MjAxMDEyMzQ1Njc4")
        self.assertEqual(events[0].stage, "accepted_text")
        self.assertEqual(events[0].sender_wa_id, "201012345678")
        self.assertEqual(events[0].phone_number_id, "15550109999")
        self.assertEqual(events[0].text_body, "OK")

    def test_yclouds_own_record_id_is_never_used_as_wamid(self):
        ev = self._docs_event()
        del ev["whatsappInboundMessage"]["wamid"]
        self.assertEqual(wc.parse_inbound_events(ev, now_epoch_s=self.NOW), [])

    def test_legacy_fixture_shape_still_parses(self):
        ev = self._docs_event()
        msg = ev["whatsappInboundMessage"]
        del msg["wamid"]
        msg["id"] = "yim_in_2027_0001"
        events = wc.parse_inbound_events(ev, now_epoch_s=self.NOW)
        self.assertEqual([e.wamid for e in events], ["yim_in_2027_0001"])


if __name__ == "__main__":
    unittest.main()
