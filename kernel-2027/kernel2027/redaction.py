"""Secret redaction — one implementation for every log line in the repo.

Promoted from ``ai2027_protocol.security.redact_secrets`` (the most complete
copy: 11 patterns) rather than inventing a new one, so adoption is a rename
with identical behaviour on every existing input.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

#: Credential shapes seen in this repo's providers, all with a capture group
#: convention: group(1) is preserved when present, the rest is masked.
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
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

#: Field names whose *values* must never be echoed, whatever they look like.
SENSITIVE_KEYWORDS: frozenset[str] = frozenset(
    {
        "secret",
        "token",
        "password",
        "passwd",
        "authorization",
        "api_key",
        "apikey",
        "access_key",
        "private_key",
        "client_secret",
        "credential",
        "cookie",
        "session_id",
    }
)

REDACTED = "[REDACTED]"


def redact(
    text: str,
    secrets: Iterable[str] | None = None,
    *,
    marker: str = REDACTED,
) -> str:
    """Mask credential-shaped substrings in ``text``.

    ``secrets`` adds *literal* values to scrub — the case a regex cannot catch,
    because the engine knows the key it was handed but not whether the shape is
    recognisable. Adopted from the engines that already did this, so one call
    now covers both pattern-based and known-value redaction.
    """
    if not isinstance(text, str):
        return text
    scrubbed = text
    for secret in secrets or ():
        if isinstance(secret, str) and len(secret) >= 8:
            scrubbed = scrubbed.replace(secret, marker)
    for pattern in SECRET_PATTERNS:
        if pattern.groups:
            scrubbed = pattern.sub(lambda m: m.group(1) + marker, scrubbed)
        else:
            scrubbed = pattern.sub(marker, scrubbed)
    return scrubbed


def redact_mapping(
    payload: Mapping[str, Any],
    *,
    secrets: Iterable[str] | None = None,
    marker: str = REDACTED,
    _depth: int = 0,
) -> dict[str, Any]:
    """Recursively redact a mapping: sensitive keys lose their value entirely.

    Key-based masking is the part per-engine copies kept getting wrong — a
    well-formed token that no regex recognises still must not be logged when its
    field is called ``api_key``.
    """
    if _depth > 8:  # pragma: no cover - defensive against cycles
        return {"_truncated": True}
    out: dict[str, Any] = {}
    for key, value in payload.items():
        lowered = str(key).lower()
        if any(word in lowered for word in SENSITIVE_KEYWORDS):
            out[str(key)] = marker
        elif isinstance(value, Mapping):
            out[str(key)] = redact_mapping(value, secrets=secrets, marker=marker, _depth=_depth + 1)
        elif isinstance(value, list):
            out[str(key)] = [
                redact_mapping(item, secrets=secrets, marker=marker, _depth=_depth + 1)
                if isinstance(item, Mapping)
                else redact(item, secrets, marker=marker) if isinstance(item, str) else item
                for item in value
            ]
        elif isinstance(value, str):
            out[str(key)] = redact(value, secrets, marker=marker)
        else:
            out[str(key)] = value
    return out
