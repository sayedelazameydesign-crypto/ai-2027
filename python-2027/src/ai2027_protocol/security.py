"""Input validation, path containment, SSRF guards, and secret scrubbing."""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from kernel2027.contracts import UnsafeValueError
from kernel2027.endpoint import validate_endpoint
from kernel2027.redaction import redact as kernel_redact

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
    """Mask credential-shaped substrings before logging or returning diagnostics.

    Delegates to :mod:`kernel2027` so the pattern list cannot drift between the
    engines that log provider errors — this was one of 24 copies of the same idea.
    """
    return kernel_redact(text)


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

    # The rule itself lives in kernel2027 so every engine enforces the same one;
    # this wrapper only translates the error type callers already expect.
    try:
        return validate_endpoint(candidate, allow_loopback_http=allow_local_http)
    except UnsafeValueError as exc:
        raise SecurityValidationError(str(exc)) from exc


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

    base_str = os.path.realpath(base_dir)
    if untrusted_path.strip() == ".":
        return Path(base_str)
    fullpath = os.path.normpath(os.path.join(base_str, untrusted_path))
    if not fullpath.startswith(base_str + os.sep):
        raise SecurityValidationError("Target path escapes the allowed workspace root.")
    resolved = os.path.realpath(fullpath)
    if not resolved.startswith(base_str + os.sep):
        raise SecurityValidationError("Target path escapes the allowed workspace root.")
    return Path(resolved)
