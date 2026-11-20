#!/usr/bin/env python3
"""Render Control Adapter — a read-first, policy-gated client for the Render API.

DESIGN DECISION (and why it is not inside the kernel)
=====================================================
The kernel in ``projects/agi-system`` is frozen and deterministic: it plans,
authorises, executes and verifies filesystem goals. Talking to a third-party
cloud API over the network is the opposite of that — non-deterministic, remote,
rate-limited, and able to destroy production. It therefore lives **outside** the
kernel, as an adapter with an explicit policy engine in front of it, exactly as
proposed:

    agents -> [policy gate] -> adapter -> Render API
                    |
                    +-> approval token (for mutations)
                    +-> audit log (always)
                    +-> redaction (never leak secret values)

THREE HARD RULES, EACH ENFORCED IN CODE (not in documentation)
--------------------------------------------------------------
1. **Reads are automatic, mutations are not.** Every mutating endpoint requires
   an approval token minted by ``ApprovalEngine``. There is no "force" flag that
   skips it; an unknown or expired token is refused.
2. **Secret values are never returned.** ``GET /env-vars`` returns the real
   values, so the adapter redacts them at the boundary and offers
   ``list_env_vars_masked()`` instead. ``get_env_var_value()`` exists but is
   gated behind an explicit, audited, approval-token call, and its result is
   marked ``sensitive`` — it is never written to the audit log.
3. **A successful deploy request is not a successful deploy.** ``deploy()``
   returns the accepted deploy object with ``verified: false``. Only
   ``wait_for_deploy()`` — which polls the deploy to a terminal state *and* then
   checks the service is live — may set ``verified: true``.

MEASURED, NOT ASSUMED
---------------------
This module was written without live API access: this sandbox's egress policy
blocks api.render.com (verified: TLS reset, while api.github.com returns 200).
So the response *shapes* below come from Render's official docs, and every one
of them is exercised against recorded fixtures in ``test_adapter.py``. The
adapter has **never** been proven against the live API. ``probe.py`` is the
Phase 0 read-only audit that does that, and it must be run by someone with a
key before any of this is trusted.

Documented shapes used (source: api-docs.render.com):
  * list endpoints return ``[{"<resource>": {...}, "cursor": "..."}]`` — wrapped
  * auth is ``Authorization: Bearer <key>``
  * pagination is cursor-based, ``limit`` default 20, max 100
  * rate limits: POST /services 20/hour; deploy & PATCH /services 10/min/service;
    all other POST/PATCH/DELETE 30/min; GET /logs 30/min

Stdlib only.
"""
from __future__ import annotations

# Make the sibling kernel importable from a bare checkout before it is used.
import _kernel_path  # noqa: F401,E402  isort: skip

import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE = "https://api.render.com/v1"

#: Values shorter than this are not treated as secret material when redacting
#: log lines; masking every 3-char string would destroy the logs' usefulness.
MIN_SECRET_LEN = 6

#: Hard ceiling on pages fetched by a list call. A cursor bug that never
#: terminates must not turn into an infinite loop against a rate-limited API.
MAX_PAGES = 50

#: Official documented limits (per api-docs.render.com/reference/rate-limiting).
RATE_LIMITS = {
    "create_service": (20, 3600),
    "deploy": (10, 60),
    "update_service": (10, 60),
    "other_mutation": (30, 60),
    "list_logs": (30, 60),
}


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #
#: operation -> (risk, approval required, human-readable policy)
POLICY: dict[str, tuple[str, bool, str]] = {
    # ---- reads: automatic ----
    "list_services":        ("read",     False, "automatic"),
    "get_service":          ("read",     False, "automatic"),
    "list_deploys":         ("read",     False, "automatic"),
    "get_deploy":           ("read",     False, "automatic"),
    "list_events":          ("read",     False, "automatic"),
    "list_logs":            ("read",     False, "automatic"),
    "list_env_vars_masked": ("read",     False, "automatic (values redacted)"),
    "list_owners":          ("read",     False, "automatic"),
    # ---- mutations: approval required ----
    "restart_service":      ("restart",  True,  "approval required"),
    "deploy":               ("deploy",   True,  "approval required"),
    "update_service":       ("deploy",   True,  "approval required"),
    "set_env_var":          ("secret",   True,  "explicit approval required"),
    # ---- dangerous: always explicit ----
    "delete_service":       ("destroy",  True,  "ALWAYS explicit approval"),
    # ---- never automatic, ever ----
    "get_env_var_value":    ("secret",   True,  "explicit approval + audited; "
                                                "never logged"),
}


class PolicyError(PermissionError):
    """Raised when a call is not allowed by policy (e.g. no valid token)."""


class RateLimitError(RuntimeError):
    """Raised *before* sending when the local limiter would exceed a quota."""


class ApiError(RuntimeError):
    def __init__(self, status: int, code: str, body: str, path: str):
        self.status, self.code, self.body, self.path = status, code, body, path
        super().__init__(f"HTTP {status} {code} on {path}: {body[:200]}")

    @property
    def is_auth(self) -> bool:
        return self.status in (401, 403)

    @property
    def is_rate_limited(self) -> bool:
        return self.status == 429

    @property
    def is_not_found(self) -> bool:
        return self.status == 404


# --------------------------------------------------------------------------- #
# Approval
# --------------------------------------------------------------------------- #
@dataclass
class Approval:
    token: str
    operation: str
    expires_at: float
    reason: str = ""
    single_use: bool = True


class ApprovalEngine:
    """Mints and checks short-lived, single-use, operation-scoped tokens.

    Deliberately local and HMAC-based rather than "the agent says it is fine":
    a token is bound to (operation, target, minute) so it cannot be reused for a
    different service or replayed later.
    """

    def __init__(self, secret: bytes | None = None, ttl: float = 300.0):
        self._secret = secret or os.urandom(32)
        self.ttl = ttl
        self._used: set[str] = set()
        self._nonce = 0

    def _sig(self, operation: str, target: str, bucket: int, nonce: int) -> str:
        msg = f"{operation}|{target}|{bucket}|{nonce}".encode()
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()[:32]

    def mint(self, operation: str, target: str, reason: str = "") -> Approval:
        if operation not in POLICY:
            raise PolicyError(f"unknown operation {operation!r}")
        if not POLICY[operation][1]:
            raise PolicyError(f"{operation!r} is automatic; it needs no approval")
        # The nonce makes each minted token UNIQUE. Without it, two approvals of
        # the same operation in the same minute produced the same string, and the
        # single-use check rejected the second one — i.e. the guard blocked
        # legitimate operations and would have looked like a policy bug. Found by
        # test_40 (ten deploys in a row).
        self._nonce += 1
        bucket = int(time.time() // 60)
        token = f"{self._nonce:08x}{self._sig(operation, target, bucket, self._nonce)}"
        return Approval(token, operation, time.time() + self.ttl, reason)

    def check(self, token: str, operation: str, target: str) -> bool:
        """True only for a live, unused token minted for THIS operation+target."""
        if not token or token in self._used or len(token) < 40:
            return False
        try:
            nonce = int(token[:8], 16)
        except ValueError:
            return False
        bucket = int(time.time() // 60)
        # accept the current minute and the previous one (clock skew / slow human)
        for b in (bucket, bucket - 1):
            expected = f"{nonce:08x}{self._sig(operation, target, b, nonce)}"
            if hmac.compare_digest(token, expected):
                self._used.add(token)
                return True
        return False


# --------------------------------------------------------------------------- #
# Rate limiter
# --------------------------------------------------------------------------- #
class LocalRateLimiter:
    """Token bucket matching Render's documented limits.

    Refusing locally is not politeness: a 429 from Render mid-deploy is worse
    than an honest local refusal, because the deploy may already be queued.
    """

    def __init__(self):
        self._hits: dict[str, list[float]] = {}

    def check(self, kind: str) -> None:
        limit, window = RATE_LIMITS.get(kind, RATE_LIMITS["other_mutation"])
        now = time.time()
        hits = [t for t in self._hits.get(kind, []) if now - t < window]
        if len(hits) >= limit:
            wait = window - (now - hits[0])
            raise RateLimitError(
                f"local limit for {kind!r} is {limit}/{window}s; retry in {wait:.0f}s")
        hits.append(now)
        self._hits[kind] = hits


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #
# This adapter's masked marker appears in generated reports consumed downstream,
# so the spelling is pinned here rather than silently becoming the kernel default.
MARKER = "***REDACTED***"

from kernel2027.redaction import (  # noqa: E402 - kernel is stdlib-only
    redact as kernel_redact,
    redact_mapping as kernel_redact_mapping,
)

SECRET_KEY_RE = re.compile(r"(key|token|secret|password|passwd|pwd|credential|auth)",
                           re.IGNORECASE)


def redact(value, secrets: list[str] | None = None):
    """Mask anything that looks like a secret, recursively.

    Delegates to :func:`kernel2027.redact_mapping` so this adapter, the protocol
    package and the fleet all apply the *same* key heuristics and pattern list.
    Key-name masking and literal-value scrubbing both live in the kernel now.
    """
    if isinstance(value, dict):
        return kernel_redact_mapping(value, secrets=secrets, marker=MARKER)
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        return kernel_redact(value, secrets, marker=MARKER)
    return value


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
class AuditLog:
    """Append-only JSONL. Never receives a secret VALUE, only key names."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, **event) -> dict:
        event.setdefault("ts", time.time())
        event.setdefault("ts_iso", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        if self.path:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event


# --------------------------------------------------------------------------- #
# Transport (injectable, so tests need no network)
# --------------------------------------------------------------------------- #
def urllib_transport(method: str, url: str, headers: dict, body: bytes | None,
                     timeout: float) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()


def recording_transport(status: int, body: bytes, headers: dict | None = None):
    """A transport that returns a canned response and records the request.

    The inner function's REQUEST-body parameter is named `request_body`. The
    first version also called it `body`, which shadowed the canned RESPONSE and
    made every call return ``None`` — 16 tests failed with
    "invalid_json: Expecting value ... None". A fake that cannot tell a request
    from a response is not a fake of anything.
    """
    calls: list[dict] = []
    canned_body = body

    def transport(method, url, hdrs, request_body, timeout):
        calls.append({"method": method, "url": url, "headers": dict(hdrs),
                      "body": request_body})
        return status, headers or {}, canned_body

    transport.calls = calls  # type: ignore[attr-defined]
    return transport


# --------------------------------------------------------------------------- #
# The adapter
# --------------------------------------------------------------------------- #
@dataclass
class RenderClient:
    api_key: str = ""
    base: str = DEFAULT_BASE
    transport: object = urllib_transport
    approvals: ApprovalEngine = field(default_factory=ApprovalEngine)
    limiter: LocalRateLimiter = field(default_factory=LocalRateLimiter)
    audit: AuditLog = field(default_factory=AuditLog)
    timeout: float = 20.0
    #: every secret VALUE seen this session, used to scrub logs
    _secrets: list[str] = field(default_factory=list, repr=False)

    # -- plumbing -------------------------------------------------------- #
    def _headers(self) -> dict:
        h = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _request(self, method: str, path: str, *, query: dict | None = None,
                 payload: dict | None = None, limit_kind: str = "other_mutation",
                 redact_body: bool = True) -> tuple[object, dict]:
        url = self.base.rstrip("/") + path
        if query:
            clean = {k: v for k, v in query.items() if v is not None and v != []}
            if clean:
                url += "?" + urllib.parse.urlencode(clean, doseq=True)
        body = json.dumps(payload).encode() if payload is not None else None
        if method != "GET":
            self.limiter.check(limit_kind)
        status, headers, raw = self.transport(method, url, self._headers(), body,
                                              self.timeout)  # type: ignore[misc]
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        meta = {"status": status, "headers": headers, "path": path,
                "request_id": (headers or {}).get("x-request-id")
                or (headers or {}).get("X-Request-Id")}
        if status >= 400:
            code = ""
            try:
                parsed = json.loads(text)
                code = str(parsed.get("code") or parsed.get("error") or "")
            except ValueError:
                pass
            raise ApiError(status, code or "error", text, path)
        if not text.strip():
            return None, meta
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ApiError(status, "invalid_json", f"{exc}: {text[:120]}", path)
        return (redact(data, self._secrets) if redact_body else data), meta

    def _unwrap(self, data, key: str) -> list[dict]:
        """Render list endpoints wrap items: ``[{"service": {...}, "cursor": ...}]``.

        Accepting a bare object as well costs nothing and avoids a silent empty
        list if the shape ever changes — but a *wrapped* response is what the
        docs specify, and the adapter treats a mismatch as an error rather than
        guessing.
        """
        if data is None:
            return []
        if not isinstance(data, list):
            raise ApiError(200, "unexpected_shape",
                           f"expected a list of {key!r} wrappers, got {type(data).__name__}",
                           "")
        out = []
        for item in data:
            if isinstance(item, dict) and key in item:
                entry = dict(item[key])
                entry["_cursor"] = item.get("cursor")
                out.append(entry)
            elif isinstance(item, dict):
                out.append(item)          # tolerated: bare object
            else:
                raise ApiError(200, "unexpected_shape", f"item is {type(item).__name__}", "")
        return out

    def _approve(self, operation: str, target: str, token: str | None) -> None:
        """The single gate every mutation passes through."""
        if not self.approvals.check(token or "", operation, target):
            self.audit.write(event="approval_denied", operation=operation,
                             target=target, supplied=bool(token))
            risk, needed, policy = POLICY[operation]
            raise PolicyError(
                f"{operation!r} on {target!r} requires a valid approval token "
                f"(policy: {policy}). No token, a token for another operation or "
                f"target, a reused token, or an expired token all land here.")

    # -- reads (automatic) ------------------------------------------------ #
    def list_services(self, limit: int = 100) -> list[dict]:
        out: list[dict] = []
        cursor = None
        for _ in range(MAX_PAGES):
            data, _meta = self._request("GET", "/services",
                                        query={"limit": min(limit, 100),
                                               "cursor": cursor})
            page = self._unwrap(data, "service")
            out.extend(page)
            cursor = page[-1].get("_cursor") if page else None
            if not cursor or len(page) < min(limit, 100):
                break
        return out

    def get_service(self, service_id: str) -> dict:
        data, _ = self._request("GET", f"/services/{service_id}")
        return self._unwrap_one(data, "service")

    def list_deploys(self, service_id: str, limit: int = 20) -> list[dict]:
        data, _ = self._request("GET", f"/services/{service_id}/deploys",
                                query={"limit": min(limit, 100)})
        return self._unwrap(data, "deploy")

    def get_deploy(self, service_id: str, deploy_id: str) -> dict:
        data, _ = self._request("GET", f"/services/{service_id}/deploys/{deploy_id}")
        return self._unwrap_one(data, "deploy")

    def list_events(self, service_id: str, limit: int = 20) -> list[dict]:
        data, _ = self._request("GET", f"/services/{service_id}/events",
                                query={"limit": min(limit, 100)})
        return self._unwrap(data, "event")

    def list_logs(self, owner_id: str, resource: list[str],
                  start: str | None = None, end: str | None = None,
                  limit: int = 100) -> dict:
        self.limiter.check("list_logs")
        data, meta = self._request("GET", "/logs",
                                   query={"ownerId": owner_id, "resource": resource,
                                          "startTime": start, "endTime": end,
                                          "limit": limit})
        if not isinstance(data, dict):
            raise ApiError(200, "unexpected_shape", "logs must be an object", "")
        logs = data.get("logs") or []
        return {"logs": redact(logs, self._secrets), "hasMore": bool(data.get("hasMore")),
                "nextStartTime": data.get("nextStartTime"),
                "nextEndTime": data.get("nextEndTime"), "meta": meta}

    def list_owners(self) -> list[dict]:
        data, _ = self._request("GET", "/owners", query={"limit": 100})
        return self._unwrap(data, "owner")

    def list_env_vars_masked(self, service_id: str) -> list[dict]:
        """Env vars WITHOUT their values — key names only.

        ``GET /services/{id}/env-vars`` returns real values. This method keeps
        them out of the caller's hands entirely: only keys, generation markers
        and a value length. A caller who needs the value must use
        ``get_env_var_value`` with an approval token.
        """
        raw, _ = self._request("GET", f"/services/{service_id}/env-vars",
                               redact_body=False)
        items = self._unwrap(raw, "envVar")
        masked = []
        for item in items:
            key = item.get("key")
            val = item.get("value") or ""
            if isinstance(val, str) and val and len(val) >= MIN_SECRET_LEN:
                self._remember_secret(val)
            masked.append({"key": key,
                           "value_present": bool(val),
                           "value_len": len(val) if isinstance(val, str) else None,
                           "generated": item.get("generateValue", False)})
        self.audit.write(event="env_vars_read_masked", service_id=service_id,
                         count=len(masked))
        return masked

    def get_env_var_value(self, service_id: str, key: str,
                          approval_token: str | None = None) -> dict:
        """Return a real secret value. Approval-gated and never audited by value."""
        self._approve("get_env_var_value", f"{service_id}:{key}", approval_token)
        raw, _ = self._request("GET", f"/services/{service_id}/env-vars",
                               redact_body=False)
        for item in self._unwrap(raw, "envVar"):
            if item.get("key") == key:
                value = item.get("value") or ""
                self._remember_secret(value)
                # the audit records the KEY, never the value
                self.audit.write(event="secret_value_read", service_id=service_id,
                                 key=key, chars=len(value), sensitive=True)
                return {"key": key, "value": value, "sensitive": True,
                        "note": "never log, never forward to another agent"}
        raise ApiError(404, "not_found", f"env var {key!r} not on {service_id}", "")

    def _remember_secret(self, value: str) -> None:
        if value and len(value) >= MIN_SECRET_LEN and value not in self._secrets:
            self._secrets.append(value)

    @staticmethod
    def _unwrap_one(data, key: str) -> dict:
        """Accept every single-resource shape Render is documented or observed to use.

        ``GET /services/{id}`` returns a 200 whose body is a LIST containing ONE
        ``{"service": {...}, "cursor": ...}`` envelope — the same envelope as the
        collection endpoint, not a bare object. The first version only handled an
        object and raised "unexpected_shape" on the real shape, which would have
        broken `get_service`, `delete_service`'s name check and `service_is_live`.
        Found by the offline fixtures before any live call was attempted.
        """
        if isinstance(data, dict) and key in data:
            return data[key]
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and key in item:
                    return item[key]
                if isinstance(item, dict):
                    return item
            raise ApiError(200, "empty_response", f"list contained no {key!r}", "")
        if isinstance(data, dict):
            return data
        raise ApiError(200, "unexpected_shape",
                       f"expected object or single-item list with {key!r}, "
                       f"got {type(data).__name__}", "")

    # -- mutations (approval required) ------------------------------------ #
    def restart_service(self, service_id: str, approval_token: str | None = None) -> dict:
        self._approve("restart_service", service_id, approval_token)
        data, meta = self._request("POST", f"/services/{service_id}/restart",
                                   payload={}, limit_kind="other_mutation")
        self.audit.write(event="restart_requested", service_id=service_id,
                         request_id=meta["request_id"], verified=False)
        return {"requested": True, "service_id": service_id,
                "verified": False,
                "note": "request accepted; liveness must be checked separately",
                "meta": meta}

    def deploy(self, service_id: str, commit_id: str | None = None,
               approval_token: str | None = None,
               clear_cache: bool = False) -> dict:
        """Trigger a deploy. A 200/202 here proves NOTHING about the app."""
        self._approve("deploy", service_id, approval_token)
        payload: dict = {"clearCache": "clear" if clear_cache else "do_not_clear"}
        if commit_id:
            payload["commitId"] = commit_id
        data, meta = self._request("POST", f"/services/{service_id}/deploys",
                                   payload=payload, limit_kind="deploy")
        dep = self._unwrap_one(data, "deploy") if data else {}
        self.audit.write(event="deploy_requested", service_id=service_id,
                         commit_id=commit_id, deploy_id=dep.get("id"),
                         request_id=meta["request_id"], verified=False)
        return {"deploy_id": dep.get("id"), "status": dep.get("status"),
                "commit_id": dep.get("commit", {}).get("id") if isinstance(dep.get("commit"), dict) else commit_id,
                # DELIBERATELY false. Only wait_for_deploy() may set this true.
                "verified": False,
                "note": "accepted, not verified. Call wait_for_deploy() before "
                        "claiming success.",
                "meta": meta}

    def set_env_var(self, service_id: str, key: str, value: str,
                    approval_token: str | None = None) -> dict:
        """Set one env var. The VALUE is sent but never logged or returned."""
        self._approve("set_env_var", f"{service_id}:{key}", approval_token)
        data, meta = self._request("PUT", f"/services/{service_id}/env-vars/{key}",
                                   payload={"value": value},
                                   limit_kind="other_mutation",
                                   redact_body=False)
        self._remember_secret(value)
        self.audit.write(event="env_var_set", service_id=service_id, key=key,
                         chars=len(value), request_id=meta["request_id"],
                         sensitive=True)
        return {"service_id": service_id, "key": key, "value_set": True,
                "value_echoed": False, "meta": meta}

    def delete_service(self, service_id: str, approval_token: str | None = None,
                       confirm_name: str | None = None) -> dict:
        """Delete. Requires approval AND the service's exact name as confirmation."""
        self._approve("delete_service", service_id, approval_token)
        if confirm_name is not None:
            actual = (self.get_service(service_id) or {}).get("name")
            if actual != confirm_name:
                raise PolicyError(
                    f"delete refused: confirm_name {confirm_name!r} != actual {actual!r}")
        else:
            raise PolicyError("delete refused: confirm_name is mandatory")
        self._request("DELETE", f"/services/{service_id}",
                      limit_kind="other_mutation", redact_body=False)
        self.audit.write(event="service_deleted", service_id=service_id,
                         name=confirm_name)
        return {"deleted": True, "service_id": service_id, "name": confirm_name}

    # -- verification ------------------------------------------------------ #
    TERMINAL_OK = ("live", "deactivated")
    TERMINAL_BAD = ("build_failed", "update_failed", "canceled", "pre_deploy_failed")

    def wait_for_deploy(self, service_id: str, deploy_id: str,
                        timeout: float = 900.0, poll: float = 5.0,
                        sleep=time.sleep) -> dict:
        """Poll to a terminal state. This is the ONLY path to ``verified: true``.

        Even a ``live`` deploy is not proof the app works — it proves Render
        considers the instance live. The caller is told exactly which claim is
        being made, and ``service_is_live()`` is offered as a stronger check.
        """
        t0 = time.time()
        last = {}
        while time.time() - t0 < timeout:
            last = self.get_deploy(service_id, deploy_id)
            status = str(last.get("status", ""))
            if status in self.TERMINAL_OK:
                self.audit.write(event="deploy_terminal", service_id=service_id,
                                 deploy_id=deploy_id, status=status, verified=True)
                return {"deploy_id": deploy_id, "status": status, "verified": True,
                        "elapsed_s": round(time.time() - t0, 1),
                        "claim": "Render reports this deploy reached a terminal "
                                 "successful state; it does NOT prove the app "
                                 "serves traffic correctly"}
            if status in self.TERMINAL_BAD:
                self.audit.write(event="deploy_terminal", service_id=service_id,
                                 deploy_id=deploy_id, status=status, verified=False)
                return {"deploy_id": deploy_id, "status": status, "verified": False,
                        "elapsed_s": round(time.time() - t0, 1),
                        "claim": "deploy failed; do not report success"}
            sleep(poll)
        self.audit.write(event="deploy_timeout", service_id=service_id,
                         deploy_id=deploy_id, last_status=last.get("status"))
        return {"deploy_id": deploy_id, "status": last.get("status"),
                "verified": False, "timed_out": True,
                "claim": f"no terminal state within {timeout}s — UNKNOWN, not success"}

    def service_is_live(self, service_id: str, probes: int = 3,
                        interval: float = 2.0, sleep=time.sleep) -> dict:
        """Independent liveness: fetch the service ``probes`` times and require
        every read to report not-suspended with a live URL.

        This is still Render's own view, not an HTTP fetch of the app. It is
        stronger than ``verified`` on a deploy, and weaker than an end-to-end
        check — ``probe_app_url()`` does that when the URL is public.
        """
        seen = []
        for i in range(probes):
            svc = self.get_service(service_id)
            seen.append({"suspended": svc.get("suspended"),
                         "type": svc.get("type"),
                         "url": (svc.get("serviceDetails") or {}).get("url")})
            if i + 1 < probes:
                sleep(interval)
        ok = all(s["suspended"] in ("not_suspended", False, None) for s in seen)
        return {"service_id": service_id, "probes": seen, "live": ok,
                "claim": "Render reports the service not-suspended; this is not "
                         "an end-to-end request to the application"}

    def probe_app_url(self, url: str) -> dict:
        """End-to-end: actually GET the deployed URL."""
        req = urllib.request.Request(url, headers={"Accept": "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return {"url": url, "status": r.status, "ok": 200 <= r.status < 400}
        except urllib.error.HTTPError as e:
            return {"url": url, "status": e.code, "ok": False}
        except (urllib.error.URLError, OSError) as e:
            return {"url": url, "status": None, "ok": False, "error": str(e)}


# --------------------------------------------------------------------------- #
# Log diagnosis (read-only, no network)
# --------------------------------------------------------------------------- #
ERROR_PATTERNS: list[tuple[str, str]] = [
    (r"ModuleNotFoundError|No module named", "missing_dependency"),
    (r"Address already in use|EADDRINUSE", "port_conflict"),
    (r"OOMKilled|out of memory|MemoryError", "out_of_memory"),
    (r"PermissionError|EACCES", "permission_denied"),
    (r"ECONNREFUSED|Connection refused", "connection_refused"),
    (r"Timeout|timed out|ETIMEDOUT", "timeout"),
    (r"syntax error|SyntaxError|IndentationError", "syntax_error"),
    (r"Traceback \(most recent call last\)", "unhandled_exception"),
    (r"exit status 1|exited with code [1-9]|Error: command failed", "process_exit_nonzero"),
    (r"SSL|certificate|TLS", "tls_error"),
    (r"disk|No space left", "disk_full"),
]


def diagnose(log_lines: list[str]) -> dict:
    """Classify error lines. Reports what it cannot classify instead of guessing."""
    counts: dict[str, int] = {}
    samples: dict[str, str] = {}
    unclassified = 0
    errorish = 0
    # A line must LOOK like a problem before it is classified. The first version
    # only matched error|exception|fail|refused|traceback|fatal|panic, which
    # silently skipped "Killed process 1 (python3) out of memory" — OOM kills are
    # a leading cause of service death and their log lines usually never say
    # "error". Found by the first run of test_60.
    problem_re = (r"error|exception|fail|refused|traceback|fatal|panic|crash|"
                  r"\bkilled\b|\boom\b|out of memory|memoryerror|"
                  r"\bexit(ed)? with (code|status)\b|\babort")
    for line in log_lines:
        if not re.search(problem_re, line, re.IGNORECASE):
            continue
        errorish += 1
        for pattern, label in ERROR_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                counts[label] = counts.get(label, 0) + 1
                samples.setdefault(label, line.strip()[:300])
                break
        else:
            unclassified += 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    return {"error_lines": errorish, "classified": sum(counts.values()),
            "unclassified": unclassified,
            "causes": [{"cause": c, "count": n, "sample": samples[c]}
                       for c, n in ranked],
            "note": "a classification is a HYPOTHESIS; it is not a fix and it is "
                    "not evidence that the hypothesis is correct"}


# --------------------------------------------------------------------------- #
# Infrastructure audit (read-only)
# --------------------------------------------------------------------------- #
def audit_infrastructure(services: list[dict]) -> dict:
    """Find configuration risks in a service inventory. Read-only, no opinions
    presented as facts: every finding says what was observed."""
    findings = []
    for svc in services:
        name, sid = svc.get("name"), svc.get("id")
        details = svc.get("serviceDetails") or {}
        if svc.get("suspended") == "suspended":
            findings.append({"severity": "high", "service": name, "id": sid,
                             "issue": "suspended",
                             "detail": "service is suspended and serving nothing"})
        if svc.get("autoDeploy") is False:
            findings.append({"severity": "info", "service": name, "id": sid,
                             "issue": "auto_deploy_off",
                             "detail": "new commits will not deploy automatically"})
        if svc.get("type") == "web_service" and not details.get("healthCheckPath"):
            findings.append({"severity": "medium", "service": name, "id": sid,
                             "issue": "no_health_check_path",
                             "detail": "Render cannot distinguish a hung app from a "
                                       "healthy one"})
        if details.get("plan", "").startswith("free"):
            findings.append({"severity": "info", "service": name, "id": sid,
                             "issue": "free_plan",
                             "detail": "free instances sleep and are not an uptime SLA"})
        if not svc.get("branch"):
            findings.append({"severity": "low", "service": name, "id": sid,
                             "issue": "no_branch",
                             "detail": "no tracked branch; deploys are manual or "
                                       "image-based"})
    by_sev: dict[str, int] = {}
    for f in findings:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
    return {"services": len(services), "findings": findings, "by_severity": by_sev,
            "note": "absent findings are NOT proof of a healthy configuration: "
                    "the API does not expose secrets, billing or traffic"}
