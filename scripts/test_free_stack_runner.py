#!/usr/bin/env python3
"""Unit tests for scripts/free_stack_runner.py ($0 CRM, PBKDF2 OTP, Coupons, HTTP, Bridge)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import unittest
import urllib.error
import urllib.request

from scripts import free_stack_runner


class FreeStackRunnerTests(unittest.TestCase):
    def test_self_check_and_cli_assert(self) -> None:
        self.assertTrue(free_stack_runner.run_self_check())
        self.assertEqual(free_stack_runner.main(["--self-check"]), 0)

    def test_pbkdf2_verification_lockout_and_coupon_single_use(self) -> None:
        conn = free_stack_runner.init_db(":memory:")
        try:
            now_val = [1795878000]
            orch = free_stack_runner.ZeroCostOrchestrator(conn, now_fn=lambda: now_val[0])

            # 5 wrong attempts lock out and delete the challenge even if correct code follows
            real_code = orch.issue_verification_code("201099998888", ttl_seconds=300)
            for _ in range(free_stack_runner.MAX_VERIFY_ATTEMPTS):
                self.assertFalse(orch.verify_code("201099998888", "000000"))
            self.assertFalse(orch.verify_code("201099998888", real_code))

            # Coupon bound to customer_id and expires cleanly
            promo = orch.issue_coupon("201099998888", discount_pct=15, ttl_hours=1)
            self.assertIsNone(orch.redeem_coupon(promo, "201011112222"))
            now_val[0] += 7200
            self.assertIsNone(orch.redeem_coupon(promo, "201099998888"))
        finally:
            conn.close()

    def test_loopback_webhook_handler_stage_only_and_body_cap(self) -> None:
        saved_env = dict(os.environ)
        conn = free_stack_runner.init_db(":memory:")
        now_epoch = 1795878000
        orch = free_stack_runner.ZeroCostOrchestrator(conn, now_fn=lambda: now_epoch)
        server = free_stack_runner.ThreadingHTTPServer(
            ("127.0.0.1", 0), free_stack_runner.make_webhook_handler(orch)
        )
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            hook_secret = "whsec_loopback_test_2027"
            os.environ["AI2027_WHATSAPP_WEBHOOK"] = hook_secret

            # 1. GET /webhook challenge verification
            url_get = (
                f"http://127.0.0.1:{port}/webhook"
                f"?hub.mode=subscribe&hub.verify_token={hook_secret}&hub.challenge=chal_2027"
            )
            with urllib.request.urlopen(url_get, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.read().decode("utf-8"), "chal_2027")

            # 2. POST /webhook YCloud v2 OTP request never echoes OTP in HTTP response body!
            event = {
                "id": "evt_http_01",
                "type": "whatsapp.inbound_message.received",
                "whatsappInboundMessage": {
                    "id": "yim_http_2027_01",
                    "from": "+201012345678",
                    "to": "+15550109999",
                    "sendTime": "2026-11-28T14:59:55.000Z",
                    "type": "text",
                    "text": {"body": "أرسل رمز تأكيد"},
                },
            }
            raw = json.dumps(event, ensure_ascii=False).encode("utf-8")
            sig = hmac.new(
                hook_secret.encode("utf-8"),
                (f"{now_epoch}.").encode("utf-8") + raw,
                hashlib.sha256,
            ).hexdigest()
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/webhook",
                data=raw,
                headers={
                    "Content-Type": "application/json",
                    "YCloud-Signature": f"t={now_epoch},s={sig}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                body_json = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(
                body_json,
                {"ok": True, "ingested": 1, "stages": ["pending_approval"]},
            )

            # 3. Forged signature -> HTTP 401
            bad_req = urllib.request.Request(
                f"http://127.0.0.1:{port}/webhook",
                data=raw,
                headers={"YCloud-Signature": f"t={now_epoch},s={'0' * 64}"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(bad_req, timeout=5)
            self.assertEqual(ctx.exception.code, 401)
            ctx.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            conn.close()
            os.environ.clear()
            os.environ.update(saved_env)


if __name__ == "__main__":
    unittest.main()
