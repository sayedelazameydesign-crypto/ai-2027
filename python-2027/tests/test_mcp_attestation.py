"""Attestation integrity, freshness, replay, and key-configuration tests."""

from __future__ import annotations

import hashlib
import secrets

import pytest

from ai2027_protocol.mcp_attestation import (
    McpConfigurationError,
    canonical_json,
    sign_mcp_result,
    signing_key_from_env,
    verify_signed_mcp_result,
)

_KEY = secrets.token_bytes(32)


def test_hmac_result_verification_and_tamper_detection() -> None:
    envelope = sign_mcp_result(
        tool="ai2027.inspect_file",
        request_id="req-7",
        result={"ok": True, "path": "report.md", "sha256": "abc", "label": "Giza — الجيزة"},
        key=_KEY,
        request_path="report.md",
    )
    assert verify_signed_mcp_result(
        envelope,
        _KEY,
        expected_tool="ai2027.inspect_file",
        expected_request_id="req-7",
        expected_path="report.md",
    )
    assert not verify_signed_mcp_result(envelope, b"x" * 32)
    assert not verify_signed_mcp_result(envelope, b"short")
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        sign_mcp_result(tool="test.tool", request_id=1, result={}, key=b"short")
    changed = dict(envelope)
    changed["result"] = {"ok": True, "path": "other.md", "sha256": "abc"}
    assert not verify_signed_mcp_result(changed, _KEY)
    replaced = dict(envelope)
    replaced["result"] = {"ok": True, "path": "replacement.md"}
    replaced["result_sha256"] = hashlib.sha256(canonical_json(replaced["result"])).hexdigest()
    assert not verify_signed_mcp_result(replaced, _KEY)


def test_signed_result_rejects_context_mismatch_stale_data_and_replay() -> None:
    envelope = sign_mcp_result(
        tool="ai2027.inspect_file",
        request_id=17,
        request_path="docs/report.md",
        result={"ok": True, "path": "docs/report.md"},
        key=_KEY,
        issued_at=1_000,
        nonce="redteam-test-nonce-0001",
    )
    seen_nonces: set[str] = set()
    assert verify_signed_mcp_result(
        envelope,
        _KEY,
        expected_tool="ai2027.inspect_file",
        expected_request_id=17,
        expected_path="docs/report.md",
        now=1_000,
        seen_nonces=seen_nonces,
    )
    assert not verify_signed_mcp_result(
        envelope,
        _KEY,
        expected_request_id=18,
        expected_path="docs/report.md",
        now=1_001,
    )
    assert not verify_signed_mcp_result(
        envelope,
        _KEY,
        expected_request_id=17,
        expected_path="other.md",
        now=1_001,
    )
    assert not verify_signed_mcp_result(envelope, _KEY, now=1_301, max_age_s=300)
    assert not verify_signed_mcp_result(
        envelope,
        _KEY,
        expected_request_id=17,
        expected_path="docs/report.md",
        now=1_002,
        seen_nonces=seen_nonces,
    )


def test_signing_key_is_required_and_never_echoed() -> None:
    with pytest.raises(McpConfigurationError, match="64 hex characters"):
        signing_key_from_env({})
    assert signing_key_from_env({"AI2027_MCP_SIGNING_KEY": _KEY.hex()}) == _KEY
    with pytest.raises(McpConfigurationError, match="hexadecimal"):
        signing_key_from_env({"AI2027_MCP_SIGNING_KEY": "z" * 64})
