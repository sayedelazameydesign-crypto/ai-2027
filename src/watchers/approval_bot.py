"""Zero-Interface approval channel — P1 step 2 (proactive watcher follow-up).

Sends approval cards for pending ``pa_approvals`` rows over a notification
transport (Telegram Bot API) and applies grant/deny callbacks back to the
sheet. Fail-closed everywhere: this mirrors kernel AD-004
(``default_verdict: deny``) and the kernel's own ``ApprovalState`` vocabulary
(``pending`` / ``granted`` / ``denied`` / ``expired`` — see
``kernel-2027/kernel2027/approvals.py``).

Security contract
-----------------
- **Egress (ssGateway):** every card passes ``sanitize_and_redact`` from the
  watcher before leaving the process. Tainted summaries (national IDs, card
  numbers, passwords) never reach Telegram unredacted.
- **Ingress (fail-closed):** malformed callbacks, senders outside
  ``PA_APPROVAL_ALLOWED_SENDERS`` (empty list = deny all), unknown rows,
  non-pending rows, expired rows and consumed single-use rows are all denied.
  Absence of an answer is never consent.
- **Audit:** every card batch and every decision appends a chained row to
  ``pa_audit_logs_202611`` by reusing the watcher's ``append_audit_log`` —
  there is exactly one chain implementation (``prev_hash -> self_hash``).
- **Dependencies:** stdlib + ``gspread`` only (same connector layer as the
  watcher; outside the zero-dependency kernel path guarded by
  ``scripts/ci_guard.py imports``). HTTP goes through ``urllib`` — no
  third-party HTTP client.

Runtime configuration (env)
---------------------------
- ``PA_MEMORY_SHEET_ID``         — spreadsheet id (shared with the watcher)
- ``PA_APPROVAL_BOT_TOKEN``      — Telegram bot token
- ``PA_APPROVAL_CHAT_ID``        — target chat for cards
- ``PA_APPROVAL_ALLOWED_SENDERS``— comma-separated Telegram user ids (fail-closed)
- ``PA_APPROVAL_API_BASE``       — default ``https://api.telegram.org``
- ``APPROVAL_BOT_INTERVAL_SEC``  — daemon sleep, default 300
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import gspread

# ---------------------------------------------------------------------------
# Sibling load: reuse the watcher's helpers so the audit chain has exactly one
# implementation (AGENTS.md: do not duplicate kernel/memory/policy logic).
# ---------------------------------------------------------------------------
def _load_sibling(name: str) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_pw = _load_sibling("proactive_watcher")
SPREADSHEET_ID: str = _pw.SPREADSHEET_ID
POLL_INTERVAL_SECONDS: int = int(os.getenv("APPROVAL_BOT_INTERVAL_SEC", "300"))
sanitize_and_redact = _pw.sanitize_and_redact
compute_sha256 = _pw.compute_sha256
generate_ulid_like = _pw.generate_ulid_like
utc_now_iso = _pw.utc_now_iso
ProactiveWatcher = _pw.ProactiveWatcher

# pa_approvals header — 22 columns A:V (verified against the live sheet).
APPROVAL_COLUMNS = [
    "id", "created_at", "updated_at", "version", "source_id", "confidence",
    "epistemic", "sensitivity", "consent", "retention", "review_at",
    "deleted_at", "taint", "checksum", "action", "payload_hash", "summary",
    "status", "issued_at", "expires_at", "consumed_at", "single_use",
]

# Column indexes used when mutating a row.
COL_UPDATED_AT = 2
COL_VERSION = 3
COL_CHECKSUM = 13
COL_STATUS = 17
COL_CONSUMED_AT = 20

# Terminal states, aligned with kernel-2027 ApprovalState.
STATE_PENDING = "pending"
STATE_GRANTED = "granted"
STATE_DENIED = "denied"
STATE_EXPIRED = "expired"

_CALLBACK_RE = re.compile(r"^appr:([0-9A-Za-z]+):(grant|deny)$")

DENY_ANSWERS = {
    "invalid_callback": "طلب غير صالح.",
    "sender_not_allowed": "غير مصرح لك بالتصويت.",
    "unknown_approval": "الطلب غير موجود.",
    "not_pending": "تمت معالجة هذا الطلب مسبقاً.",
    "expired": "انتهت صلاحية الطلب.",
    "consumed": "استُهلك هذا الطلب (single-use).",
}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def parse_ts(value: str) -> Optional[datetime]:
    """Parse the sheet's ISO-8601 timestamps (with or without milliseconds)."""
    if not value:
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def is_expired(expires_at: str, now: datetime) -> bool:
    """Fail-closed: an unparsable expiry counts as expired."""
    exp = parse_ts(expires_at)
    if exp is None:
        return True
    return now >= exp


def approval_from_row(row: List[Any]) -> Dict[str, Any]:
    """Map a pa_approvals row (A:V) onto its header names, padding short rows."""
    values = list(row) + [""] * (len(APPROVAL_COLUMNS) - len(row))
    return dict(zip(APPROVAL_COLUMNS, values[: len(APPROVAL_COLUMNS)]))


def approval_checksum(ap: Dict[str, Any]) -> str:
    """Documented checksum for a pa_approvals row.

    The historical seed rows were written with an unidentified
    canonicalisation (no verifier for it exists in this repository), so the
    first mutation re-canonicalises the row to this documented formula. Chain
    linkage (``prev_hash`` of the next row == ``self_hash`` of this row) is
    unaffected either way.
    """
    core = {key: ap.get(key, "") for key in (
        "id", "created_at", "updated_at", "version", "action", "payload_hash",
        "summary", "status", "issued_at", "expires_at", "consumed_at",
        "single_use",
    )}
    return compute_sha256(core)


def build_approval_card(ap: Dict[str, Any]) -> Dict[str, Any]:
    """Build an outbound approval card. Summary is redacted (egress ssGateway)."""
    summary, _sensitivity = sanitize_and_redact(str(ap.get("summary", "")))
    approval_id = str(ap.get("id", ""))
    text = (
        "🔐 طلب موافقة — {action}\n"
        "{summary}\n"
        "المعرّف: {approval_id}\n"
        "ينتهي: {expires_at}\n"
        "اضغط «موافق» أو «رفض». القرار يُسجَّل في pa_approvals ويُسلسل في "
        "pa_audit_logs_202611."
    ).format(
        action=ap.get("action", "?"),
        summary=summary or "(بدون ملخص)",
        approval_id=approval_id,
        expires_at=ap.get("expires_at", "?"),
    )
    return {
        "approval_id": approval_id,
        "text": text,
        "buttons": [
            {"label": "✔ موافق", "data": "appr:%s:grant" % approval_id},
            {"label": "✘ رفض", "data": "appr:%s:deny" % approval_id},
        ],
    }


# ---------------------------------------------------------------------------
# Transport — Telegram Bot API over stdlib urllib, HTTP layer injectable
# ---------------------------------------------------------------------------
class TelegramTransport:
    def __init__(
        self,
        bot_token: str = "",
        chat_id: str = "",
        api_base: str = "",
        http_post: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
        http_get: Optional[Callable[[str], Dict[str, Any]]] = None,
    ) -> None:
        self.bot_token = bot_token or os.getenv("PA_APPROVAL_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("PA_APPROVAL_CHAT_ID", "")
        self.api_base = (
            api_base or os.getenv("PA_APPROVAL_API_BASE", "https://api.telegram.org")
        ).rstrip("/")
        self._post = http_post or self._urllib_post
        self._get = http_get or self._urllib_get

    @property
    def configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def _url(self, method: str) -> str:
        return "%s/bot%s/%s" % (self.api_base, self.bot_token, method)

    @staticmethod
    def _urllib_post(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _urllib_get(url: str) -> Dict[str, Any]:
        with urllib.request.urlopen(url, timeout=35) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))

    def send_card(self, card: Dict[str, Any], chat_id: str = "") -> str:
        """Send one approval card. Raises when unconfigured (fail-closed)."""
        if not self.configured:
            raise RuntimeError(
                "approval bot transport not configured "
                "(PA_APPROVAL_BOT_TOKEN / PA_APPROVAL_CHAT_ID) — fail-closed"
            )
        payload = {
            "chat_id": chat_id or self.chat_id,
            "text": card["text"],
            "reply_markup": {
                "inline_keyboard": [
                    [{"text": b["label"], "callback_data": b["data"]}]
                    for b in card["buttons"]
                ]
            },
        }
        response = self._post(self._url("sendMessage"), payload)
        if not response.get("ok"):
            raise RuntimeError("telegram sendMessage failed: %s" % response)
        return str(response.get("result", {}).get("message_id", ""))

    def answer_callback(self, callback_query_id: str, text: str,
                        show_alert: bool = False) -> None:
        response = self._post(self._url("answerCallbackQuery"), {
            "callback_query_id": callback_query_id,
            "text": text,
            "show_alert": show_alert,
        })
        if not response.get("ok"):
            raise RuntimeError("telegram answerCallbackQuery failed: %s" % response)

    def fetch_updates(self, offset: Optional[int] = None,
                      timeout: int = 0) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        url = self._url("getUpdates") + "?" + urllib.parse.urlencode(params)
        response = self._get(url)
        if not response.get("ok"):
            raise RuntimeError("telegram getUpdates failed: %s" % response)
        return list(response.get("result", []))


# ---------------------------------------------------------------------------
# The gate: fail-closed decision logic + sheet writes + chained audit
# ---------------------------------------------------------------------------
class ApprovalGate:
    """Reads pending approvals, sends cards, applies callbacks — fail-closed."""

    def __init__(
        self,
        sheet: Any,
        transport: TelegramTransport,
        allowed_senders: Optional[List[str]] = None,
        now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.sheet = sheet
        self.transport = transport
        if allowed_senders is None:
            raw = os.getenv("PA_APPROVAL_ALLOWED_SENDERS", "")
            allowed_senders = [s.strip() for s in raw.split(",") if s.strip()]
        # Empty allowlist => nobody may vote (fail-closed, AD-004).
        self.allowed_senders = {str(s) for s in allowed_senders}
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        # One chain implementation: reuse the watcher's append_audit_log.
        self._audit = ProactiveWatcher.__new__(ProactiveWatcher)
        self._audit.sheet = sheet
        self._sent_cards: set = set()

    # -- sheet access -------------------------------------------------------
    def _approvals_ws(self) -> Any:
        return self.sheet.worksheet("pa_approvals")

    def _find_row(self, approval_id: str) -> Optional[List[Any]]:
        rows = self._approvals_ws().get_all_values()
        for idx, row in enumerate(rows[1:], start=2):  # sheet row numbers (1-based + header)
            if row and str(row[0]) == approval_id:
                return_row = list(row) + [""] * (len(APPROVAL_COLUMNS) - len(row))
                return [idx, return_row[: len(APPROVAL_COLUMNS)]]
        return None

    def pending_approvals(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        now = now or self.now_fn()
        rows = self._approvals_ws().get_all_values()[1:]
        pending = []
        for row in rows:
            ap = approval_from_row(row)
            if ap["status"] != STATE_PENDING:
                continue
            if is_expired(ap["expires_at"], now):
                continue
            pending.append(ap)
        return pending

    # -- outbound -----------------------------------------------------------
    def send_pending_cards(self, chat_id: str = "") -> List[Dict[str, Any]]:
        """Send one card per undecided, unexpired approval (once per process)."""
        if not self.transport.configured:
            raise RuntimeError(
                "approval bot transport not configured — fail-closed, no cards sent"
            )
        now = self.now_fn()
        sent: List[Dict[str, Any]] = []
        for ap in self.pending_approvals(now):
            if ap["id"] in self._sent_cards:
                continue
            card = build_approval_card(ap)
            message_id = self.transport.send_card(card, chat_id)
            self._sent_cards.add(ap["id"])
            sent.append({"id": ap["id"], "message_id": message_id})
        if sent:
            self._audit.append_audit_log(
                unit="approval",
                action="approval.cards_sent",
                outcome="allowed",
                subjects=[item["id"] for item in sent],
                payload={"sent": len(sent)},
                policy_id="P1-APPROVAL-BOT",
            )
        return sent

    # -- inbound ------------------------------------------------------------
    def handle_callback(self, query: Dict[str, Any]) -> Dict[str, Any]:
        """Apply one callback query. Persist first, then answer the client."""
        callback_data = str(query.get("callback_data", ""))
        sender = str(query.get("from", {}).get("id", ""))
        query_id = str(query.get("id", ""))

        match = _CALLBACK_RE.fullmatch(callback_data)
        if not match:
            return self._deny(None, "invalid_callback", sender, callback_data,
                              query_id)
        approval_id, verb = match.group(1), match.group(2)

        if sender not in self.allowed_senders:
            return self._deny(approval_id, "sender_not_allowed", sender,
                              callback_data, query_id)

        found = self._find_row(approval_id)
        if found is None:
            return self._deny(approval_id, "unknown_approval", sender,
                              callback_data, query_id)
        sheet_row, row = found
        ap = approval_from_row(row)

        if ap["status"] != STATE_PENDING:
            return self._deny(approval_id, "not_pending", sender,
                              callback_data, query_id)

        now = self.now_fn()
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        if is_expired(ap["expires_at"], now):
            # Lazy transition to the kernel's EXPIRED state, then deny.
            self._persist(sheet_row, row, ap, STATE_EXPIRED, now_iso)
            return self._audit_decision(
                ap, STATE_EXPIRED, "expired", sender, callback_data, query_id
            )

        single_use = str(ap.get("single_use", "")).lower() == "true"
        if single_use and ap.get("consumed_at"):
            return self._deny(approval_id, "consumed", sender, callback_data,
                              query_id)

        state = STATE_GRANTED if verb == "grant" else STATE_DENIED
        self._persist(sheet_row, row, ap, state, now_iso)
        return self._audit_decision(ap, state, "user_decision", sender,
                                    callback_data, query_id)

    def _persist(self, sheet_row: int, row: List[str], ap: Dict[str, Any],
                 state: str, now_iso: str) -> None:
        """Write the terminal state back to pa_approvals (A:V, RAW semantics)."""
        row[COL_UPDATED_AT] = now_iso
        try:
            row[COL_VERSION] = str(int(row[COL_VERSION] or "1") + 1)
        except ValueError:
            row[COL_VERSION] = "2"
        row[COL_STATUS] = state
        row[COL_CONSUMED_AT] = now_iso
        updated = approval_from_row(row)
        row[COL_CHECKSUM] = approval_checksum(updated)
        self._approvals_ws().batch_update([{
            "range": "A%d:V%d" % (sheet_row, sheet_row),
            "values": [row],
        }])
        # Reflect the persisted state in the caller's dict (audit payload).
        ap.update(approval_from_row(row))

    def _audit_decision(self, ap: Dict[str, Any], state: str, reason: str,
                        sender: str, callback_data: str, query_id: str) -> Dict[str, Any]:
        policy = (
            "POL-004:DELEGATION-GRANTED" if state == STATE_GRANTED
            else "POL-004:DELEGATION-DENIED"
        )
        self._audit.append_audit_log(
            unit="approval",
            action="approval.%s" % state,
            outcome=state,
            subjects=[ap["id"]],
            payload={
                "approval_id": ap["id"],
                "action": ap.get("action", ""),
                "state": state,
                "reason": reason,
                "sender": sender,
                "callback": callback_data,
                "version": ap.get("version", ""),
            },
            policy_id=policy,
        )
        if state == STATE_GRANTED:
            answer, ok = "✅ تم التسجيل: موافقة ممنوحة.", True
        elif state == STATE_EXPIRED:
            answer, ok = DENY_ANSWERS["expired"], False
        else:
            answer, ok = "تم التسجيل: رفض.", False
        if query_id:
            self.transport.answer_callback(query_id, answer,
                                           show_alert=not ok)
        return {
            "ok": ok,
            "state": state,
            "reason": reason,
            "approval_id": ap["id"],
            "answer": answer,
        }

    def _deny(self, approval_id: Optional[str], reason: str, sender: str,
              callback_data: str, query_id: str) -> Dict[str, Any]:
        """Fail-closed path: audit the attempt, then answer — no state change."""
        self._audit.append_audit_log(
            unit="approval",
            action="approval.callback",
            outcome="denied",
            subjects=[approval_id] if approval_id else [],
            payload={
                "approval_id": approval_id or "",
                "reason": reason,
                "sender": sender,
                "callback": callback_data[:120],
            },
            policy_id="POL-004:DELEGATION-DENIED",
        )
        answer = DENY_ANSWERS.get(reason, "مرفوض.")
        if query_id:
            self.transport.answer_callback(query_id, answer, show_alert=True)
        return {
            "ok": False,
            "state": STATE_DENIED,
            "reason": reason,
            "approval_id": approval_id or "",
            "answer": answer,
        }


# ---------------------------------------------------------------------------
# Runtime entry points
# ---------------------------------------------------------------------------
class ApprovalBot:
    """Wires gspread + env config into an ApprovalGate with a daemon loop."""

    def __init__(
        self,
        creds: Any,
        transport: Optional[TelegramTransport] = None,
        allowed_senders: Optional[List[str]] = None,
        now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.gc = gspread.authorize(creds)
        self.sheet = self.gc.open_by_key(SPREADSHEET_ID)
        self.transport = transport or TelegramTransport()
        self.gate = ApprovalGate(self.sheet, self.transport,
                                 allowed_senders=allowed_senders, now_fn=now_fn)
        self._offset: Optional[int] = None

    def run_once(self) -> Dict[str, Any]:
        """One loop: deliver new cards, then apply any pending callbacks."""
        sent = self.gate.send_pending_cards()
        updates = self.transport.fetch_updates(offset=self._offset)
        handled: List[Dict[str, Any]] = []
        for update in updates:
            self._offset = int(update.get("update_id", 0)) + 1
            query = update.get("callback_query")
            if not query:
                continue
            handled.append(self.gate.handle_callback(query))
        return {
            "cards_sent": len(sent),
            "callbacks_handled": len(handled),
            "granted": sum(1 for h in handled if h.get("state") == STATE_GRANTED),
            "denied": sum(1 for h in handled if not h.get("ok")),
        }


def start_daemon(creds: Any) -> None:
    """Persistent delivery + callback loop (same shape as the watcher's)."""
    bot = ApprovalBot(creds)
    while True:
        try:
            report = bot.run_once()
            print("[%s] Approval bot cycle: %s" % (utc_now_iso(), report))
        except Exception as exc:  # noqa: BLE001 — daemon must survive transient errors
            print("[%s] Approval bot error: %s" % (utc_now_iso(), exc))
        time.sleep(POLL_INTERVAL_SECONDS)
