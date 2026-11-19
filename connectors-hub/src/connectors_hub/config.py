"""Configuration with local-only defaults and explicit resource allowlists."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    """Raised when configuration would weaken a safety boundary."""


def default_data_dir() -> Path:
    override = os.environ.get("CONNECTORS_HUB_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "ConnectorsHub"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser() / "connectors-hub"
    return Path.home() / ".local" / "share" / "connectors-hub"


def _csv_set(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(part.strip() for part in value.split(",") if part.strip())


def validate_supabase_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise ConfigurationError("Supabase URL is malformed") from exc
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError(
            "Supabase URL must not contain user info, query, or fragment"
        )
    if parsed.path not in {"", "/"}:
        raise ConfigurationError("Supabase URL must point to the project root")
    is_local = hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (is_local and parsed.scheme == "http"):
        raise ConfigurationError(
            "Supabase URL must use HTTPS (HTTP is allowed only on loopback for local development)"
        )
    if not is_local and not hostname.endswith((".supabase.co", ".supabase.in")):
        raise ConfigurationError("Supabase URL host must be a Supabase project host")
    if port is not None and not (
        is_local or (parsed.scheme == "https" and port == 443)
    ):
        raise ConfigurationError("Supabase URL must use the standard HTTPS port")
    return value.rstrip("/")


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    api_host: str = "127.0.0.1"
    api_port: int = 8765
    google_login_hint: str | None = None
    supabase_url: str | None = None
    supabase_search_tables: frozenset[str] = frozenset()
    supabase_search_columns: tuple[str, ...] = ()

    @property
    def database_path(self) -> Path:
        return self.data_dir / "hub.sqlite3"

    @classmethod
    def from_env(cls) -> Settings:
        raw_port = os.environ.get("CONNECTORS_HUB_PORT", "8765")
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise ConfigurationError("CONNECTORS_HUB_PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise ConfigurationError("CONNECTORS_HUB_PORT must be between 1 and 65535")
        columns = tuple(
            c.strip()
            for c in os.environ.get("CONNECTORS_HUB_SUPABASE_SEARCH_COLUMNS", "").split(
                ","
            )
            if c.strip()
        )
        if any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column) for column in columns
        ):
            raise ConfigurationError(
                "Supabase search columns must be simple SQL identifiers"
            )
        return cls(
            data_dir=default_data_dir(),
            api_port=port,
            google_login_hint=os.environ.get("CONNECTORS_HUB_GOOGLE_LOGIN_HINT")
            or None,
            supabase_url=validate_supabase_url(
                os.environ.get("CONNECTORS_HUB_SUPABASE_URL")
            ),
            supabase_search_tables=_csv_set(
                os.environ.get("CONNECTORS_HUB_SUPABASE_SEARCH_TABLES")
            ),
            supabase_search_columns=columns,
        )
