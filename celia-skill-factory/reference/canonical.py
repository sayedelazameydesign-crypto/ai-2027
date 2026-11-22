"""Canonical serialization for CELIA event records.

Design rule (ADR-0006 §3): a record's bytes are defined exactly, so a
SHA-256 over them is reproducible on any machine, in any language.

Canonical form:
  - UTF-8, no BOM
  - keys sorted ascending, no insignificant whitespace
  - integers only (no floats: float formatting is not portable)
  - no NaN/Infinity
  - one JSON object per line, terminated by a single LF

$0: standard library only.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

GENESIS = "0" * 64


class CanonicalError(ValueError):
    """Raised when an object cannot be serialized canonically."""


def _check(value: Any, where: str = "$") -> None:
    if isinstance(value, float):
        raise CanonicalError(
            f"{where}: float is not canonical (formatting is not portable); "
            "use an integer or a string"
        )
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise CanonicalError(f"{where}: dict key {k!r} is not a string")
            _check(v, f"{where}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _check(v, f"{where}[{i}]")
    elif isinstance(value, (str, int, bool)) or value is None:
        return
    else:
        raise CanonicalError(f"{where}: unsupported type {type(value).__name__}")


def dumps(obj: Any) -> str:
    """Canonical JSON text of `obj` (no trailing newline)."""
    _check(obj)
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def dump_bytes(obj: Any) -> bytes:
    return dumps(obj).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(obj: Any) -> str:
    """SHA-256 of the canonical form of `obj`."""
    return sha256_hex(dump_bytes(obj))


def _no_duplicates(pairs):
    seen: dict[str, Any] = {}
    for k, v in pairs:
        if k in seen:
            raise CanonicalError(f"duplicate key rejected: {k!r}")
        seen[k] = v
    return seen


def loads_strict(text: str) -> Any:
    """Parse JSON, rejecting duplicate keys (ADR-0006 §3)."""
    try:
        return json.loads(text, object_pairs_hook=_no_duplicates)
    except CanonicalError:
        raise
    except json.JSONDecodeError as exc:
        raise CanonicalError(f"invalid JSON: {exc}") from exc
