"""AES-GCM encryption for pending provider mutation payloads."""

from __future__ import annotations

import base64
import json
import secrets
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .credentials import CredentialVault, VaultError


class PayloadCipher:
    """Encrypt JSON payloads using a 256-bit key stored in the OS keyring."""

    def __init__(self, vault: CredentialVault, key_name: str = "action_encryption_key"):
        self._vault = vault
        self._key_name = key_name

    def _key(self, *, create: bool) -> bytes:
        encoded = self._vault.get(self._key_name)
        if encoded is None:
            if not create:
                raise VaultError("The local action encryption key is not configured")
            encoded = self._vault.get_or_create(self._key_name, nbytes=32)
        try:
            key = base64.urlsafe_b64decode(encoded.encode("ascii"))
        except (ValueError, UnicodeError) as exc:
            raise VaultError("The local action encryption key is malformed") from exc
        if len(key) != 32:
            raise VaultError("The local action encryption key has an invalid length")
        return key

    def encrypt_json(self, value: Any, *, associated_data: bytes) -> str:
        nonce = secrets.token_bytes(12)
        raw = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        ciphertext = AESGCM(self._key(create=True)).encrypt(nonce, raw, associated_data)
        return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt_json(self, encrypted: str, *, associated_data: bytes) -> Any:
        try:
            packed = base64.urlsafe_b64decode(encrypted.encode("ascii"))
            nonce, ciphertext = packed[:12], packed[12:]
            raw = AESGCM(self._key(create=False)).decrypt(
                nonce, ciphertext, associated_data
            )
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            # Avoid returning cipher internals or payload fragments to callers.
            raise VaultError("Could not decrypt the pending action payload") from exc
