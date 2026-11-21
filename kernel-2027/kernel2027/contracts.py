"""Core value helpers: identifiers, time, deterministic hashing.

Promoted from ``fleet2027.contracts`` (where they were already exercised by the
durability gates) so every engine can share one definition instead of the 85
private copies the audit found.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone

# ``datetime.UTC`` only exists on Python 3.11+, but this package declares
# requires-python >= 3.10 and is imported by engines that CI tests on 3.10.
# Spelling the alias this way keeps the promise true instead of nominal.
UTC = timezone.utc
from typing import Any

#: Bumped whenever a promoted behaviour changes in a way engines could notice.
KERNEL_VERSION = "1.0.0"

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,126}$")


class KernelError(RuntimeError):
    """Base class for every error the kernel raises."""


class UnsafeValueError(KernelError, ValueError):
    """Raised when a value would be unsafe as a filename, SQL key or URL."""


def utc_now() -> str:
    """Return the current UTC instant as ISO-8601 with a trailing ``Z``."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def require_safe_id(value: str, *, where: str) -> str:
    """Validate an identifier that will become a filename or a SQL key.

    Shared because every engine stores keys somewhere, and a permissive local
    copy is how path traversal bugs get written.
    """
    if not isinstance(value, str) or not _SAFE_ID.match(value):
        raise UnsafeValueError(f"{where}: unsafe identifier {value!r}")
    return value


def canonical_json(payload: Any) -> str:
    """Serialise strict JSON deterministically, so a digest is reproducible."""
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def sha256_bytes(raw: bytes) -> str:
    """Return the hex sha256 of ``raw``."""
    return hashlib.sha256(raw).hexdigest()


def sha256_text(text: str) -> str:
    """Return the hex sha256 of the UTF-8 encoding of ``text``."""
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: str) -> str:
    """Return the hex sha256 of a file, streaming so large files stay cheap."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(*parts: Any, prefix: str = "", length: int = 16) -> str:
    """Derive a short, stable, collision-resistant id from its parts.

    Used for idempotency and dedupe keys. Deterministic on purpose: the same
    inputs must always produce the same id, in every engine and every process.
    """
    material = canonical_json(list(parts))
    digest = sha256_text(material)[:length]
    return f"{prefix}{digest}" if prefix else digest


def clamp(value: float, low: float, high: float) -> float:
    """Confine ``value`` to ``[low, high]`` without raising."""
    if low > high:
        raise KernelError(f"clamp bounds are inverted: low={low} > high={high}")
    return max(low, min(high, value))


def strict_object(raw: Mapping[str, Any], *, allowed: frozenset[str], where: str) -> dict[str, Any]:
    """Reject unknown keys instead of silently ignoring them."""
    unknown = set(raw) - allowed
    if unknown:
        raise KernelError(f"{where}: unknown keys {sorted(unknown)}")
    return dict(raw)
