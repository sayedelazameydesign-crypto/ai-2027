"""Per-actor credentials derived from a control-plane-only secret key."""
from __future__ import annotations

import base64
import hashlib
import hmac


class ActorTokenManager:
    def __init__(self, master_key: str | bytes) -> None:
        key = master_key.encode("utf-8") if isinstance(master_key, str) else master_key
        if len(key) < 32:
            raise ValueError("Actor master key must contain at least 32 bytes")
        self._key = key

    def token_for(self, namespace: str, name: str, uid: str) -> str:
        if not namespace or not name or not uid:
            raise ValueError("Namespace, actor name, and UID are required")
        message = f"agent-substrate/v1\0{namespace}\0{name}\0{uid}".encode("utf-8")
        digest = hmac.new(self._key, message, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
