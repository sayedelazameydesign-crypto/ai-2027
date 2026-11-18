"""Strict, public-data-only request and receipt contracts for the local handoff.

Nothing here opens a browser or trusts a receipt as a source of authority.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REQUEST_SCHEMA = "ai-2027.browser-handoff.request.v1"
RECEIPT_SCHEMA = "ai-2027.browser-handoff.receipt.v1"
TASK = "observe-loopback-fixture"
MAX_TTL = 3600
DEFAULT_TTL = 1800
MAX_JSON_BYTES = 4096
SIGN_NAMESPACE = "ai-2027-browser-handoff"
SIGN_PRINCIPAL = "arena-browser-owner"
CHECKS = frozenset({
    "required_pairing_and_lab_instance_matched", "incognito_requested", "agent_tab_matched",
    "fixture_served", "marker_observed", "daemon_session_cleanup_confirmed",
})
_REQUEST_FIELDS = {"schema", "task", "request_id", "issued_at", "expires_at"}
_RECEIPT_FIELDS = {
    "schema", "task", "request_id", "request_sha256", "started_at", "completed_at",
    "local_status", "fixture_nonce", "observed_marker_sha256", "checks", "receipt_sha256",
}


class HandoffError(Exception):
    """Only fixed reason codes reach the CLI; do not print input or RPC errors."""

    def __init__(self, reason: str, *, status: str = "REJECTED", code: int = 1):
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.code = code


def canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def has_hex(value: Any, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and re.fullmatch(r"[0-9a-f]+", value) is not None


def _without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HandoffError("duplicate_json_key")
        result[key] = value
    return result


def read_document(path: Path) -> tuple[bytes, dict[str, Any]]:
    """Read once and cap size; the same bytes can be passed to signature verification."""
    try:
        if not path.is_file():
            raise HandoffError("document_not_regular_file")
        with path.open("rb") as stream:
            raw = stream.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise HandoffError("document_too_large")
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_without_duplicates)
    except OSError as exc:
        raise HandoffError("document_unreadable") from exc
    except (ValueError, RecursionError) as exc:
        raise HandoffError("invalid_json") from exc
    if not isinstance(payload, dict):
        raise HandoffError("document_not_object")
    return raw, payload


def _is_epoch(value: Any) -> bool:
    return type(value) is int and 0 < value < 4_102_444_800  # before 2100-01-01


@dataclass(frozen=True, slots=True)
class Request:
    """An immutable snapshot of the fixed task; intentionally no URL or action overrides."""

    request_id: str
    issued_at: int
    expires_at: int

    def document(self) -> dict[str, Any]:
        return {
            "schema": REQUEST_SCHEMA, "task": TASK, "request_id": self.request_id,
            "issued_at": self.issued_at, "expires_at": self.expires_at,
        }

    @property
    def digest(self) -> str:
        return sha256(canonical(self.document()))

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> Request:
        if set(payload) != _REQUEST_FIELDS or payload["schema"] != REQUEST_SCHEMA or payload["task"] != TASK:
            raise HandoffError("unsupported_request")
        ident, start, end = payload["request_id"], payload["issued_at"], payload["expires_at"]
        if not has_hex(ident, 32) or not _is_epoch(start) or not _is_epoch(end) or not 1 <= end - start <= MAX_TTL:
            raise HandoffError("invalid_request_id_or_window")
        return cls(ident, start, end)

    @classmethod
    def create(cls, ttl: int = DEFAULT_TTL, *, now: int | None = None) -> Request:
        if type(ttl) is not int or not 1 <= ttl <= MAX_TTL:
            raise HandoffError("invalid_ttl")
        start = int(time.time()) if now is None else now
        return cls.parse(cls(uuid.uuid4().hex, start, start + ttl).document())

    def require_digest(self, expected_sha256: str) -> None:
        """Recompute from this snapshot; never derive the reference from the request."""
        if not has_hex(expected_sha256, 64) or not hmac.compare_digest(self.digest, expected_sha256):
            raise HandoffError("request_digest_mismatch")

    def require_current(self, now: int) -> None:
        if not self.issued_at <= now < self.expires_at:
            raise HandoffError("request_outside_approval_window")


def marker(request: Request, fixture_nonce: str) -> str:
    return f"ARENA-HANDOFF-{request.request_id}-{fixture_nonce}"


def build_receipt(request: Request, fixture_nonce: str, observed_marker_sha256: str,
                  started_at: int, completed_at: int) -> dict[str, Any]:
    """Called only after the local runner confirms every gate and teardown."""
    body = {
        "schema": RECEIPT_SCHEMA, "task": TASK, "request_id": request.request_id,
        "request_sha256": request.digest, "local_status": "VERIFIED_LOCAL",
        "started_at": started_at, "completed_at": completed_at,
        "fixture_nonce": fixture_nonce, "observed_marker_sha256": observed_marker_sha256,
        "checks": {name: True for name in sorted(CHECKS)},
    }
    return {**body, "receipt_sha256": sha256(canonical(body))}


def verify_receipt(request: Request, receipt: dict[str, Any], *, now: int | None = None) -> str:
    """Verify schema, binding, chronology and digest — NOT provenance or live execution."""
    if set(receipt) != _RECEIPT_FIELDS or receipt["schema"] != RECEIPT_SCHEMA or receipt["task"] != TASK:
        raise HandoffError("unsupported_receipt")
    if receipt["local_status"] != "VERIFIED_LOCAL" or receipt["request_id"] != request.request_id or receipt["request_sha256"] != request.digest:
        raise HandoffError("request_receipt_mismatch")
    start, end = receipt["started_at"], receipt["completed_at"]
    now = int(time.time()) if now is None else now
    if not _is_epoch(start) or not _is_epoch(end) or not request.issued_at <= start <= end < request.expires_at or end > now:
        raise HandoffError("receipt_outside_request_window")
    checks = receipt["checks"]
    if not isinstance(checks, dict) or set(checks) != CHECKS or not all(value is True for value in checks.values()):
        raise HandoffError("receipt_checks_incomplete")
    nonce = receipt["fixture_nonce"]
    if not has_hex(nonce, 32) or receipt["observed_marker_sha256"] != sha256(marker(request, nonce).encode("ascii")):
        raise HandoffError("marker_mismatch")
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    digest = sha256(canonical(body))
    if receipt["receipt_sha256"] != digest:
        raise HandoffError("receipt_digest_mismatch")
    return digest


def verify_signature(raw_receipt: bytes, signature: Path, trusted_signers: Path) -> None:
    """Verify OpenSSH signature against an operator-supplied, independent trust file."""
    binary = shutil.which("ssh-keygen")
    if binary is None:
        raise HandoffError("ssh_keygen_missing", status="BLOCKED", code=3)
    try:
        if not signature.is_file() or not trusted_signers.is_file() or signature.stat().st_size > MAX_JSON_BYTES:
            raise HandoffError("signature_or_trust_file_invalid")
        result = subprocess.run(
            [binary, "-Y", "verify", "-f", str(trusted_signers), "-I", SIGN_PRINCIPAL,
             "-n", SIGN_NAMESPACE, "-s", str(signature)],
            input=raw_receipt, capture_output=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HandoffError("signature_verification_unavailable", status="BLOCKED", code=3) from exc
    if result.returncode != 0:
        raise HandoffError("untrusted_or_invalid_signature")
