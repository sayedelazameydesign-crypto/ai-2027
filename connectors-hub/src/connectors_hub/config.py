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


def validate_lm_studio_url(value: str | None) -> str:
    value = value or "http://127.0.0.1:1234/v1"
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise ConfigurationError("LM Studio URL is malformed") from exc
    if hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ConfigurationError("LM Studio must be reachable only through loopback")
    if parsed.scheme not in {"http", "https"} or parsed.path.rstrip("/") != "/v1":
        raise ConfigurationError("LM Studio URL must be a loopback /v1 endpoint")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError(
            "LM Studio URL must not contain credentials, query, or fragment"
        )
    if port is not None and not 1 <= port <= 65535:
        raise ConfigurationError("LM Studio URL port is invalid")
    return value.rstrip("/")


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
    lm_studio_base_url: str = "http://127.0.0.1:1234/v1"
    lm_studio_model: str = "local-model"
    nvidia_model: str | None = None
    reasoning_context_tokens: int = 8192
    reasoning_max_output_tokens: int = 1024

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
        local_model = os.environ.get("CONNECTORS_HUB_LM_STUDIO_MODEL", "local-model")
        nvidia_model = os.environ.get("CONNECTORS_HUB_NVIDIA_MODEL") or None
        model_pattern = r"[A-Za-z0-9][A-Za-z0-9._/-]{0,119}"
        if not re.fullmatch(model_pattern, local_model):
            raise ConfigurationError(
                "LM Studio model must be a simple model identifier"
            )
        if nvidia_model and not re.fullmatch(model_pattern, nvidia_model):
            raise ConfigurationError("NVIDIA model must be a simple model identifier")
        try:
            reasoning_context_tokens = int(
                os.environ.get("CONNECTORS_HUB_REASONING_CONTEXT_TOKENS", "8192")
            )
            reasoning_max_output_tokens = int(
                os.environ.get("CONNECTORS_HUB_REASONING_MAX_OUTPUT_TOKENS", "1024")
            )
        except ValueError as exc:
            raise ConfigurationError(
                "Reasoning context and output token limits must be integers"
            ) from exc
        if not 1024 <= reasoning_context_tokens <= 65536:
            raise ConfigurationError(
                "CONNECTORS_HUB_REASONING_CONTEXT_TOKENS must be between 1024 and 65536"
            )
        if not 1 <= reasoning_max_output_tokens <= min(2048, reasoning_context_tokens):
            raise ConfigurationError(
                "CONNECTORS_HUB_REASONING_MAX_OUTPUT_TOKENS must be between 1 and the context limit (at most 2048)"
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
            lm_studio_base_url=validate_lm_studio_url(
                os.environ.get("CONNECTORS_HUB_LM_STUDIO_BASE_URL")
            ),
            lm_studio_model=local_model,
            nvidia_model=nvidia_model,
            reasoning_context_tokens=reasoning_context_tokens,
            reasoning_max_output_tokens=reasoning_max_output_tokens,
        )
