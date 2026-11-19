"""Input validation, path containment, SSRF guards, and secret scrubbing."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"nvapi-[0-9A-Za-z_\-]{20,}"),
    re.compile(r"sk-(?:proj-|or-v1-)?[0-9A-Za-z_\-]{20,}"),
    re.compile(r"gsk_[0-9A-Za-z_\-]{20,}"),
    re.compile(r"e2b_[0-9A-Za-z_\-]{20,}"),
    re.compile(r"rnd_[0-9A-Za-z_\-]{20,}"),
    re.compile(r"gh[pousr]_[0-9A-Za-z_\-]{20,}"),
    re.compile(r"(?:AKIA|ASIA|AROA)[0-9A-Z]{16}"),
    re.compile(r"eyJ[0-9A-Za-z_\-]{10,}\.eyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]+"),
    re.compile(r"(?i)bearer\s+[0-9A-Za-z._\-]{12,}"),
    re.compile(r"(?i)([?&](?:key|api_key|token|secret)=)[^&\s]+"),
)

_SENSITIVE_KEY_WORDS = (
    "secret",
    "token",
    "password",
    "authorization",
    "api_key",
    "apikey",
    "credential",
)
_SAFE_METADATA_KEYS = frozenset(
    {
        "max_tokens",
        "context_tokens",
        "reasoning_max_output_tokens",
        "token_budget",
        "has_api_key",
        "key_configured",
        "credential_state",
    }
)
_ENCODED_TRAVERSAL_RE = re.compile(r"%(?:2e|2f|5c|00)", re.IGNORECASE)
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")


class SecurityValidationError(ValueError):
    """Raised when untrusted input violates security boundaries."""


def redact_secrets(text: str) -> str:
    """Mask credential-shaped substrings before logging or returning diagnostics."""
    scrubbed = text
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            scrubbed = pattern.sub(r"\1[REDACTED]", scrubbed)
        else:
            scrubbed = pattern.sub("[REDACTED]", scrubbed)
    return scrubbed


def scrub_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively mask sensitive keys and secret substrings in structured logs."""
    result: dict[str, Any] = {}
    for key, value in data.items():
        low_key = key.lower()
        is_sensitive = low_key not in _SAFE_METADATA_KEYS and any(
            word in low_key for word in _SENSITIVE_KEY_WORDS
        )
        if is_sensitive:
            result[key] = "[REDACTED]"
        elif isinstance(value, Mapping):
            result[key] = scrub_mapping(value)
        elif isinstance(value, list | tuple):
            result[key] = [
                scrub_mapping(item)
                if isinstance(item, Mapping)
                else redact_secrets(item)
                if isinstance(item, str)
                else item
                for item in value
            ]
        elif isinstance(value, str):
            result[key] = redact_secrets(value)
        else:
            result[key] = value
    return result


def sanitize_prompt_text(text: str, *, max_length: int = 16_000) -> str:
    """Validate and normalize prompt text, rejecting null bytes and control chars."""
    if "\x00" in text:
        raise SecurityValidationError("Prompt must not contain null bytes.")
    for ch in text:
        code = ord(ch)
        if code < 32 and ch not in ("\n", "\r", "\t"):
            raise SecurityValidationError("Prompt contains disallowed control characters.")
    cleaned = text.strip()
    if not cleaned:
        raise SecurityValidationError("Prompt must not be blank.")
    if len(cleaned) > max_length:
        raise SecurityValidationError(f"Prompt exceeds maximum length of {max_length} characters.")
    return cleaned


def validate_provider_url(raw_url: str, *, allow_local_http: bool = True) -> str:
    """Ensure provider endpoints use HTTPS or explicitly permitted loopback HTTP."""
    candidate = raw_url.strip()
    if not candidate:
        raise SecurityValidationError("Provider URL must not be empty.")
    parsed = urlparse(candidate)
    if parsed.username or parsed.password:
        raise SecurityValidationError("Provider URL must not embed credentials.")
    if parsed.query or parsed.fragment:
        raise SecurityValidationError("Provider URL must not include query strings or fragments.")
    host = (parsed.hostname or "").lower()
    if not host:
        raise SecurityValidationError("Provider URL must specify a hostname.")

    is_loopback = host in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme == "http":
        if not (allow_local_http and is_loopback):
            raise SecurityValidationError("Plain HTTP is allowed only for local loopback hosts.")
    elif parsed.scheme != "https":
        raise SecurityValidationError("Provider URL must use https or loopback http.")

    if not is_loopback:
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None and (
            ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
        ):
            raise SecurityValidationError("Provider URL cannot target private or link-local IPs.")

    return candidate.rstrip("/")


def resolve_safe_path(base_dir: Path, untrusted_path: str) -> Path:
    """Resolve a relative path inside base_dir, rejecting cross-platform escapes."""
    if not untrusted_path or not untrusted_path.strip():
        raise SecurityValidationError("Target path must not be empty.")
    if "\x00" in untrusted_path:
        raise SecurityValidationError("Target path contains null bytes.")
    if _ENCODED_TRAVERSAL_RE.search(untrusted_path):
        raise SecurityValidationError("Target path contains encoded traversal sequences.")
    decoded = unquote(untrusted_path)
    if (
        "\\" in untrusted_path
        or "\\" in decoded
        or _DRIVE_PREFIX_RE.match(untrusted_path)
        or _DRIVE_PREFIX_RE.match(decoded)
        or untrusted_path.startswith("/")
        or decoded.startswith("/")
    ):
        raise SecurityValidationError("Target path must be relative and POSIX-formatted.")

    base_resolved = base_dir.resolve()
    candidate = (base_resolved / untrusted_path).resolve()
    if not candidate.is_relative_to(base_resolved):
        raise SecurityValidationError("Target path escapes the allowed workspace root.")
    return candidate
