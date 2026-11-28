#!/usr/bin/env python3
"""Free-First Zero-Cost Operations Engine & Governed Bridge ($0 Stack, stdlib-only).

Replaces paid SaaS add-ons (CRM/Inbox such as SaleSmartly/Freshdesk/Mixdesk,
Zapier webhook glue, SMS/Voice OTP, Coupon trackers, and paid WhatsApp template
blasts) using pure Python 3.10+ stdlib + SQLite WAL + AI-2027 governed gates:

1. **SQLite WAL CRM Inbox (`inbox_messages`)**: tracks customer inquiries,
   ticket states (`pending_approval` -> `replied` / `blocked`), and deterministic
   `wamid` idempotency.
2. **$0 24-Hour Service Window + Daily Quota Guard**: delegates signature and
   inbound parsing to ``src/connectors/whatsapp_cloud.py`` (supporting both
   **YCloud v2** `YCloud-Signature` and **Meta Graph API** `X-Hub-Signature-256`)
   and refuses anything outside the free `86,400s` customer-care window.
3. **Zero-Cost Coupons & Salted PBKDF2 Verification Codes**:
   - Single-use customer-bound coupons (`issue_coupon` / `redeem_coupon`).
   - Verification codes hashed with per-challenge random 128-bit salt via
     `hashlib.pbkdf2_hmac("sha256", ..., 120_000)` and locked after 5 failed
     attempts (`issue_verification_code` / `verify_code`).
4. **Direct Governed Bridge (`GovernedReplyBridge`)**: connects any local/free
   reply generator (including `ReflectiveCognitivePipeline`) to
   `whatsapp_cloud.preflight_reply_payload` + `GovernedExecutor.execute` so
   every outbound reply requires a single-use `pa_approvals` row and active
   `pa_delegation_grants` row (`MAX_SPEND = 0`).
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib.util
import json
import os
import secrets
import sqlite3
import sys
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
MAX_WEBHOOK_BYTES = 1024 * 1024  # 1 MiB hard ceiling
PBKDF2_ITERATIONS = 120_000
MAX_VERIFY_ATTEMPTS = 5


def _load_repo_module(alias: str, rel_path: str) -> Any:
    if alias in sys.modules:
        return sys.modules[alias]
    target = REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(alias, target)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {rel_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


def init_db(db_path: str = ":memory:") -> sqlite3.Connection:
    """Open SQLite WAL database for $0 CRM inbox, coupons, verification codes, and quotas."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS inbox_messages (
            wamid TEXT PRIMARY KEY,
            sender TEXT NOT NULL,
            phone_number_id TEXT NOT NULL,
            body TEXT NOT NULL,
            msg_ts INTEGER NOT NULL,
            status TEXT NOT NULL,
            reply_text TEXT NOT NULL DEFAULT '',
            payload_hash TEXT NOT NULL DEFAULT '',
            outbound_message_id TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS coupons (
            code TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            discount_pct INTEGER NOT NULL,
            used INTEGER NOT NULL DEFAULT 0,
            expires_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS verification_challenges (
            customer_id TEXT PRIMARY KEY,
            salt_hex TEXT NOT NULL,
            derived_hex TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            expires_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS daily_quota (
            day_key TEXT PRIMARY KEY,
            prepared_count INTEGER NOT NULL DEFAULT 0,
            dispatched_count INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.commit()
    return conn


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str


def _configured_daily_cap(explicit_cap: Optional[int] = None) -> int:
    if explicit_cap is not None:
        return max(0, int(explicit_cap))
    raw = (os.environ.get("AI2027_MAX_FREE_REPLIES_PER_DAY") or "50").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0  # fail-closed on malformed config


def _derive_code_digest(customer_id: str, code_digits: str, salt_hex: str) -> str:
    material = f"{customer_id}:{code_digits}".encode("utf-8")
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        material,
        bytes.fromhex(salt_hex),
        PBKDF2_ITERATIONS,
    )
    return dk.hex()


class ZeroCostOrchestrator:
    """Pure-stdlib $0 CRM, Coupon, Verification, and 24h-Window Reply Stager."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        max_free_replies_per_day: Optional[int] = None,
        reply_generator: Optional[Callable[[Any, "ZeroCostOrchestrator"], str]] = None,
        now_fn: Optional[Callable[[], int]] = None,
    ) -> None:
        self.conn = conn
        self._max_per_day = max_free_replies_per_day
        self._reply_generator = reply_generator
        self._now_fn = now_fn or (lambda: int(time.time()))
        self._wc = _load_repo_module("ai2027_wc_free_stack", "src/connectors/whatsapp_cloud.py")
        self._ge = _load_repo_module("ai2027_ge_free_stack", "src/executors/governed_executor.py")
        self._seen_inbound: set[str] = set()

    def _now(self) -> int:
        return int(self._now_fn())

    def can_send_free_reply(self, msg_ts: int, now_ts: Optional[int] = None) -> GateDecision:
        """Fail-closed $0 service-window + daily free quota check."""
        now_s = self._now() if now_ts is None else int(now_ts)
        if not self._wc.is_within_free_service_window(msg_ts, now_epoch_s=now_s):
            return GateDecision(False, "outside_24h_service_window")

        cap = _configured_daily_cap(self._max_per_day)
        day_key = time.strftime("%Y-%m-%d", time.gmtime(now_s))
        row = self.conn.execute(
            "SELECT prepared_count, dispatched_count FROM daily_quota WHERE day_key = ?",
            (day_key,),
        ).fetchone()
        used = max(int(row[0]), int(row[1])) if row else 0
        if used >= cap:
            return GateDecision(False, f"daily_free_quota_exceeded:{used}>={cap}")
        return GateDecision(True, "free_service_window_ok")

    def issue_coupon(
        self,
        customer_id: str,
        *,
        discount_pct: int = 10,
        ttl_hours: int = 48,
    ) -> str:
        """Issue a customer-bound $0 promo code stored in local SQLite."""
        pct = max(1, min(100, int(discount_pct)))
        code = "CELIA-" + secrets.token_hex(3).upper()
        expires_at = self._now() + max(1, int(ttl_hours)) * 3600
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO coupons (code, customer_id, discount_pct, used, expires_at)
                VALUES (?, ?, ?, 0, ?)
                """,
                (code, customer_id, pct, expires_at),
            )
        return code

    def redeem_coupon(self, code: str, customer_id: str) -> Optional[int]:
        """Atomically redeem an unused, non-expired coupon bound to ``customer_id``."""
        now_s = self._now()
        with self.conn:
            row = self.conn.execute(
                """
                SELECT discount_pct FROM coupons
                WHERE code = ? AND customer_id = ? AND used = 0 AND expires_at >= ?
                """,
                (code.strip().upper(), customer_id, now_s),
            ).fetchone()
            if row is None:
                return None
            cur = self.conn.execute(
                """
                UPDATE coupons SET used = 1
                WHERE code = ? AND customer_id = ? AND used = 0 AND expires_at >= ?
                """,
                (code.strip().upper(), customer_id, now_s),
            )
            if cur.rowcount != 1:
                return None
        return int(row[0])

    def issue_verification_code(self, customer_id: str, *, ttl_seconds: int = 300) -> str:
        """Generate a 6-digit verification code stored only as salted PBKDF2-HMAC-SHA256."""
        code_digits = f"{secrets.randbelow(900_000) + 100_000}"
        salt_hex = secrets.token_hex(16)
        derived_hex = _derive_code_digest(customer_id, code_digits, salt_hex)
        expires_at = self._now() + max(30, int(ttl_seconds))
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO verification_challenges
                    (customer_id, salt_hex, derived_hex, attempts, expires_at)
                VALUES (?, ?, ?, 0, ?)
                ON CONFLICT(customer_id) DO UPDATE SET
                    salt_hex = excluded.salt_hex,
                    derived_hex = excluded.derived_hex,
                    attempts = 0,
                    expires_at = excluded.expires_at
                """,
                (customer_id, salt_hex, derived_hex, expires_at),
            )
        return code_digits

    def verify_code(self, customer_id: str, candidate_code: str) -> bool:
        """Constant-time verification with automatic lockout after 5 failed attempts."""
        now_s = self._now()
        row = self.conn.execute(
            """
            SELECT salt_hex, derived_hex, attempts, expires_at
            FROM verification_challenges WHERE customer_id = ?
            """,
            (customer_id,),
        ).fetchone()
        if row is None:
            return False
        salt_hex, expected_hex, attempts, expires_at = (
            str(row[0]),
            str(row[1]),
            int(row[2]),
            int(row[3]),
        )
        if now_s > expires_at or attempts >= MAX_VERIFY_ATTEMPTS:
            with self.conn:
                self.conn.execute(
                    "DELETE FROM verification_challenges WHERE customer_id = ?",
                    (customer_id,),
                )
            return False

        candidate_hex = _derive_code_digest(customer_id, candidate_code.strip(), salt_hex)
        if hmac.compare_digest(expected_hex, candidate_hex):
            with self.conn:
                self.conn.execute(
                    "DELETE FROM verification_challenges WHERE customer_id = ?",
                    (customer_id,),
                )
            return True

        next_attempts = attempts + 1
        with self.conn:
            if next_attempts >= MAX_VERIFY_ATTEMPTS:
                self.conn.execute(
                    "DELETE FROM verification_challenges WHERE customer_id = ?",
                    (customer_id,),
                )
            else:
                self.conn.execute(
                    "UPDATE verification_challenges SET attempts = ? WHERE customer_id = ?",
                    (next_attempts, customer_id),
                )
        return False

    def _default_reply(self, event: Any) -> str:
        normalized = str(event.text_body or "").strip().lower()
        if "خصم" in normalized or "coupon" in normalized:
            promo = self.issue_coupon(event.sender_wa_id, discount_pct=15)
            return f"تم إصدار كود الخصم المجاني الخاص بك: {promo} (صالح لمدة 48 ساعة)."
        if "تأكيد" in normalized or "رمز" in normalized or "verify" in normalized:
            code_val = self.issue_verification_code(event.sender_wa_id)
            return f"رمز التحقق المجاني الخاص بك هو: {code_val} (صالح لمدة 5 دقائق)."
        return "أهلاً بك في Celia! تم تسجيل طلبك وسنخدمك فوراً ضمن نافذة الخدمة المجانية."

    def build_outbound_payload(self, wamid: str) -> Optional[Dict[str, Any]]:
        """Return the canonical ``whatsapp_cloud.send_reply`` payload for ``wamid``."""
        row = self.conn.execute(
            """
            SELECT sender, reply_text, msg_ts, wamid, status
            FROM inbox_messages WHERE wamid = ?
            """,
            (wamid,),
        ).fetchone()
        if row is None or str(row[4]) not in ("pending_approval", "replied"):
            return None
        return {
            "recipient_phone": str(row[0]),
            "message_text": str(row[1]),
            "inbound_timestamp_s": int(row[2]),
            "reply_to_wamid": str(row[3]),
        }

    def ingest_event(self, event: Any) -> Dict[str, Any]:
        """Persist a verified ``InboundWhatsAppEvent`` and stage its governed reply draft."""
        now_s = self._now()
        existing = self.conn.execute(
            "SELECT status, payload_hash FROM inbox_messages WHERE wamid = ?",
            (event.wamid,),
        ).fetchone()
        if existing is not None or event.stage == "duplicate_event":
            return {"wamid": event.wamid, "stage": "duplicate_event"}

        if event.stage != "accepted_text":
            with self.conn:
                self.conn.execute(
                    """
                    INSERT INTO inbox_messages
                        (wamid, sender, phone_number_id, body, msg_ts, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.wamid,
                        event.sender_wa_id,
                        event.phone_number_id,
                        event.text_body,
                        event.timestamp_s,
                        f"blocked:{event.stage}",
                        now_s,
                    ),
                )
            return {"wamid": event.wamid, "stage": event.stage}

        gate = self.can_send_free_reply(event.timestamp_s, now_s)
        if not gate.allowed:
            with self.conn:
                self.conn.execute(
                    """
                    INSERT INTO inbox_messages
                        (wamid, sender, phone_number_id, body, msg_ts, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.wamid,
                        event.sender_wa_id,
                        event.phone_number_id,
                        event.text_body,
                        event.timestamp_s,
                        f"blocked:{gate.reason}",
                        now_s,
                    ),
                )
            return {"wamid": event.wamid, "stage": gate.reason}

        reply_text = (
            self._reply_generator(event, self)
            if self._reply_generator is not None
            else self._default_reply(event)
        ).strip()
        outbound_payload = {
            "recipient_phone": event.sender_wa_id,
            "message_text": reply_text,
            "inbound_timestamp_s": int(event.timestamp_s),
            "reply_to_wamid": event.wamid,
        }
        payload_hash = str(self._ge.compute_sha256(outbound_payload))
        day_key = time.strftime("%Y-%m-%d", time.gmtime(now_s))
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO inbox_messages
                    (wamid, sender, phone_number_id, body, msg_ts, status,
                     reply_text, payload_hash, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending_approval', ?, ?, ?)
                """,
                (
                    event.wamid,
                    event.sender_wa_id,
                    event.phone_number_id,
                    event.text_body,
                    int(event.timestamp_s),
                    reply_text,
                    payload_hash,
                    now_s,
                ),
            )
            self.conn.execute(
                """
                INSERT INTO daily_quota (day_key, prepared_count, dispatched_count)
                VALUES (?, 1, 0)
                ON CONFLICT(day_key) DO UPDATE SET prepared_count = prepared_count + 1
                """,
                (day_key,),
            )
        return {
            "wamid": event.wamid,
            "stage": "pending_approval",
            "payload_hash": payload_hash,
        }

    def ingest_signed_webhook(
        self,
        raw_body: bytes,
        *,
        hub_signature: Optional[str] = None,
        ycloud_signature: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Verify either YCloud v2 or Meta webhook signature, then stage $0 replies."""
        now_s = self._now()
        if ycloud_signature:
            ok, sig_stage = self._wc.verify_ycloud_signature(
                raw_body, ycloud_signature, now_epoch_s=now_s
            )
        else:
            meta_secret = (os.environ.get("WHATSAPP_CLOUD_APP_SECRET") or "").encode("utf-8")
            ok, sig_stage = self._wc.verify_hub_signature(
                raw_body, hub_signature, meta_secret or None
            )
        if not ok:
            return {"ok": False, "stage": sig_stage, "stages": []}

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"ok": False, "stage": "invalid_json", "stages": []}

        events = self._wc.parse_inbound_events(
            payload,
            now_epoch_s=now_s,
            seen_effect_keys=self._seen_inbound,
        )
        outcomes = [self.ingest_event(ev) for ev in events]
        return {
            "ok": True,
            "stage": "ok",
            "ingested": len(outcomes),
            "stages": [item["stage"] for item in outcomes],
        }


class GovernedReplyBridge:
    """Direct bridge from ``ZeroCostOrchestrator`` to ``GovernedExecutor`` ($0)."""

    def __init__(
        self,
        orchestrator: ZeroCostOrchestrator,
        executor: Any,
        *,
        sent_effect_keys: Optional[set[str]] = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.executor = executor
        self.sent_effect_keys = sent_effect_keys if sent_effect_keys is not None else set()
        self._wc = orchestrator._wc
        self.executor.register_tool(
            "whatsapp_cloud.send_reply",
            self._wc.make_send_reply_handler(
                now_epoch_fn=orchestrator._now,
                sent_effect_keys=self.sent_effect_keys,
            ),
        )

    def dispatch_approved_reply(self, *, wamid: str, approval_id: str) -> Dict[str, Any]:
        """Run ``preflight_reply_payload`` BEFORE ``GovernedExecutor.execute``."""
        payload = self.orchestrator.build_outbound_payload(wamid)
        if payload is None:
            return {"ok": False, "stage": "unknown_or_unready_wamid"}

        now_s = self.orchestrator._now()
        pre = self._wc.preflight_reply_payload(payload, now_epoch_s=now_s)
        if not pre["ok"]:
            return {"ok": False, "stage": str(pre["stage"])}

        res = self.executor.execute(approval_id, "whatsapp_cloud.send_reply", payload)
        if not res.ok:
            return {"ok": False, "stage": str(res.reason)}

        outbound_id = str((res.output or {}).get("outbound_message_id") or "")
        day_key = time.strftime("%Y-%m-%d", time.gmtime(now_s))
        with self.orchestrator.conn:
            self.orchestrator.conn.execute(
                """
                UPDATE inbox_messages
                SET status = 'replied', outbound_message_id = ?
                WHERE wamid = ?
                """,
                (outbound_id, wamid),
            )
            self.orchestrator.conn.execute(
                """
                INSERT INTO daily_quota (day_key, prepared_count, dispatched_count)
                VALUES (?, 0, 1)
                ON CONFLICT(day_key) DO UPDATE SET dispatched_count = dispatched_count + 1
                """,
                (day_key,),
            )
        return {
            "ok": True,
            "stage": "replied",
            "outbound_message_id": outbound_id,
        }


def make_webhook_handler(orchestrator: ZeroCostOrchestrator) -> type[BaseHTTPRequestHandler]:
    """Build a bounded, stage-only HTTP handler (never echoes secrets or OTPs)."""
    wc = orchestrator._wc

    class WebhookHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/webhook":
                self.send_response(404)
                self.end_headers()
                return
            qs = parse_qs(parsed.query)
            expected = (
                os.environ.get("WHATSAPP_CLOUD_VERIFY_TOKEN")
                or os.environ.get("AI2027_WHATSAPP_WEBHOOK")
                or ""
            ).strip()
            ok, challenge_out, _ = wc.verify_webhook_challenge(
                qs.get("hub.mode", [None])[0],
                qs.get("hub.verify_token", [None])[0],
                expected,
                qs.get("hub.challenge", [None])[0],
            )
            if not ok:
                self.send_response(403)
                self.end_headers()
                return
            body = challenge_out.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/webhook":
                self.send_response(404)
                self.end_headers()
                return
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                self.send_response(400)
                self.end_headers()
                return
            if length <= 0 or length > MAX_WEBHOOK_BYTES:
                self.send_response(413 if length > MAX_WEBHOOK_BYTES else 400)
                self.end_headers()
                return
            raw = self.rfile.read(length)
            outcome = orchestrator.ingest_signed_webhook(
                raw,
                hub_signature=self.headers.get("X-Hub-Signature-256"),
                ycloud_signature=self.headers.get("YCloud-Signature"),
            )
            if not outcome["ok"]:
                self.send_response(401)
                self.end_headers()
                return
            resp_bytes = json.dumps(
                {
                    "ok": True,
                    "ingested": outcome["ingested"],
                    "stages": outcome["stages"],
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return  # Suppress raw request logging

    return WebhookHandler


def run_self_check() -> bool:
    """Offline hermetic verification of the $0 stack + GovernedReplyBridge."""
    from datetime import datetime, timezone

    wc = _load_repo_module("ai2027_wc_free_stack", "src/connectors/whatsapp_cloud.py")
    ge = _load_repo_module("ai2027_ge_free_stack", "src/executors/governed_executor.py")

    saved_env = dict(os.environ)
    saved_transport = wc._transport
    conn = init_db(":memory:")
    try:
        for name in list(os.environ):
            if name.upper().startswith(
                ("WHATSAPP_", "WAAPI_", "META_", "YCLOUD_", "AI2027_WHATSAPP_")
            ):
                os.environ.pop(name, None)

        hook_secret = "whsec_free_stack_fixture_2027"
        os.environ["YCLOUD_WEBHOOK_SECRET"] = hook_secret
        os.environ["YCLOUD_API_KEY"] = "yc_fixture_free_stack_key"
        os.environ["YCLOUD_WHATSAPP_FROM"] = "+15550109999"

        now_epoch = 1795878000  # 2026-11-28T15:00:00Z
        now_dt = datetime(2026, 11, 28, 15, 0, 0, tzinfo=timezone.utc)
        orch = ZeroCostOrchestrator(
            conn,
            max_free_replies_per_day=10,
            now_fn=lambda: now_epoch,
        )

        # Verify coupon single-use & PBKDF2 verification lockout
        promo = orch.issue_coupon("201012345678", discount_pct=20)
        if orch.redeem_coupon(promo, "201012345678") != 20:
            return False
        if orch.redeem_coupon(promo, "201012345678") is not None:
            return False
        vcode = orch.issue_verification_code("201012345678")
        if not orch.verify_code("201012345678", vcode):
            return False

        # Ingest signed YCloud v2 customer inquiry
        ycloud_event = {
            "id": "evt_fs_01",
            "type": "whatsapp.inbound_message.received",
            "whatsappInboundMessage": {
                "id": "yim_fs_2027_0001",
                "from": "+201012345678",
                "to": "+15550109999",
                "sendTime": "2026-11-28T14:59:50.000Z",
                "type": "text",
                "text": {"body": "أريد كود خصم على الفستان"},
            },
        }
        raw_body = json.dumps(ycloud_event, ensure_ascii=False).encode("utf-8")
        sig = hmac.new(
            hook_secret.encode("utf-8"),
            (f"{now_epoch}.").encode("utf-8") + raw_body,
            hashlib.sha256,
        ).hexdigest()
        ingested = orch.ingest_signed_webhook(
            raw_body, ycloud_signature=f"t={now_epoch},s={sig}"
        )
        if not ingested["ok"] or ingested["stages"] != ["pending_approval"]:
            return False

        outbound_payload = orch.build_outbound_payload("yim_fs_2027_0001")
        if outbound_payload is None:
            return False

        # Wire GovernedExecutor + single-use approval + active grant
        wc._transport = lambda m, u, b, k: wc.CloudTransportResult(
            "ok", 200, {"id": "yim_out_fs_01", "status": "accepted"}
        )
        appr = [""] * 22
        appr[0] = "01APPR_FREE_STACK_01"
        appr[1] = "2026-11-28T15:00:00.000Z"
        appr[2] = "2026-11-28T15:00:00.000Z"
        appr[3] = "1"
        appr[14] = "whatsapp_cloud.send_reply"
        appr[15] = ge.compute_sha256(outbound_payload)
        appr[16] = "Free-stack YCloud reply"
        appr[17] = "granted"
        appr[18] = "2026-11-28T15:00:00.000Z"
        appr[19] = "2026-11-29T15:00:00.000Z"
        appr[21] = "true"
        appr[13] = ge.approval_checksum(ge.approval_from_row(appr))

        grant = [""] * 23
        grant[0] = "01GRANT_FREE_STACK"
        grant[14] = "agent.write"
        grant[15] = "active"
        grant[17] = json.dumps(["whatsapp_cloud.send_reply"])
        grant[18] = json.dumps([])
        grant[19] = json.dumps({"max_per_day": 10})
        grant[21] = "user"

        class _WS:
            def __init__(self, rows: List[List[str]]) -> None:
                self._rows = rows

            def get_all_values(self) -> List[List[str]]:
                return [list(r) for r in self._rows]

            def batch_update(self, updates: List[Dict[str, Any]]) -> None:
                for u in updates:
                    idx = int(u["range"].split(":")[0][1:]) - 1
                    self._rows[idx] = [str(c) for c in u["values"][0]]

        tables = {
            "pa_approvals": [["h"] * 22, appr],
            "pa_delegation_grants": [["h"] * 23, grant],
            "pa_audit_logs_202611": [["h"] * 25],
        }

        class _Sheet:
            def worksheet(self, name: str) -> _WS:
                return _WS(tables[name])

        executor = ge.GovernedExecutor(_Sheet(), lambda **kw: None, now_fn=lambda: now_dt)
        bridge = GovernedReplyBridge(orch, executor)
        dispatched = bridge.dispatch_approved_reply(
            wamid="yim_fs_2027_0001",
            approval_id="01APPR_FREE_STACK_01",
        )
        replay = bridge.dispatch_approved_reply(
            wamid="yim_fs_2027_0001",
            approval_id="01APPR_FREE_STACK_01",
        )
        return bool(
            dispatched["ok"]
            and dispatched["stage"] == "replied"
            and not replay["ok"]
            and replay["stage"] == "approval_already_consumed"
        )
    finally:
        conn.close()
        wc._transport = saved_transport
        os.environ.clear()
        os.environ.update(saved_env)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="AI-2027 $0 Free-Stack CRM & Webhook Runner")
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Run offline hermetic verification of the $0 stack + GovernedReplyBridge.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="Bind port (default 8080)")
    args = parser.parse_args(argv)

    if args.self_check:
        if run_self_check():
            print("OK: free_stack_runner + governed_bridge passed (0 network, $0)")
            return 0
        print("FAIL: free_stack_runner self-check failed")
        return 1

    db_path = (os.environ.get("AI2027_FREE_STACK_DB") or "free_stack.db").strip()
    conn = init_db(db_path)
    orch = ZeroCostOrchestrator(conn)
    server = ThreadingHTTPServer((args.host, args.port), make_webhook_handler(orch))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
