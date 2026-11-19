"""Small OS-keyring wrapper. Values are never logged or returned by API routes."""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import sys
from typing import Protocol

SERVICE_NAME = "ai-2027-connectors-hub"
_VALID_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class VaultError(RuntimeError):
    """Base class for keyring failures."""


class VaultUnavailable(VaultError):
    """The host has no usable encrypted OS keyring; fail closed."""


class CredentialRejected(VaultError):
    """A credential violates a safety restriction."""


class KeyringBackend(Protocol):
    priority: float

    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password(self, service: str, username: str, password: str) -> None: ...
    def delete_password(self, service: str, username: str) -> None: ...


def _looks_like_supabase_service_role(value: str) -> bool:
    """Best-effort refusal of known Supabase admin/service-role credentials.

    This is not token validation. Supabase RLS and the project must still enforce
    authorization server-side; the check is an additional fail-closed guard.
    """
    if value.strip().lower().startswith(("sb_secret_", "service_role_")):
        return True
    parts = value.split(".")
    if len(parts) < 2:
        return False
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return False
    role = claims.get("role") if isinstance(claims, dict) else None
    return isinstance(role, str) and role.lower() in {"service_role", "supabase_admin"}


class CredentialVault:
    """Access credentials through the operating system keyring only.

    On systems without a secure keyring (or where a plaintext backend is selected),
    operations fail closed. Tests should inject an in-memory fake backend.
    """

    def __init__(self, backend: KeyringBackend | None = None):
        self._backend = backend
        self._injected_backend = backend is not None

    @staticmethod
    def _is_native_os_backend(backend: KeyringBackend) -> bool:
        module = type(backend).__module__.lower()
        name = type(backend).__name__.lower()
        if os.name == "nt":
            return "winvault" in name or "winvault" in module
        if sys.platform == "darwin":
            return "macos" in module or "os_x" in module
        if sys.platform.startswith("linux"):
            return "secretservice" in module or "kwallet" in module
        return False

    def _get_backend(self) -> KeyringBackend:
        if self._backend is None:
            try:
                import keyring

                self._backend = keyring.get_keyring()
            except Exception as exc:  # pragma: no cover - host-specific
                raise VaultUnavailable(
                    "No usable operating-system keyring is available"
                ) from exc
        name = type(self._backend).__name__.lower()
        priority = getattr(self._backend, "priority", 0)
        if priority <= 0 or "plaintext" in name or "nullkeyring" in name:
            raise VaultUnavailable(
                "The selected keyring backend is not a secure OS credential store"
            )
        if not self._injected_backend and not self._is_native_os_backend(self._backend):
            raise VaultUnavailable(
                "Only a native operating-system credential store is accepted"
            )
        return self._backend

    def get(self, name: str) -> str | None:
        self._validate_name(name)
        try:
            return self._get_backend().get_password(SERVICE_NAME, name)
        except VaultError:
            raise
        except Exception as exc:
            raise VaultUnavailable(
                "Could not read the operating-system keyring"
            ) from exc

    def require(self, name: str) -> str:
        value = self.get(name)
        if not value:
            raise CredentialRejected(
                f"Credential '{name}' is not configured in the OS keyring"
            )
        return value

    def set(self, name: str, value: str) -> None:
        self._validate_name(name)
        if not value or not value.strip():
            raise CredentialRejected("Credential value must not be empty")
        if name in {
            "supabase_service_role",
            "supabase_service_role_key",
            "supabase_secret_key",
        }:
            raise CredentialRejected(
                "Supabase service-role/secret keys are not accepted by this hub"
            )
        if name.startswith("supabase_") and _looks_like_supabase_service_role(value):
            raise CredentialRejected(
                "A Supabase service-role/admin credential is not accepted; use a publishable key and user-scoped JWT with RLS"
            )
        try:
            self._get_backend().set_password(SERVICE_NAME, name, value)
        except VaultError:
            raise
        except Exception as exc:
            raise VaultUnavailable(
                "Could not write to the operating-system keyring"
            ) from exc

    def delete(self, name: str) -> None:
        self._validate_name(name)
        try:
            backend = self._get_backend()
            try:
                backend.delete_password(SERVICE_NAME, name)
            except Exception as exc:
                # keyring raises PasswordDeleteError for a missing item. Treat it
                # as an idempotent delete without exposing backend details.
                if (
                    "delete" not in type(exc).__name__.lower()
                    and "password" not in type(exc).__name__.lower()
                ):
                    raise
        except VaultError:
            raise
        except Exception as exc:
            raise VaultUnavailable(
                "Could not delete from the operating-system keyring"
            ) from exc

    def get_or_create(self, name: str, nbytes: int = 32) -> str:
        existing = self.get(name)
        if existing:
            return existing
        generated = base64.urlsafe_b64encode(secrets.token_bytes(nbytes)).decode(
            "ascii"
        )
        self.set(name, generated)
        return generated

    @staticmethod
    def _validate_name(name: str) -> None:
        if not _VALID_NAME.fullmatch(name):
            raise CredentialRejected(
                "Credential name must be a short lowercase identifier"
            )
