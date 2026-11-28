#!/usr/bin/env python3
"""Offline end-to-end: signed YCloud webhook -> channel assistant draft (no send).

Proves the WhatsApp half of docs/CHANNEL-ASSISTANT.md §11 with no network and
no real secrets:

* only a correctly signed, fresh, first-seen webhook reaches the assistant;
  bad / missing / expired signatures and replays never call it;
* the assistant only *drafts*: the reply is staged ``pending_approval`` and
  nothing is sent until ``GovernedReplyBridge`` gets a single-use approval;
* coupon / verification requests keep the deterministic path (a model never
  invents a coupon or an OTP);
* a failing or slow assistant falls back to the default reply — a customer
  message is never dropped.

The stub-module tests run everywhere (stdlib only, Python 3.10+). The real
``ai2027_protocol`` test runs when python-2027 is importable (3.11+).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import types
import unittest
from pathlib import Path
from typing import Any, List

from scripts import free_stack_runner as fsr

NOW = 1795878000  # 2026-11-28T15:00:00Z
SECRET = "whsec_assistant_e2e_fixture"


def _event(wamid: str, text: str, send_time: str = "2026-11-28T14:59:50.000Z") -> bytes:
    body = {
        "id": "evt_" + wamid,
        "type": "whatsapp.inbound_message.received",
        "whatsappInboundMessage": {
            "id": "rec_" + wamid,
            "wamid": wamid,
            "from": "+201012345678",
            "to": "+15550109999",
            "sendTime": send_time,
            "type": "text",
            "text": {"body": text},
        },
    }
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def _sign(raw: bytes, ts: int = NOW, secret: str = SECRET) -> str:
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    return f"t={ts},s={sig}"


class _Env:
    def __enter__(self) -> "_Env":
        self.saved = dict(os.environ)
        for name in list(os.environ):
            if name.upper().startswith(("WHATSAPP_", "WAAPI_", "META_", "YCLOUD_", "AI2027_")):
                os.environ.pop(name, None)
        os.environ["YCLOUD_WEBHOOK_SECRET"] = SECRET
        return self

    def __exit__(self, *exc: Any) -> None:
        os.environ.clear()
        os.environ.update(self.saved)


class SignedWebhookToDraftTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = _Env().__enter__()
        self.conn = fsr.init_db(":memory:")
        self.calls: List[str] = []

        def drafter(event: Any, orch: Any) -> str:
            self.calls.append(event.wamid)
            return "رد المساعد: المقاسات من S إلى XL."

        self.orch = fsr.ZeroCostOrchestrator(
            self.conn, now_fn=lambda: NOW, reply_generator=drafter
        )
        wc = self.orch._wc
        self._saved_transport = wc._transport

        def _no_network(*a: Any, **k: Any) -> Any:
            raise AssertionError("draft path must never send")

        wc._transport = _no_network

    def tearDown(self) -> None:
        self.orch._wc._transport = self._saved_transport
        self.conn.close()
        self._env.__exit__()

    def _row(self, wamid: str) -> Any:
        return self.conn.execute(
            "SELECT status, reply_text FROM inbox_messages WHERE wamid = ?", (wamid,)
        ).fetchone()

    def test_valid_signature_stages_assistant_draft_pending_approval(self) -> None:
        raw = _event("wamid.E2E0001", "عندي سؤال عن المقاسات")
        out = self.orch.ingest_signed_webhook(raw, ycloud_signature=_sign(raw))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["stages"], ["pending_approval"])
        self.assertEqual(self.calls, ["wamid.E2E0001"])
        status, reply = self._row("wamid.E2E0001")
        self.assertEqual(status, "pending_approval")  # waits for approval
        self.assertIn("المقاسات", reply)
        payload = self.orch.build_outbound_payload("wamid.E2E0001")
        self.assertEqual(payload["message_text"], reply)

    def test_rejected_signatures_never_reach_the_assistant(self) -> None:
        raw = _event("wamid.E2E0002", "مرحبا")
        cases = {
            "wrong_secret": _sign(raw, secret="whsec_attacker"),
            "expired": _sign(raw, ts=NOW - 3600),
            "tampered_body": _sign(_event("wamid.E2E0002", "مرحبا!")),
            "garbage": "t=abc,s=zzz",
        }
        for name, header in cases.items():
            with self.subTest(case=name):
                out = self.orch.ingest_signed_webhook(raw, ycloud_signature=header)
                self.assertFalse(out["ok"], out)
        missing = self.orch.ingest_signed_webhook(raw)  # no header at all (Meta path)
        self.assertFalse(missing["ok"])
        self.assertEqual(self.calls, [])
        self.assertIsNone(self._row("wamid.E2E0002"))

    def test_replayed_webhook_is_drafted_once(self) -> None:
        raw = _event("wamid.E2E0003", "سؤال")
        first = self.orch.ingest_signed_webhook(raw, ycloud_signature=_sign(raw))
        second = self.orch.ingest_signed_webhook(raw, ycloud_signature=_sign(raw))
        self.assertEqual(first["stages"], ["pending_approval"])
        self.assertEqual(second["stages"], ["duplicate_event"])
        self.assertEqual(self.calls, ["wamid.E2E0003"])

    def test_failing_assistant_falls_back_to_default_reply(self) -> None:
        def boom(event: Any, orch: Any) -> str:
            raise RuntimeError("provider down")

        self.orch._reply_generator = boom
        raw = _event("wamid.E2E0004", "مرحبا")
        out = self.orch.ingest_signed_webhook(raw, ycloud_signature=_sign(raw))
        self.assertEqual(out["stages"], ["pending_approval"])
        self.assertIn("Celia", self._row("wamid.E2E0004")[1])

    def test_outside_free_window_is_blocked_before_drafting(self) -> None:
        raw = _event("wamid.E2E0005", "مرحبا", send_time="2026-11-26T14:00:00.000Z")
        out = self.orch.ingest_signed_webhook(raw, ycloud_signature=_sign(raw))
        self.assertTrue(out["ok"])
        self.assertNotEqual(out["stages"], ["pending_approval"])
        self.assertEqual(self.calls, [])


class _StubCA:
    """Minimal stand-in for ai2027_protocol.channel_assistant_runtime (stdlib only)."""

    def __init__(self, text: str = "رد ذكي") -> None:
        self.text = text
        self.drafted: List[str] = []

    def module(self) -> types.SimpleNamespace:
        stub = self

        class DraftCaptureSender:  # noqa: D401 - stub
            pass

        def make_whatsapp_draft_generator(assistant, capture, *, timeout_s, on_error):
            def gen(event, orch=None):
                stub.drafted.append(event.wamid)
                return stub.text

            return gen

        return types.SimpleNamespace(
            DraftCaptureSender=DraftCaptureSender,
            ChannelAssistant=lambda **kw: object(),
            build_assistant_router=lambda: object(),
            make_whatsapp_draft_generator=make_whatsapp_draft_generator,
        )


class AssistantWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = _Env().__enter__()
        self.conn = fsr.init_db(":memory:")
        self.orch = fsr.ZeroCostOrchestrator(self.conn, now_fn=lambda: NOW)

    def tearDown(self) -> None:
        self.conn.close()
        self._env.__exit__()

    def test_flag_is_off_by_default_and_parses_truthy_values(self) -> None:
        self.assertFalse(fsr.assistant_enabled())
        for value, expected in (("1", True), ("true", True), ("ON", True), ("0", False), ("", False)):
            os.environ[fsr.ASSISTANT_FLAG] = value
            self.assertEqual(fsr.assistant_enabled(), expected, value)

    def test_transactional_messages_keep_the_deterministic_path(self) -> None:
        stub = _StubCA()
        self.orch._reply_generator = fsr.build_assistant_reply_generator(
            self.orch, assistant_module=stub.module()
        )
        coupon = _event("wamid.E2E0010", "أريد كود خصم")
        otp = _event("wamid.E2E0011", "أرسل رمز تأكيد")
        chat = _event("wamid.E2E0012", "ما مواعيد العمل؟")
        for raw in (coupon, otp, chat):
            self.orch.ingest_signed_webhook(raw, ycloud_signature=_sign(raw))
        rows = dict(self.conn.execute("SELECT wamid, reply_text FROM inbox_messages").fetchall())
        self.assertIn("CELIA-", rows["wamid.E2E0010"])  # a real coupon was issued
        self.assertIn("رمز التحقق", rows["wamid.E2E0011"])
        self.assertEqual(rows["wamid.E2E0012"], "رد ذكي")
        self.assertEqual(stub.drafted, ["wamid.E2E0012"])  # model saw only the chat

    def test_missing_package_returns_none_and_defaults_stay(self) -> None:
        saved = dict(sys.modules)
        try:
            sys.modules["ai2027_protocol"] = None  # type: ignore[assignment]
            sys.modules["ai2027_protocol.channel_assistant_runtime"] = None  # type: ignore[assignment]
            self.assertIsNone(fsr.build_assistant_reply_generator(self.orch))
        finally:
            sys.modules.clear()
            sys.modules.update(saved)


def _real_assistant_importable() -> bool:
    src = str(Path(fsr.REPO_ROOT) / "python-2027" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    try:
        import ai2027_protocol.channel_assistant_runtime  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


@unittest.skipUnless(_real_assistant_importable(), "python-2027 not installed")
class RealAssistantEndToEndTests(unittest.TestCase):
    """Real channel_assistant + FreeFirstRouter + BudgetGuard, fake free model."""

    def setUp(self) -> None:
        self._env = _Env().__enter__()
        self.conn = fsr.init_db(":memory:")
        self.orch = fsr.ZeroCostOrchestrator(self.conn, now_fn=lambda: NOW)

    def tearDown(self) -> None:
        self.conn.close()
        self._env.__exit__()

    def _module_with(self, providers: List[Any]) -> types.SimpleNamespace:
        from ai2027_protocol import channel_assistant_runtime as ca
        from ai2027_protocol.budget import BudgetPolicy
        from ai2027_protocol.router import FreeFirstRouter

        def router() -> Any:
            return FreeFirstRouter(providers=providers, budget_policy=BudgetPolicy(max_spend_usd=0.0))

        return types.SimpleNamespace(**{**vars(ca), "build_assistant_router": router})

    @staticmethod
    def _provider(kind: str, tier: str, text: str) -> Any:
        from ai2027_protocol.contracts import BillingTier, ProviderKind

        class P:
            def __init__(self) -> None:
                self.kind = ProviderKind(kind)
                self.model = kind + "-model"
                self.is_remote = True
                self.billing_tier = BillingTier(tier)
                self.prompts: List[Any] = []

            def is_configured(self) -> bool:
                return True

            async def complete(self, request: Any, *, timeout_s: float) -> str:
                self.prompts.append(request)
                return text

        return P()

    def test_free_model_draft_is_staged_and_paid_model_is_never_called(self) -> None:
        paid = self._provider("qwen", "paid", "PAID")
        free = self._provider("groq", "free_tier", "أهلاً! نعمل من 10 صباحاً حتى 10 مساءً.")
        self.orch._reply_generator = fsr.build_assistant_reply_generator(
            self.orch, assistant_module=self._module_with([paid, free])
        )
        raw = _event("wamid.REAL0001", "ما مواعيد العمل؟")
        out = self.orch.ingest_signed_webhook(raw, ycloud_signature=_sign(raw))
        self.assertEqual(out["stages"], ["pending_approval"])
        status, reply = self.conn.execute(
            "SELECT status, reply_text FROM inbox_messages WHERE wamid='wamid.REAL0001'"
        ).fetchone()
        self.assertEqual(status, "pending_approval")
        self.assertIn("10 صباحاً", reply)
        self.assertEqual(paid.prompts, [])
        # Customers meet CELIA in customer scope (no owner context).
        from ai2027_protocol.celia_persona import CELIA_CUSTOMER_PROMPT

        self.assertEqual(free.prompts[0].system_prompt, CELIA_CUSTOMER_PROMPT)
        self.assertEqual(len(free.prompts), 1)
        self.assertIn("ما مواعيد العمل؟", free.prompts[0].prompt)

    def test_no_free_model_drafts_the_default_reply_not_a_debug_echo(self) -> None:
        from ai2027_protocol.ai_providers import DeterministicFallbackProvider

        self.orch._reply_generator = fsr.build_assistant_reply_generator(
            self.orch, assistant_module=self._module_with([DeterministicFallbackProvider()])
        )
        raw = _event("wamid.REAL0002", "مرحبا")
        self.orch.ingest_signed_webhook(raw, ycloud_signature=_sign(raw))
        reply = self.conn.execute(
            "SELECT reply_text FROM inbox_messages WHERE wamid='wamid.REAL0002'"
        ).fetchone()[0]
        self.assertIn("Celia", reply)
        self.assertNotIn("deterministic", reply)


if __name__ == "__main__":
    unittest.main()
