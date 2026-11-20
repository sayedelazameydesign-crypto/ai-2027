"""Shared-key integrity attestations for AI-2027 MCP tool results.

MCP's standard tool result has no built-in signature field. This module adds an
HMAC-SHA256 envelope for clients that receive the same secret through their own
secure configuration. HMAC provides authenticity/integrity between key holders;
it is not a public-key signature or non-repudiation mechanism.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from collections.abc import Mapping
from typing import Any

MCP_SIGNING_KEY_ENV = "AI2027_MCP_SIGNING_KEY"
MCP_RESULT_MAX_AGE_SECONDS = 300
MCP_RESULT_CLOCK_SKEW_SECONDS = 30
JsonRpcId = str | int


class McpConfigurationError(RuntimeError):
    """Raised when the MCP server cannot start safely from its configuration."""


def signing_key_from_env(environ: Mapping[str, str] | None = None) -> bytes:
    """Load a 32-byte HMAC key encoded as 64 hex characters; never echo it."""
    values = os.environ if environ is None else environ
    encoded = values.get(MCP_SIGNING_KEY_ENV, "")
    if len(encoded) != 64:
        raise McpConfigurationError(
            f"Set {MCP_SIGNING_KEY_ENV} to a randomly generated 32-byte key "
            "encoded as 64 hex characters."
        )
    try:
        key = bytes.fromhex(encoded)
    except ValueError as exc:
        raise McpConfigurationError(
            f"{MCP_SIGNING_KEY_ENV} must contain only hexadecimal characters."
        ) from exc
    if len(key) != 32:
        raise McpConfigurationError(f"{MCP_SIGNING_KEY_ENV} must decode to exactly 32 bytes.")
    return key


def canonical_json(value: object) -> bytes:
    """Serialize compact UTF-8 JSON with sorted keys for signatures and wire output."""
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return serialized.encode("utf-8", errors="backslashreplace")


def _path_digest(path: str | None) -> str | None:
    """Return a stable digest for the original request path without exposing it."""
    if path is None:
        return None
    return hashlib.sha256(path.encode("utf-8", errors="backslashreplace")).hexdigest()


def sign_mcp_result(
    *,
    tool: str,
    request_id: JsonRpcId,
    result: dict[str, Any],
    key: bytes,
    request_path: str | None = None,
    issued_at: int | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    """Bind a result to its tool, request ID/path, timestamp and one-time nonce."""
    if len(key) != 32:
        raise ValueError("MCP HMAC key must be exactly 32 bytes.")
    timestamp = int(time.time()) if issued_at is None else issued_at
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
        raise ValueError("MCP result issued_at must be a non-negative Unix timestamp.")
    request_nonce = secrets.token_urlsafe(18) if nonce is None else nonce
    if not isinstance(request_nonce, str) or not 16 <= len(request_nonce) <= 128:
        raise ValueError("MCP result nonce must contain 16 to 128 characters.")
    result_hash = hashlib.sha256(canonical_json(result)).hexdigest()
    body = {
        "tool": tool,
        "request_id": request_id,
        "request_path_sha256": _path_digest(request_path),
        "issued_at": timestamp,
        "nonce": request_nonce,
        "result": result,
        "result_sha256": result_hash,
    }
    key_id = hashlib.sha256(key).hexdigest()[:16]
    signature = hmac.new(key, canonical_json(body), hashlib.sha256).hexdigest()
    return {
        **body,
        "signature": {"algorithm": "HMAC-SHA256", "key_id": key_id, "value": signature},
    }


def verify_signed_mcp_result(
    envelope: Mapping[str, Any],
    key: bytes,
    *,
    expected_tool: str | None = None,
    expected_request_id: JsonRpcId | None = None,
    expected_path: str | None = None,
    max_age_s: int | None = MCP_RESULT_MAX_AGE_SECONDS,
    now: int | None = None,
    seen_nonces: set[str] | None = None,
) -> bool:
    """Verify an attestation, freshness and optional request context.

    Callers that need replay resistance should pass a per-connection set via
    ``seen_nonces`` and the request ID/path they expected. The signed timestamp
    bounds stale replay; nonce tracking rejects a repeated envelope in that
    verifier's lifetime. HMAC alone cannot provide non-repudiation.
    """
    if len(key) != 32:
        return False
    try:
        tool = envelope["tool"]
        request_id = envelope["request_id"]
        request_path_sha256 = envelope["request_path_sha256"]
        issued_at = envelope["issued_at"]
        nonce = envelope["nonce"]
        result = envelope["result"]
        result_hash = envelope["result_sha256"]
        signature = envelope["signature"]
        if (
            not isinstance(tool, str)
            or not isinstance(result, dict)
            or not isinstance(signature, dict)
            or not isinstance(request_id, str | int)
            or isinstance(request_id, bool)
            or not isinstance(issued_at, int)
            or isinstance(issued_at, bool)
            or issued_at < 0
            or not isinstance(nonce, str)
            or not 16 <= len(nonce) <= 128
            or (request_path_sha256 is not None and not isinstance(request_path_sha256, str))
            or not isinstance(result_hash, str)
        ):
            return False
        if expected_tool is not None and tool != expected_tool:
            return False
        if expected_request_id is not None and request_id != expected_request_id:
            return False
        if expected_path is not None and request_path_sha256 != _path_digest(expected_path):
            return False
        if max_age_s is not None:
            if isinstance(max_age_s, bool) or max_age_s < 0:
                return False
            current_time = int(time.time()) if now is None else now
            if isinstance(current_time, bool) or not isinstance(current_time, int):
                return False
            age = current_time - issued_at
            if age > max_age_s or age < -MCP_RESULT_CLOCK_SKEW_SECONDS:
                return False
        expected_key_id = hashlib.sha256(key).hexdigest()[:16]
        if (
            signature.get("algorithm") != "HMAC-SHA256"
            or signature.get("key_id") != expected_key_id
        ):
            return False
        expected_hash = hashlib.sha256(canonical_json(result)).hexdigest()
        if not hmac.compare_digest(result_hash, expected_hash):
            return False
        body = {
            "tool": tool,
            "request_id": request_id,
            "request_path_sha256": request_path_sha256,
            "issued_at": issued_at,
            "nonce": nonce,
            "result": result,
            "result_sha256": result_hash,
        }
        expected_signature = hmac.new(key, canonical_json(body), hashlib.sha256).hexdigest()
        actual_signature = signature.get("value")
        if not isinstance(actual_signature, str) or not hmac.compare_digest(
            actual_signature, expected_signature
        ):
            return False
        if seen_nonces is not None:
            if nonce in seen_nonces:
                return False
            seen_nonces.add(nonce)
        return True
    except (KeyError, TypeError, ValueError):
        return False
