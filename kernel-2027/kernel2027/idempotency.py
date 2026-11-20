"""Idempotency keys — the same inputs must always produce the same key.

Eighteen files across two engines invent these; a key that differs between the
engine that *records* an effect and the engine that *checks* it is worse than no
key at all, because it looks like protection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import KernelError, stable_id


@dataclass(frozen=True)
class IdempotencyKey:
    """A namespaced, deterministic key for one logical operation."""

    value: str
    namespace: str = ""

    def __post_init__(self) -> None:
        """Refuse an empty key: an empty key dedupes everything into one row."""
        if not self.value.strip():
            raise KernelError("IdempotencyKey.value must not be blank")

    def __str__(self) -> str:
        """Render as the string persisted in storage."""
        return self.value


def effect_key(
    *,
    namespace: str,
    operation: str,
    scope: dict[str, Any] | None = None,
    length: int = 32,
) -> IdempotencyKey:
    """Build a stable key for an external effect.

    ``namespace`` is the engine or product area, ``operation`` the action, and
    ``scope`` the inputs that make this instance distinct (a user id, a step
    id). Reordering keys in ``scope`` must not change the result — the canonical
    serialisation guarantees that.
    """
    if not namespace.strip():
        raise KernelError("namespace must not be blank")
    if not operation.strip():
        raise KernelError("operation must not be blank")
    digest = stable_id(namespace, operation, scope or {}, length=length)
    return IdempotencyKey(value=f"{namespace}:{digest}", namespace=namespace)
