#!/usr/bin/env python3
"""WAAPI (waapi.app) connector — stdlib only, structured fail-closed results.

Design rules (docs/WAAPI-INTEGRATION-PLAN.md):

- **No third-party imports** (ADR-0002): ``urllib`` only, like the approval
  bot's Telegram transport.
- **Never raise with, return, or log** the token, a provider response body,
  or a raw exception message — every failure is a *stage* string
  (``token_missing``, ``refused_422``, ``network_error``, …). Exceptions and
  bodies may carry account data; stages never do.
- **Read-only by default**: ``health()`` / ``instance_id()`` /
  ``client_status()`` only. ``send_text()`` is the single write entry point
  and is reached exclusively through the governed executor (approval +
  consume gates) or the owner-only opt-in notify path.
- **Preflight before consume**: the runner checks ``preflight()`` (token
  presence, no network) *before* the executor consumes an approval, so a
  missing token never burns a single-use approval.
- Transport is a module attribute (``_transport``) so hermetic tests swap
  it out — no network ever runs in CI.

Endpoint facts (waapi.readme.io, verified 2026-11-26):
  GET  /api/v1/instances
  GET  /api/v1/instances/{id}/client/status
  POST /api/v1/instances/{id}/client/action/send-message  {chatId, message}
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, NamedTuple, Optional

BASE_URL = "https://waapi.app/api/v1/instances"
TIMEOUT_SEC = 15
MAX_BODY_BYTES = 1024 * 1024
MESSAGE_MAX_CHARS = 4096

# chatId shapes accepted by the provider: direct user or group.
_CHAT_RE = re.compile(r"^\d{6,15}@(?:c\.us|g\.us)$")
# Instance ids are small positive integers (int64 in the API, never bool).
_ID_RE = re.compile(r"^[1-9]\d{0,18}$")
# Egyptian number shapes proven by the dr.mukhtar read-only probe.
_LOCAL_PHONE_RE = re.compile(r"^\+?[0-9 ()-]{8,24}$")
_EG_RE = re.compile(r"^20(?:10|11|12|15)\d{8}$")
_CLIENT_STATUSES = frozenset({
    "booting", "loading_screen", "qr", "authenticated",
    "auth_failure", "ready", "disconnected",
})


class TransportResult(NamedTuple):
    """Outcome of one HTTP round-trip — stage only, never a body copy."""

    stage: str          # ok | http_error | redirect_refused | too_large |
                        # not_json | network_error
    status: Optional[int]
    data: Any           # parsed JSON, only when stage == "ok"


class WaapiSendError(RuntimeError):
    """Raised from inside the executor tool — message is stage-only."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects (the read-only probe contract: redirect: error)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        return None


def _urllib_transport(method: str, url: str, body: Optional[Dict[str, Any]],
                      token: str) -> TransportResult:
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
    }
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=TIMEOUT_SEC) as response:
            status = getattr(response, "status", None) or response.getcode()
            raw = response.read(MAX_BODY_BYTES + 1)
    except urllib.error.HTTPError as exc:
        # Redirects surface as HTTPError with the NoRedirect handler.
        status = exc.code
        exc.close()          # never read or retain the provider body
        if 300 <= status < 400:
            return TransportResult("redirect_refused", status, None)
        return TransportResult("http_error", status, None)
    except (urllib.error.URLError, OSError, TimeoutError):
        # No str(exc): provider/runtime messages may contain sensitive detail.
        return TransportResult("network_error", None, None)
    if len(raw) > MAX_BODY_BYTES:
        return TransportResult("too_large", status, None)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return TransportResult("not_json", status, None)
    return TransportResult("ok", status, parsed)


#: Swappable transport — hermetic tests replace this attribute.
_transport = _urllib_transport


def _token() -> str:
    return os.getenv("WAAPI_API_TOKEN", "").strip()


def preflight() -> Dict[str, Any]:
    """Local, network-free precondition — the runner's pre-consume check."""
    token = _token()
    return {"ok": bool(token), "stage": "ok" if token else "token_missing"}


def _call(method: str, url: str,
          body: Optional[Dict[str, Any]] = None
          ) -> tuple[Dict[str, Any], Any]:
    token = _token()
    if not token:
        return {"ok": False, "stage": "token_missing"}, None
    result = _transport(method, url, body, token)
    if result.stage != "ok":
        return {"ok": False, "stage": result.stage,
                "http": result.status}, None
    return {"ok": True, "http": result.status}, result.data


def _shape_fail() -> Dict[str, Any]:
    return {"ok": False, "stage": "shape_invalid"}


def health() -> Dict[str, Any]:
    """Read-only: does the token authenticate and does the API answer?"""
    state, data = _call("GET", BASE_URL)
    if not state["ok"]:
        return state
    if (not isinstance(data, dict) or data.get("status") != "success"
            or not isinstance(data.get("instances"), list)):
        return _shape_fail()
    return {"ok": True, "stage": "instances_ok",
            "count": len(data["instances"])}


def instance_id() -> Dict[str, Any]:
    """Exactly one instance (env override wins) — never printed."""
    override = os.getenv("WAAPI_INSTANCE_ID", "").strip()
    if override:
        if not _ID_RE.match(override):
            return {"ok": False, "stage": "instance_id_invalid"}
        return {"ok": True, "instance_id": override,
                "stage": "instance_from_env"}
    state, data = _call("GET", BASE_URL)
    if not state["ok"]:
        return state
    if (not isinstance(data, dict) or data.get("status") != "success"
            or not isinstance(data.get("instances"), list)):
        return _shape_fail()
    instances = data["instances"]
    if len(instances) != 1:
        return {"ok": False, "stage": "instance_count_%d" % len(instances)}
    first = instances[0]
    raw_id = first.get("id") if isinstance(first, dict) else None
    if isinstance(raw_id, bool) or not isinstance(raw_id, (int, str)):
        return _shape_fail()
    iid = str(raw_id)
    if not _ID_RE.match(iid):
        return {"ok": False, "stage": "instance_id_invalid"}
    return {"ok": True, "instance_id": iid, "stage": "instance_discovered"}


def client_status(iid: str) -> Dict[str, Any]:
    """Read-only connection status of one instance."""
    state, data = _call("GET", "%s/%s/client/status" % (BASE_URL, iid))
    if not state["ok"]:
        return state
    if not isinstance(data, dict):
        return _shape_fail()
    observed = data.get("clientStatus")
    if (not isinstance(observed, dict)
            or observed.get("status") != "success"
            or str(observed.get("instanceId")) != iid
            or observed.get("instanceStatus") not in _CLIENT_STATUSES):
        return _shape_fail()
    return {"ok": True, "stage": "client_status",
            "status": observed.get("instanceStatus")}


def readiness() -> Dict[str, Any]:
    """Preflight + live client readiness — the runner's PRE-consume gate.

    Combines the local token check with the provider's own ``ready`` state,
    so an unlinked or disconnected WhatsApp instance never burns a
    single-use approval either.
    """
    pre = preflight()
    if not pre["ok"]:
        return pre
    iid = instance_id()
    if not iid["ok"]:
        return iid
    status = client_status(iid["instance_id"])
    if not status["ok"]:
        return status
    if status.get("status") != "ready":
        return {"ok": False, "stage": "not_ready",
                "status": status.get("status")}
    return {"ok": True, "stage": "ready"}


def send_text(chat_id: str, message: str) -> Dict[str, Any]:
    """The single write entry point. Structured result, never an exception.

    Readiness is enforced HERE as well (defense in depth): the POST never
    fires while the client is not ``ready`` — the live probe checks state,
    and this gate guarantees the send path cannot skip it.
    """
    chat = str(chat_id or "").strip()
    text = str(message or "").strip()
    if not _CHAT_RE.match(chat):
        return {"ok": False, "stage": "chat_id_invalid"}
    if not text or len(text) > MESSAGE_MAX_CHARS:
        return {"ok": False, "stage": "message_invalid"}
    iid = instance_id()
    if not iid["ok"]:
        return iid
    status = client_status(iid["instance_id"])
    if not status["ok"]:
        return status                    # transport/shape failure: no POST
    if status.get("status") != "ready":
        return {"ok": False, "stage": "not_ready",
                "status": status.get("status")}
    url = "%s/%s/client/action/send-message" % (BASE_URL, iid["instance_id"])
    state, data = _call("POST", url, {"chatId": chat, "message": text})
    if not state["ok"]:
        if state.get("http") == 422:
            # 422 = refused before send (unregistered number / reach-out cap).
            return {"ok": False, "stage": "refused_422"}
        return state
    if not isinstance(data, dict) or data.get("status") != "success":
        return {"ok": False, "stage": "send_shape_invalid"}
    return {"ok": True, "stage": "sent"}


def make_send_handler():
    """Executor tool handler: raises a stage-only error on delivery failure.

    The consume gate has already run by the time this fires (consume-before-
    dispatch), so a delivery refusal is audited as ``failed`` with a stage
    reason and never leaks provider detail.
    """
    def handler(payload: Dict[str, Any]) -> Dict[str, Any]:
        result = send_text(str(payload.get("chat_id", "")),
                           str(payload.get("message", "")))
        if not result.get("ok"):
            raise WaapiSendError("waapi_" + str(result.get("stage",
                                                           "send_failed")))
        return {"delivered": True, "stage": "sent"}
    return handler


# --------------------------------------------------------------------------
# Owner-only notify path (opt-in companion to the Telegram approval card)
# --------------------------------------------------------------------------
def normalize_egyptian(raw: Any) -> str:
    """Same normalisation the dr.mukhtar read-only probe proved in CI."""
    text = str(raw or "").strip()
    if not _LOCAL_PHONE_RE.match(text):
        return ""
    digits = re.sub(r"\D", "", text)
    if digits.startswith("0020"):
        digits = digits[2:]
    if digits.startswith("0"):
        digits = "20" + digits[1:]
    return digits if _EG_RE.match(digits) else ""


def owner_chat_id() -> str:
    """Fixed recipient: WAAPI_OWNER_CHAT_ID — the only notify destination."""
    raw = os.getenv("WAAPI_OWNER_CHAT_ID", "").strip()
    if not raw:
        return ""
    if _CHAT_RE.match(raw):
        return raw
    digits = normalize_egyptian(raw)
    return digits + "@c.us" if digits else ""


def notify_approval(ap: Dict[str, Any], vote_url: str) -> Dict[str, Any]:
    """WhatsApp companion for a pending approval card.

    Opt-in (``PA_WAAPI_NOTIFY=1``), owner-only recipient, fail-closed on
    every precondition. Returns a structured stage — never raises.
    """
    if os.getenv("PA_WAAPI_NOTIFY", "").strip() != "1":
        return {"ok": False, "stage": "notify_disabled"}
    chat = owner_chat_id()
    if not chat:
        return {"ok": False, "stage": "owner_chat_missing"}
    if not str(vote_url or "").strip():
        return {"ok": False, "stage": "vote_url_missing"}
    action = str(ap.get("action", "")).strip()
    text = ("🔐 طلب موافقة جديد: %s\n"
            "صفحة التصويت بمفتاح المرور (passkey):\n%s"
            % (action, vote_url))
    return send_text(chat, text)
