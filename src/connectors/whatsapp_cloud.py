#!/usr/bin/env python3
"""Official Meta WhatsApp Business Cloud API connector & webhook verifier ($0, stdlib-only).

Design rules:
- **Zero third-party dependencies** (ADR-0002): ``hmac``, ``hashlib``, ``json``,
  ``urllib.request``, and shared ``kernel2027`` primitives only.
- **Constant-time verification & zero secret leakage**: ``hmac.compare_digest``
  guards both ``X-Hub-Signature-256`` and webhook verification tokens; no secret,
  bearer credential, signature header, or provider error body is ever logged,
  printed, or echoed into exception/return strings.
- **MAX_SPEND = 0 (24-hour customer-service window guard)**: free replies are
  permitted only inside Meta's 24-hour customer-initiated service window
  (``0 <= now_epoch_s - inbound_timestamp_s <= 86_400``); paid template sends
  outside that window are refused fail-closed (``outside_24h_service_window``).
- **Idempotency & kernel approval gate**: inbound ``wamid`` events derive a
  deterministic :func:`kernel2027.effect_key` and outbound replies require an
  explicit :func:`kernel2027.decide` ``approved=True`` decision after
  :func:`preflight` succeeds locally.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, MutableSet, NamedTuple, Optional, Tuple

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "kernel-2027") not in sys.path:
    sys.path.insert(0, str(_ROOT / "kernel-2027"))

from kernel2027 import (  # noqa: E402
    ApprovalDecision,
    ApprovalRequest,
    ApprovalState,
    decide,
    effect_key,
    sha256_text,
)

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
SERVICE_WINDOW_SECONDS = 86_400
CLOCK_SKEW_TOLERANCE_SECONDS = 300
TIMEOUT_SEC = 15
MAX_BODY_BYTES = 1024 * 1024
MESSAGE_MAX_CHARS = 4096

_PHONE_ID_RE = re.compile(r"^[1-9]\d{5,24}$")
_E164_DIGITS_RE = re.compile(r"^[1-9]\d{7,14}$")
_WAMID_RE = re.compile(r"^wamid\.[A-Za-z0-9_+/=-]{4,240}$")
_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9._-]{1,256}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class CloudTransportResult(NamedTuple):
    """Stage-only HTTP outcome — never exposes bearer values or raw error bodies."""

    stage: str
    status: Optional[int]
    data: Any


class WhatsAppCloudSendError(RuntimeError):
    """Raised inside GovernedExecutor dispatch — carries stage code only."""


@dataclass(frozen=True)
class InboundWhatsAppEvent:
    """Normalized inbound webhook item with idempotency key and 24h window check."""

    wamid: str
    sender_wa_id: str
    phone_number_id: str
    timestamp_s: int
    message_type: str
    text_body: str
    idempotency_key: str
    within_24h_window: bool
    stage: str  # accepted_text | duplicate_event | non_text_ignored | outside_24h_service_window


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse HTTP redirects fail-closed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        return None


def _urllib_transport(
    method: str,
    url: str,
    body: Optional[Dict[str, Any]],
    bearer_value: str,
) -> CloudTransportResult:
    headers = {
        "Authorization": "Bearer " + bearer_value,
        "Accept": "application/json",
    }
    encoded: Optional[bytes] = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=encoded, headers=headers, method=method)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=TIMEOUT_SEC) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            content_type = (resp.headers.get("Content-Type") or "").lower()
            raw = resp.read(MAX_BODY_BYTES + 1)
            if len(raw) > MAX_BODY_BYTES:
                return CloudTransportResult("too_large", status, None)
            if "application/json" not in content_type:
                return CloudTransportResult("not_json", status, None)
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return CloudTransportResult("not_json", status, None)
            return CloudTransportResult("ok", status, parsed)
    except urllib.error.HTTPError as err:
        if err.code in (301, 302, 303, 307, 308):
            return CloudTransportResult("redirect_refused", err.code, None)
        return CloudTransportResult("http_error", err.code, None)
    except (urllib.error.URLError, TimeoutError, OSError):
        return CloudTransportResult("network_error", None, None)


_transport = _urllib_transport


def normalize_recipient_digits(raw_phone: str) -> Optional[str]:
    """Normalize E.164 or Egyptian local phone input to Meta Graph digits."""
    if not isinstance(raw_phone, str):
        return None
    digits = re.sub(r"[\s()-]", "", raw_phone.strip())
    if digits.startswith("+"):
        digits = digits[1:]
    if digits.startswith("00"):
        digits = digits[2:]
    if re.match(r"^01[0125]\d{8}$", digits):
        digits = "2" + digits
    if not _E164_DIGITS_RE.match(digits):
        return None
    return digits


def verify_webhook_challenge(
    mode: Optional[str],
    provided_verify_value: Optional[str],
    expected_verify_value: Optional[str],
    challenge: Optional[str],
) -> Tuple[bool, str, str]:
    """Validate Meta ``GET /webhook`` subscription challenge in constant time.

    Returns ``(ok, challenge_or_empty, stage)``.
    """
    if not expected_verify_value or not expected_verify_value.strip():
        return False, "", "missing_verify_config"
    if mode != "subscribe":
        return False, "", "invalid_mode"
    if not provided_verify_value:
        return False, "", "challenge_mismatch"
    matched = hmac.compare_digest(
        provided_verify_value.encode("utf-8"),
        expected_verify_value.encode("utf-8"),
    )
    if not matched:
        return False, "", "challenge_mismatch"
    if not challenge or not _CHALLENGE_RE.match(challenge):
        return False, "", "invalid_challenge"
    return True, challenge, "ok"


def verify_hub_signature(
    raw_body: bytes,
    signature_header: Optional[str],
    signing_bytes: Optional[bytes],
) -> Tuple[bool, str]:
    """Verify Meta ``X-Hub-Signature-256: sha256=<hex>`` using ``hmac.compare_digest``."""
    if not isinstance(raw_body, (bytes, bytearray)):
        return False, "invalid_body_type"
    if not signing_bytes:
        return False, "missing_signing_config"
    if not signature_header or not isinstance(signature_header, str):
        return False, "missing_signature"
    header = signature_header.strip()
    if not header.startswith("sha256="):
        return False, "malformed_signature"
    provided_hex = header[len("sha256=") :].lower()
    if not _HEX64_RE.match(provided_hex):
        return False, "malformed_signature"
    expected_hex = hmac.new(bytes(signing_bytes), bytes(raw_body), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(provided_hex, expected_hex):
        return False, "invalid_signature"
    return True, "ok"


def is_within_free_service_window(
    inbound_timestamp_s: int,
    *,
    now_epoch_s: Optional[int] = None,
) -> bool:
    """Return True iff reply falls inside Meta's $0 24-hour customer-service window."""
    now_s = int(time.time()) if now_epoch_s is None else int(now_epoch_s)
    elapsed = now_s - int(inbound_timestamp_s)
    if elapsed < -CLOCK_SKEW_TOLERANCE_SECONDS:
        return False
    return elapsed <= SERVICE_WINDOW_SECONDS


def parse_inbound_events(
    payload: Any,
    *,
    now_epoch_s: Optional[int] = None,
    seen_effect_keys: Optional[MutableSet[str]] = None,
) -> List[InboundWhatsAppEvent]:
    """Extract normalized inbound events from a verified Cloud API webhook payload."""
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return []
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return []

    seen = seen_effect_keys if seen_effect_keys is not None else set()
    now_s = int(time.time()) if now_epoch_s is None else int(now_epoch_s)
    events: List[InboundWhatsAppEvent] = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict) or change.get("field") != "messages":
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            metadata = value.get("metadata") or {}
            phone_number_id = str(metadata.get("phone_number_id") or "").strip()
            if not _PHONE_ID_RE.match(phone_number_id):
                continue
            messages = value.get("messages")
            if not isinstance(messages, list):
                continue
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                wamid = str(msg.get("id") or "").strip()
                sender = normalize_recipient_digits(str(msg.get("from") or ""))
                raw_ts = str(msg.get("timestamp") or "").strip()
                if not _WAMID_RE.match(wamid) or sender is None or not raw_ts.isdigit():
                    continue
                ts_int = int(raw_ts)
                msg_type = str(msg.get("type") or "unknown").strip().lower()
                idem = str(
                    effect_key(
                        namespace="whatsapp_cloud",
                        operation="inbound_message",
                        scope={"phone_number_id": phone_number_id, "wamid": wamid},
                    )
                )
                in_window = is_within_free_service_window(ts_int, now_epoch_s=now_s)

                text_body = ""
                if msg_type == "text":
                    text_obj = msg.get("text")
                    if isinstance(text_obj, dict):
                        text_body = str(text_obj.get("body") or "").strip()[:MESSAGE_MAX_CHARS]

                if idem in seen:
                    stage = "duplicate_event"
                else:
                    seen.add(idem)
                    if msg_type != "text" or not text_body:
                        stage = "non_text_ignored"
                    elif not in_window:
                        stage = "outside_24h_service_window"
                    else:
                        stage = "accepted_text"

                events.append(
                    InboundWhatsAppEvent(
                        wamid=wamid,
                        sender_wa_id=sender,
                        phone_number_id=phone_number_id,
                        timestamp_s=ts_int,
                        message_type=msg_type,
                        text_body=text_body,
                        idempotency_key=idem,
                        within_24h_window=in_window,
                        stage=stage,
                    )
                )
    return events


def preflight() -> Dict[str, Any]:
    """Local-only check that bearer credential and phone_number_id exist before approval."""
    bearer = (os.environ.get("WHATSAPP_CLOUD_ACCESS_TOKEN") or "").strip()
    phone_id = (os.environ.get("WHATSAPP_CLOUD_PHONE_NUMBER_ID") or "").strip()
    if not bearer:
        return {"ok": False, "stage": "missing_access_credential"}
    if not _PHONE_ID_RE.match(phone_id):
        return {"ok": False, "stage": "invalid_phone_number_id"}
    return {"ok": True, "stage": "ok", "phone_number_id": phone_id}


def evaluate_outbound_approval(
    *,
    recipient_wa_id: str,
    reply_to_wamid: str,
    granted: Optional[bool],
    decided_by: str = "operator",
) -> ApprovalDecision:
    """Evaluate kernel fail-closed approval for an outbound WhatsApp reply."""
    req = ApprovalRequest(
        action="whatsapp_cloud.send_reply",
        reason="Customer-service window text reply",
        risk="medium",
        requested_by="whatsapp_cloud",
        metadata={
            "recipient_digits_len": len(recipient_wa_id),
            "has_reply_wamid": bool(reply_to_wamid),
        },
    )
    return decide(req, granted=granted, decided_by=decided_by)


def preflight_reply_payload(
    payload: Any,
    *,
    now_epoch_s: Optional[int] = None,
) -> Dict[str, Any]:
    """Validate local credentials, payload shape, and 24h free window BEFORE approval consume.

    Runners call this helper prior to ``GovernedExecutor.execute(...)`` so an
    expired 24-hour customer-service window or malformed payload never burns a
    single-use ``pa_approvals`` row.
    """
    pre = preflight()
    if not pre["ok"]:
        return {"ok": False, "stage": pre["stage"]}
    if not isinstance(payload, dict):
        return {"ok": False, "stage": "invalid_payload"}
    recipient = normalize_recipient_digits(str(payload.get("recipient_phone") or ""))
    if recipient is None:
        return {"ok": False, "stage": "invalid_recipient"}
    msg = payload.get("message_text")
    if not isinstance(msg, str) or not msg.strip() or len(msg.strip()) > MESSAGE_MAX_CHARS:
        return {"ok": False, "stage": "invalid_message"}
    reply_wamid = str(payload.get("reply_to_wamid") or "")
    if not _WAMID_RE.match(reply_wamid):
        return {"ok": False, "stage": "invalid_reply_wamid"}
    raw_ts = payload.get("inbound_timestamp_s")
    if isinstance(raw_ts, bool) or not isinstance(raw_ts, int):
        try:
            raw_ts = int(str(raw_ts))
        except (TypeError, ValueError):
            return {"ok": False, "stage": "invalid_timestamp"}
    if not is_within_free_service_window(int(raw_ts), now_epoch_s=now_epoch_s):
        return {"ok": False, "stage": "outside_24h_service_window"}
    return {"ok": True, "stage": "ok"}


def send_reply(
    *,
    recipient_phone: str,
    message_text: str,
    inbound_timestamp_s: int,
    reply_to_wamid: str,
    approval: Optional[ApprovalDecision] = None,
    now_epoch_s: Optional[int] = None,
    sent_effect_keys: Optional[MutableSet[str]] = None,
) -> Dict[str, Any]:
    """Send a free-window text reply fail-closed behind preflight, 24h guard, and approval."""
    pre = preflight()
    if not pre["ok"]:
        return {"ok": False, "stage": pre["stage"]}

    recipient = normalize_recipient_digits(recipient_phone)
    if recipient is None:
        return {"ok": False, "stage": "invalid_recipient"}

    if not isinstance(message_text, str):
        return {"ok": False, "stage": "invalid_message"}
    cleaned_msg = message_text.strip()
    if not cleaned_msg or len(cleaned_msg) > MESSAGE_MAX_CHARS:
        return {"ok": False, "stage": "invalid_message"}

    if not _WAMID_RE.match(reply_to_wamid or ""):
        return {"ok": False, "stage": "invalid_reply_wamid"}

    if not is_within_free_service_window(inbound_timestamp_s, now_epoch_s=now_epoch_s):
        return {"ok": False, "stage": "outside_24h_service_window"}

    verdict = approval or evaluate_outbound_approval(
        recipient_wa_id=recipient,
        reply_to_wamid=reply_to_wamid,
        granted=None,
    )
    if not verdict.approved:
        stage = (
            "approval_denied"
            if verdict.state == ApprovalState.DENIED
            else "approval_pending"
        )
        return {"ok": False, "stage": stage}

    phone_id = pre["phone_number_id"]
    outbound_idem = str(
        effect_key(
            namespace="whatsapp_cloud",
            operation="send_reply",
            scope={
                "phone_number_id": phone_id,
                "recipient": recipient,
                "reply_to_wamid": reply_to_wamid,
                "message_sha256": sha256_text(cleaned_msg),
            },
        )
    )
    if sent_effect_keys is not None and outbound_idem in sent_effect_keys:
        return {
            "ok": False,
            "stage": "duplicate_outbound_reply",
            "idempotency_key": outbound_idem,
        }

    bearer = (os.environ.get("WHATSAPP_CLOUD_ACCESS_TOKEN") or "").strip()
    url = f"{GRAPH_API_BASE}/{int(phone_id)}/messages"
    body = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "context": {"message_id": reply_to_wamid},
        "type": "text",
        "text": {"preview_url": False, "body": cleaned_msg},
    }
    res = _transport("POST", url, body, bearer)
    if res.stage != "ok":
        return {"ok": False, "stage": res.stage, "status": res.status}
    if not isinstance(res.data, dict) or not isinstance(res.data.get("messages"), list):
        return {"ok": False, "stage": "shape_invalid", "status": res.status}
    first = res.data["messages"][0] if res.data["messages"] else {}
    outbound_id = str(first.get("id") or "") if isinstance(first, dict) else ""
    if not outbound_id:
        return {"ok": False, "stage": "shape_invalid", "status": res.status}
    if sent_effect_keys is not None:
        sent_effect_keys.add(outbound_idem)
    return {
        "ok": True,
        "stage": "ok",
        "status": res.status,
        "outbound_message_id": outbound_id,
        "idempotency_key": outbound_idem,
    }


def make_send_reply_handler(
    *,
    now_epoch_fn: Optional[Callable[[], int]] = None,
    sent_effect_keys: Optional[MutableSet[str]] = None,
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """Build a ``GovernedExecutor`` tool handler for ``whatsapp_cloud.send_reply``.

    ``GovernedExecutor`` invokes this handler only after passing all four write
    gates (single-use ``pa_approvals`` row + ``payload_hash`` match + active
    ``pa_delegation_grants`` + consume-before-dispatch). Any delivery or 24-hour
    window refusal raises a stage-only :class:`WhatsAppCloudSendError` so the
    chained audit log records a clean stage without leaking credentials.
    """

    def handler(payload: Dict[str, Any]) -> Dict[str, Any]:
        raw_ts = payload.get("inbound_timestamp_s", -1)
        try:
            inbound_ts = int(raw_ts)
        except (TypeError, ValueError) as exc:
            raise WhatsAppCloudSendError("whatsapp_cloud_invalid_timestamp") from exc

        recipient = str(payload.get("recipient_phone") or "")
        reply_wamid = str(payload.get("reply_to_wamid") or "")
        approval = evaluate_outbound_approval(
            recipient_wa_id=normalize_recipient_digits(recipient) or "",
            reply_to_wamid=reply_wamid,
            granted=True,
            decided_by="governed_executor",
        )
        now_val = now_epoch_fn() if now_epoch_fn is not None else None
        result = send_reply(
            recipient_phone=recipient,
            message_text=str(payload.get("message_text") or ""),
            inbound_timestamp_s=inbound_ts,
            reply_to_wamid=reply_wamid,
            approval=approval,
            now_epoch_s=now_val,
            sent_effect_keys=sent_effect_keys,
        )
        if not result.get("ok"):
            stage = str(result.get("stage") or "send_failed")
            raise WhatsAppCloudSendError("whatsapp_cloud_" + stage)
        return {
            "delivered": True,
            "stage": "ok",
            "outbound_message_id": result["outbound_message_id"],
            "idempotency_key": result["idempotency_key"],
        }

    return handler


_SECURITY_TAMPER_STAGES = frozenset(
    {
        "invalid_signature",
        "malformed_signature",
        "missing_signature",
        "payload_hash_mismatch",
        "challenge_mismatch",
        "invalid_challenge",
    }
)
_IDEMPOTENCY_STAGES = frozenset(
    {
        "duplicate_event",
        "duplicate_outbound_reply",
        "approval_already_consumed",
    }
)
_POLICY_BUDGET_STAGES = frozenset(
    {
        "outside_24h_service_window",
        "limits_exceeded",
        "daily_limit_exceeded",
        "action_explicitly_denied",
        "action_not_in_allowed_actions",
        "approval_denied",
        "approval_pending",
        "non_text_ignored",
    }
)


def classify_audit_stage(stage_or_reason: str) -> str:
    """Classify a stage/denial reason for monitoring & alert routing.

    Returns one of:
    - ``"security_tamper"``: signature or payload-hash mismatch (P1 alert).
    - ``"idempotency_guard"``: benign replay/duplicate suppression.
    - ``"policy_budget_gate"``: $0 24h window, daily cap, or delegation/approval gate.
    - ``"protocol_or_transport"``: HTTP/network/config/shape warning.
    """
    normalized = str(stage_or_reason or "").strip().lower()
    if normalized.startswith("tool_execution_error:"):
        normalized = normalized.split(":", 1)[1]
    if normalized.startswith("whatsapp_cloud_"):
        normalized = normalized[len("whatsapp_cloud_") :]
    prefix = normalized.split(":", 1)[0]
    if prefix in _SECURITY_TAMPER_STAGES:
        return "security_tamper"
    if prefix in _IDEMPOTENCY_STAGES:
        return "idempotency_guard"
    if prefix in _POLICY_BUDGET_STAGES:
        return "policy_budget_gate"
    return "protocol_or_transport"


__all__ = [
    "CloudTransportResult",
    "GRAPH_API_BASE",
    "InboundWhatsAppEvent",
    "MESSAGE_MAX_CHARS",
    "SERVICE_WINDOW_SECONDS",
    "WhatsAppCloudSendError",
    "classify_audit_stage",
    "evaluate_outbound_approval",
    "is_within_free_service_window",
    "make_send_reply_handler",
    "normalize_recipient_digits",
    "parse_inbound_events",
    "preflight",
    "preflight_reply_payload",
    "send_reply",
    "verify_hub_signature",
    "verify_webhook_challenge",
]
