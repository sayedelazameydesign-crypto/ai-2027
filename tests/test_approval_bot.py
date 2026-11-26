#!/usr/bin/env python3
"""Hermetic tests for the Zero-Interface approval bot (stdlib only).

No network, no credentials, no Telegram. The transport HTTP layer and the
sheet are both fakes; the Google connector modules are stubbed before loading
``approval_bot`` (which reuses the watcher's helpers, so the audit chain has
exactly one implementation).

What these tests prove:

- outbound cards pass ssGateway redaction before egress;
- decisions update ``pa_approvals`` (22 cols A:V) with kernel-aligned states
  (granted / denied / expired) and bump ``version``;
- every decision and card batch appends a chained audit row (25 cols A:Y,
  ``prev_hash -> self_hash``);
- fail-closed: malformed callbacks, unlisted senders, empty allowlist,
  unknown / non-pending / expired / consumed approvals are all denied;
- unconfigured transport raises instead of silently sending nothing.
"""

from __future__ import annotations

import importlib.util
import os
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tests.test_proactive_watcher import (
    FakeWorksheet,
    _ensure_module,
    _load_watcher_module,
)

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "watchers" / "approval_bot.py"

_ensure_module("gspread", authorize=lambda creds: None)
_ensure_module("google.oauth2.credentials", Credentials=object)
_ensure_module("googleapiclient.discovery", build=lambda *a, **k: None)
_load_watcher_module()  # registers "proactive_watcher" for the sibling load


def _load_approval_bot():
    spec = importlib.util.spec_from_file_location("approval_bot", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ab = _load_approval_bot()

NOW = datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone.utc)
NOW_ISO = "2026-11-26T12:00:00.000Z"


class SheetWorksheet(FakeWorksheet):
    """FakeWorksheet + the batch_update used for row mutations."""

    def batch_update(self, data):
        for item in data:
            match = re.match(r"A(\d+):[A-Z]+\d+$", item["range"])
            assert match, item["range"]
            row_no = int(match.group(1))
            while len(self.rows) < row_no:
                self.rows.append([])
            self.rows[row_no - 1] = list(item["values"][0])


class FakeSheet:
    def __init__(self):
        self.worksheets = {
            "pa_approvals": SheetWorksheet(22),
            "pa_audit_logs_202611": SheetWorksheet(25),
        }

    def worksheet(self, name):
        return self.worksheets[name]


class FakeTransport:
    def __init__(self, configured=True):
        self._configured = configured
        self.sent = []
        self.answers = []
        self.updates = []

    @property
    def configured(self):
        return self._configured

    def send_card(self, card, chat_id=""):
        if not self._configured:
            raise RuntimeError("transport not configured")
        self.sent.append(card)
        return str(1000 + len(self.sent))

    def answer_callback(self, callback_query_id, text, show_alert=False):
        self.answers.append({
            "id": callback_query_id,
            "text": text,
            "alert": show_alert,
        })

    def fetch_updates(self, offset=None, timeout=0):
        return list(self.updates)


class _FakeWaapiTransport:
    """Canned waapi.app responses; records every call (hermetic)."""

    def __init__(self):
        self.calls = []

    def __call__(self, method, url, body, token):
        self.calls.append({"method": method, "url": url, "body": body,
                           "token": token})
        tr = ab._load_waapi().TransportResult
        if url.endswith("/client/action/send-message"):
            return tr("ok", 200, {"status": "success"})
        if url.endswith("/client/status"):
            return tr("ok", 200, {
                "status": "success",
                "clientStatus": {"status": "success", "instanceId": "777",
                                 "instanceStatus": "ready"}})
        return tr("ok", 200, {"status": "success",
                              "instances": [{"id": 777}]})


def seed_approval(ws, approval_id="01APRV000000000000000001", status="pending",
                  expires_at="2026-11-27T10:01:00.000Z", single_use="true",
                  consumed_at="", summary="مراجعة فاتورة عاجلة", action="gmail.send"):
    created = "2026-11-26T10:01:00.000Z"
    row = [
        approval_id, created, created, "1", "", "1", "fact", "sensitive",
        "not_granted", "review", "2026-11-27T10:01:00.000Z", "", "clean",
        "ab" * 32, action, "cd" * 32, summary, status, created, expires_at,
        consumed_at, single_use,
    ]
    ws.rows.append(row)
    return row


def make_gate(allowed=None, configured=True, sheet=None, transport=None):
    sheet = sheet or FakeSheet()
    transport = transport if transport is not None else FakeTransport(configured)
    gate = ab.ApprovalGate(
        sheet,
        transport,
        allowed_senders=[] if allowed is None else allowed,
        now_fn=lambda: NOW,
    )
    return gate, sheet, transport


def callback(approval_id, verb="grant", sender="777", query_id="q-1"):
    return {
        "id": query_id,
        "callback_data": "appr:%s:%s" % (approval_id, verb),
        "from": {"id": sender},
    }


class TestCardEgress(unittest.TestCase):
    def test_card_redacts_sensitive_summary(self):
        ap = ab.approval_from_row(
            seed_approval(FakeSheet().worksheet("pa_approvals"),
                          summary="رقم قومي 29901010123456 عاجل")
        )
        card = ab.build_approval_card(ap)
        self.assertNotIn("29901010123456", card["text"])
        self.assertIn("[REDACTED_SENSITIVE]", card["text"])

    def test_card_buttons_carry_decision_payloads(self):
        ap = ab.approval_from_row(
            seed_approval(FakeSheet().worksheet("pa_approvals"))
        )
        card = ab.build_approval_card(ap)
        data = [b["data"] for b in card["buttons"]]
        self.assertEqual(data, [
            "appr:01APRV000000000000000001:grant",
            "appr:01APRV000000000000000001:deny",
        ])

    def test_card_appends_passkey_link_when_base_url_set(self):
        # ADR-0006: the card gains a Passkey vote link; the inline buttons
        # stay as the transitional fallback.
        ap = ab.approval_from_row(
            seed_approval(FakeSheet().worksheet("pa_approvals"))
        )
        plain = ab.build_approval_card(ap)
        self.assertNotIn("Passkey", plain["text"])
        linked = ab.build_approval_card(
            ap, vote_base_url="https://vote.example.workers.dev/")
        self.assertIn("Passkey", linked["text"])
        self.assertIn(
            "https://vote.example.workers.dev"
            "?approval=01APRV000000000000000001&action=",
            linked["text"])
        self.assertIn("&hash=", linked["text"])
        self.assertEqual(len(linked["buttons"]), 2)


class TestSendPending(unittest.TestCase):
    def test_sends_only_pending_unexpired_and_dedupes(self):
        gate, sheet, transport = make_gate(allowed=["777"])
        ws = sheet.worksheet("pa_approvals")
        seed_approval(ws, approval_id="A1")
        seed_approval(ws, approval_id="A2", status="granted")
        seed_approval(ws, approval_id="A3",
                      expires_at="2026-11-26T11:00:00.000Z")  # expired

        sent = gate.send_pending_cards()
        self.assertEqual([s["id"] for s in sent], ["A1"])
        self.assertEqual(len(transport.sent), 1)

        # Second call: deduped in-process, no duplicate card, no extra audit.
        self.assertEqual(gate.send_pending_cards(), [])
        audit = sheet.worksheet("pa_audit_logs_202611").rows
        self.assertEqual(len(audit), 2)  # header + one cards_sent row

    def test_unconfigured_transport_raises(self):
        gate, _sheet, _transport = make_gate(allowed=["777"], configured=False)
        with self.assertRaises(RuntimeError):
            gate.send_pending_cards()

    def test_cards_sent_audit_row_is_chained(self):
        gate, sheet, _transport = make_gate(allowed=["777"])
        seed_approval(sheet.worksheet("pa_approvals"), approval_id="A1")
        gate.send_pending_cards()
        rows = sheet.worksheet("pa_audit_logs_202611").rows[1:]
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 25, "audit must be A:Y")
        self.assertTrue(rows[0][23].startswith("GENESIS"))
        self.assertEqual(len(rows[0][24]), 64)


class TestDecisions(unittest.TestCase):
    def test_grant_updates_row_and_chains_audit(self):
        gate, sheet, transport = make_gate(allowed=["777"])
        seed_approval(sheet.worksheet("pa_approvals"), approval_id="A1")

        result = gate.handle_callback(callback("A1"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "granted")

        row = sheet.worksheet("pa_approvals").rows[1]
        self.assertEqual(len(row), 22, "pa_approvals must be A:V")
        self.assertEqual(row[ab.COL_STATUS], "granted")
        self.assertEqual(row[ab.COL_CONSUMED_AT], NOW_ISO)
        self.assertEqual(row[ab.COL_VERSION], "2")
        self.assertEqual(len(row[ab.COL_CHECKSUM]), 64)
        self.assertEqual(transport.answers[0]["id"], "q-1")
        self.assertFalse(transport.answers[0]["alert"])

        audit = sheet.worksheet("pa_audit_logs_202611").rows[1:]
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0][16], "approval.granted")
        self.assertEqual(audit[0][18], "granted")
        self.assertEqual(audit[0][21], "POL-004:DELEGATION-GRANTED")

    def test_deny_sets_denied_state(self):
        gate, sheet, transport = make_gate(allowed=["777"])
        seed_approval(sheet.worksheet("pa_approvals"), approval_id="A1")
        result = gate.handle_callback(callback("A1", verb="deny"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "denied")
        row = sheet.worksheet("pa_approvals").rows[1]
        self.assertEqual(row[ab.COL_STATUS], "denied")
        self.assertTrue(transport.answers[0]["alert"])

    def test_expired_row_transitions_to_expired_state(self):
        gate, sheet, _transport = make_gate(allowed=["777"])
        seed_approval(sheet.worksheet("pa_approvals"), approval_id="A1",
                      expires_at="2026-11-26T11:00:00.000Z")
        result = gate.handle_callback(callback("A1"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "expired")
        row = sheet.worksheet("pa_approvals").rows[1]
        self.assertEqual(row[ab.COL_STATUS], "expired")

    def test_audit_chain_links_across_two_decisions(self):
        gate, sheet, _transport = make_gate(allowed=["777"])
        seed_approval(sheet.worksheet("pa_approvals"), approval_id="A1")
        seed_approval(sheet.worksheet("pa_approvals"), approval_id="A2")
        gate.handle_callback(callback("A1"))
        gate.handle_callback(callback("A2"))
        audit = sheet.worksheet("pa_audit_logs_202611").rows[1:]
        self.assertEqual(len(audit), 2)
        # Second row's prev_hash must equal the first row's self_hash.
        self.assertEqual(audit[1][23], audit[0][24])
        self.assertNotEqual(audit[1][23], audit[1][24])


class TestFailClosed(unittest.TestCase):
    def test_malformed_callback_denied(self):
        gate, sheet, transport = make_gate(allowed=["777"])
        seed_approval(sheet.worksheet("pa_approvals"), approval_id="A1")
        result = gate.handle_callback({
            "id": "q", "callback_data": "rm -rf /", "from": {"id": "777"},
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "invalid_callback")
        self.assertEqual(
            sheet.worksheet("pa_approvals").rows[1][ab.COL_STATUS], "pending"
        )
        self.assertTrue(transport.answers[0]["alert"])

    def test_unlisted_sender_denied(self):
        gate, sheet, _transport = make_gate(allowed=["777"])
        seed_approval(sheet.worksheet("pa_approvals"), approval_id="A1")
        result = gate.handle_callback(callback("A1", sender="666"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "sender_not_allowed")
        self.assertEqual(
            sheet.worksheet("pa_approvals").rows[1][ab.COL_STATUS], "pending"
        )

    def test_empty_allowlist_denies_everyone(self):
        # allowed_senders=[] is also the env-default when unset (fail-closed).
        gate, sheet, _transport = make_gate(allowed=[])
        seed_approval(sheet.worksheet("pa_approvals"), approval_id="A1")
        result = gate.handle_callback(callback("A1", sender="777"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "sender_not_allowed")

    def test_unknown_approval_denied(self):
        gate, _sheet, _transport = make_gate(allowed=["777"])
        result = gate.handle_callback(callback("NOPE"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unknown_approval")

    def test_non_pending_denied(self):
        gate, sheet, _transport = make_gate(allowed=["777"])
        seed_approval(sheet.worksheet("pa_approvals"), approval_id="A1",
                      status="granted")
        result = gate.handle_callback(callback("A1"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "not_pending")

    def test_consumed_single_use_denied(self):
        gate, sheet, _transport = make_gate(allowed=["777"])
        seed_approval(sheet.worksheet("pa_approvals"), approval_id="A1",
                      consumed_at="2026-11-26T10:30:00.000Z")
        result = gate.handle_callback(callback("A1"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "consumed")
        # Row untouched: still pending with its original consumed_at.
        row = sheet.worksheet("pa_approvals").rows[1]
        self.assertEqual(row[ab.COL_STATUS], "pending")
        self.assertEqual(row[ab.COL_CONSUMED_AT], "2026-11-26T10:30:00.000Z")

    def test_every_denial_is_audited(self):
        gate, sheet, _transport = make_gate(allowed=["777"])
        seed_approval(sheet.worksheet("pa_approvals"), approval_id="A1")
        gate.handle_callback({"id": "q", "callback_data": "bogus",
                              "from": {"id": "777"}})
        audit = sheet.worksheet("pa_audit_logs_202611").rows[1:]
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0][16], "approval.callback")
        self.assertEqual(audit[0][18], "denied")


class TestTelegramTransport(unittest.TestCase):
    def _transport(self, calls):
        def post(url, payload):
            calls.append((url, payload))
            return {"ok": True, "result": {"message_id": 42}}

        return ab.TelegramTransport(
            bot_token="T0KEN", chat_id="42", api_base="https://tg.example",
            http_post=post,
        )

    def test_send_card_posts_inline_keyboard(self):
        calls = []
        transport = self._transport(calls)
        ap = ab.approval_from_row(
            seed_approval(FakeSheet().worksheet("pa_approvals"))
        )
        message_id = transport.send_card(ab.build_approval_card(ap))
        self.assertEqual(message_id, "42")
        url, payload = calls[0]
        self.assertEqual(url, "https://tg.example/botT0KEN/sendMessage")
        self.assertEqual(payload["chat_id"], "42")
        keyboard = payload["reply_markup"]["inline_keyboard"]
        self.assertEqual(keyboard[0][0]["callback_data"],
                         "appr:01APRV000000000000000001:grant")
        self.assertEqual(keyboard[1][0]["callback_data"],
                         "appr:01APRV000000000000000001:deny")

    def test_unconfigured_transport_fails_closed(self):
        transport = ab.TelegramTransport(
            bot_token="", chat_id="", api_base="https://tg.example",
            http_post=lambda u, p: {"ok": True},
        )
        self.assertFalse(transport.configured)
        with self.assertRaises(RuntimeError):
            transport.send_card({"text": "x", "buttons": []})

    def test_fetch_updates_passes_offset(self):
        calls = []

        def get(url):
            calls.append(url)
            return {"ok": True, "result": []}

        transport = ab.TelegramTransport(
            bot_token="T", chat_id="C", api_base="https://tg.example",
            http_get=get,
        )
        transport.fetch_updates(offset=7)
        self.assertIn("offset=7", calls[0])


class TestWaapiNotify(unittest.TestCase):
    """Phase 3 of docs/WAAPI-INTEGRATION-PLAN.md — WhatsApp companion."""

    def setUp(self):
        self._env = dict(os.environ)
        self.waapi = ab._load_waapi()
        self._transport = self.waapi._transport
        self.fake = _FakeWaapiTransport()
        self.waapi._transport = self.fake
        os.environ["PA_WAAPI_NOTIFY"] = "1"
        os.environ["WAAPI_OWNER_CHAT_ID"] = "01100000000"
        os.environ["WAAPI_API_TOKEN"] = "fixture-" + "notify-token"
        os.environ["PA_APPROVAL_VOTE_BASE_URL"] = (
            "https://vote.example.workers.dev")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self.waapi._transport = self._transport

    @staticmethod
    def _notify_audits(sheet):
        rows = sheet.worksheet("pa_audit_logs_202611").rows[1:]
        return [r for r in rows
                if len(r) > 20 and str(r[16]) == "approval.waapi_notify"]

    def test_flag_off_means_zero_notify_and_zero_network(self):
        os.environ["PA_WAAPI_NOTIFY"] = "0"
        gate, sheet, transport = make_gate(allowed=["777"])
        seed_approval(sheet.worksheet("pa_approvals"), approval_id="N1")
        gate.send_pending_cards()
        self.assertEqual(len(transport.sent), 1)     # Telegram still works
        self.assertEqual(self._notify_audits(sheet), [])
        self.assertEqual(self.fake.calls, [])

    def test_notify_sends_owner_only_message_with_vote_url(self):
        gate, sheet, transport = make_gate(allowed=["777"])
        seed_approval(sheet.worksheet("pa_approvals"), approval_id="N1")
        gate.send_pending_cards()
        self.assertEqual(len(transport.sent), 1)
        audits = self._notify_audits(sheet)
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0][18], "allowed")   # S: outcome
        posts = [c for c in self.fake.calls if c["method"] == "POST"]
        self.assertEqual(len(posts), 1)
        body = posts[0]["body"]
        # Owner-only recipient, normalised to the Egyptian international form.
        self.assertEqual(body["chatId"], "201100000000@c.us")
        self.assertIn("approval=N1", body["message"])
        self.assertIn("action=gmail.send", body["message"])
        self.assertIn("hash=", body["message"])
        self.assertIn("passkey", body["message"])
        blob = str(audits) + body["message"] + posts[0]["url"]
        self.assertNotIn("notify-token", blob)       # token never leaks

    def test_missing_token_is_structured_denial_and_card_survives(self):
        os.environ.pop("WAAPI_API_TOKEN", None)
        gate, sheet, transport = make_gate(allowed=["777"])
        seed_approval(sheet.worksheet("pa_approvals"), approval_id="N1")
        sent = gate.send_pending_cards()
        self.assertEqual(len(sent), 1)              # Telegram card unaffected
        self.assertEqual(len(transport.sent), 1)
        audits = self._notify_audits(sheet)
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0][18], "denied")    # S: outcome
        # The audit payload is stage-only: token_missing, hashed to column U.
        self.assertEqual(audits[0][20],
                         ab.compute_sha256({"stage": "token_missing"}))
        self.assertEqual(self.fake.calls, [])       # never reached the network

    def test_build_vote_url_shapes_the_worker_triple(self):
        ap = {"id": "A1", "action": "x.y", "payload_hash": "abcd"}
        self.assertEqual(
            ab.build_vote_url(ap, "https://v.example/"),
            "https://v.example?approval=A1&action=x.y&hash=abcd")
        self.assertEqual(
            ab.build_vote_url(ap, "https://v.example/?x=1"),
            "https://v.example/?x=1&approval=A1&action=x.y&hash=abcd")
        self.assertEqual(ab.build_vote_url(ap, ""), "")


if __name__ == "__main__":
    unittest.main()
