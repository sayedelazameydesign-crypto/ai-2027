"""Connector catalog and non-secret connection status."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .credentials import CredentialVault, VaultError
from .models import ConnectorId, ConnectorInfo, ConnectorState


@dataclass(frozen=True)
class ConnectorSpec:
    id: ConnectorId
    name: str
    auth_group: str
    api_base: str | None
    credential_names: tuple[str, ...]
    search_kind: str | None
    notes: tuple[str, ...] = ()


SPECS: dict[ConnectorId, ConnectorSpec] = {
    ConnectorId.DROPBOX: ConnectorSpec(
        ConnectorId.DROPBOX,
        "Dropbox",
        "dropbox",
        "https://api.dropboxapi.com/2",
        ("dropbox_access_token",),
        "dropbox",
        (
            "Use OAuth from Dropbox; upload/download scopes should be added only when needed.",
        ),
    ),
    ConnectorId.GITHUB: ConnectorSpec(
        ConnectorId.GITHUB,
        "GitHub",
        "github",
        "https://api.github.com",
        ("github_token",),
        "github",
        (
            "Prefer a fine-grained token or GitHub App restricted to the required repositories.",
        ),
    ),
    ConnectorId.GMAIL: ConnectorSpec(
        ConnectorId.GMAIL,
        "Gmail",
        "google",
        "https://gmail.googleapis.com/gmail/v1/users/me",
        ("google_access_token",),
        "gmail",
        ("Uses the Google OAuth identity; the account email is only a login hint.",),
    ),
    ConnectorId.GOOGLE_CALENDAR: ConnectorSpec(
        ConnectorId.GOOGLE_CALENDAR,
        "Google Calendar",
        "google",
        "https://www.googleapis.com/calendar/v3",
        ("google_access_token",),
        "google_calendar",
    ),
    ConnectorId.GOOGLE_DRIVE: ConnectorSpec(
        ConnectorId.GOOGLE_DRIVE,
        "Google Drive",
        "google",
        "https://www.googleapis.com/drive/v3",
        ("google_access_token",),
        "google_drive",
    ),
    ConnectorId.GOOGLE_DOCS: ConnectorSpec(
        ConnectorId.GOOGLE_DOCS,
        "Google Docs",
        "google",
        "https://www.googleapis.com/drive/v3",
        ("google_access_token",),
        "google_docs",
        ("Discovery uses Drive metadata; content reads/writes use the Docs API.",),
    ),
    ConnectorId.GOOGLE_SHEETS: ConnectorSpec(
        ConnectorId.GOOGLE_SHEETS,
        "Google Sheets",
        "google",
        "https://www.googleapis.com/drive/v3",
        ("google_access_token",),
        "google_sheets",
        ("Discovery uses Drive metadata; values reads/writes use the Sheets API.",),
    ),
    ConnectorId.GOOGLE_SLIDES: ConnectorSpec(
        ConnectorId.GOOGLE_SLIDES,
        "Google Slides",
        "google",
        "https://www.googleapis.com/drive/v3",
        ("google_access_token",),
        "google_slides",
        ("Discovery uses Drive metadata; content reads/writes use the Slides API.",),
    ),
    ConnectorId.LINEAR: ConnectorSpec(
        ConnectorId.LINEAR,
        "Linear",
        "linear",
        "https://api.linear.app",
        ("linear_api_key",),
        "linear",
        ("Use a scoped API key or OAuth grant; do not use an account password.",),
    ),
    ConnectorId.NOTION: ConnectorSpec(
        ConnectorId.NOTION,
        "Notion",
        "notion",
        "https://api.notion.com/v1",
        ("notion_integration_token",),
        "notion",
        (
            "Only pages and databases explicitly shared with the integration are visible.",
        ),
    ),
    ConnectorId.SUPABASE: ConnectorSpec(
        ConnectorId.SUPABASE,
        "Supabase",
        "supabase",
        None,
        ("supabase_publishable_key", "supabase_user_jwt"),
        "supabase",
        ("Requires a user-scoped JWT and RLS; service-role/secret keys are refused.",),
    ),
    ConnectorId.VERCEL: ConnectorSpec(
        ConnectorId.VERCEL,
        "Vercel",
        "vercel",
        "https://api.vercel.com",
        ("vercel_token",),
        "vercel",
        (
            "Use a least-privilege token. This reader does not request environment variables or deployment logs.",
        ),
    ),
}


def is_configured(
    spec: ConnectorSpec, vault: CredentialVault, settings: Settings
) -> bool:
    if spec.id == ConnectorId.SUPABASE and not settings.supabase_url:
        return False
    try:
        return all(bool(vault.get(name)) for name in spec.credential_names)
    except VaultError:
        return False


def get_connector_info(
    vault: CredentialVault, settings: Settings
) -> list[ConnectorInfo]:
    items: list[ConnectorInfo] = []
    for connector_id in ConnectorId:
        spec = SPECS[connector_id]
        configured = is_configured(spec, vault, settings)
        state = ConnectorState.CONFIGURED if configured else ConnectorState.UNCONFIGURED
        if (
            connector_id == ConnectorId.SUPABASE
            and configured
            and (
                not settings.supabase_search_tables
                or not settings.supabase_search_columns
            )
        ):
            state = ConnectorState.NEEDS_RESOURCE_CONFIG
        notes = list(spec.notes)
        if (
            connector_id == ConnectorId.SUPABASE
            and state == ConnectorState.NEEDS_RESOURCE_CONFIG
        ):
            notes.append(
                "Set explicit table and column allowlists before searching or reading rows."
            )
        if (
            connector_id
            in {
                ConnectorId.GMAIL,
                ConnectorId.GOOGLE_CALENDAR,
                ConnectorId.GOOGLE_DRIVE,
                ConnectorId.GOOGLE_DOCS,
                ConnectorId.GOOGLE_SHEETS,
                ConnectorId.GOOGLE_SLIDES,
            }
            and not configured
        ):
            notes.append(
                "Google browser OAuth and refresh-token onboarding are not implemented yet."
            )
        items.append(
            ConnectorInfo(
                id=connector_id,
                name=spec.name,
                state=state,
                auth_group=spec.auth_group,
                search_supported=spec.search_kind is not None,
                read_supported=True,
                notes=notes,
            )
        )
    return items
