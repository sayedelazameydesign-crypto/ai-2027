"""Local operator CLI. Only this CLI can approve and execute pending writes."""

from __future__ import annotations

import argparse
import getpass
import json
import secrets
import sys
from collections.abc import Sequence

from .app import create_app
from .approvals import ActionError, ApprovalStore
from .config import Settings
from .credentials import CredentialRejected, CredentialVault, VaultError

_CREDENTIALS = (
    "dropbox_access_token",
    "github_token",
    "google_access_token",
    "linear_api_key",
    "notion_integration_token",
    "supabase_publishable_key",
    "supabase_user_jwt",
    "vercel_token",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="connectors-hub",
        description="Local-first connectors hub; mutating requests require local CLI approval.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="initialize the local OS-keyring secrets")
    serve = sub.add_parser("serve", help="run the API on loopback only")
    serve.add_argument(
        "--port",
        type=int,
        default=None,
        help="local port (default: 8765 or CONNECTORS_HUB_PORT)",
    )

    credentials = sub.add_parser("credentials", help="manage OS-keyring credentials")
    cred_sub = credentials.add_subparsers(dest="credentials_command", required=True)
    set_credential = cred_sub.add_parser(
        "set", help="store a provider-issued token without echoing it"
    )
    set_credential.add_argument("name", choices=_CREDENTIALS)
    cred_sub.add_parser(
        "status", help="show configured status only; never show credential values"
    )

    sub.add_parser(
        "pending", help="list pending proposals without printing their payloads"
    )
    approve = sub.add_parser(
        "approve", help="review and execute exactly one pending provider mutation"
    )
    approve.add_argument("action_id")
    cancel = sub.add_parser("cancel", help="cancel one pending action")
    cancel.add_argument("action_id")
    audit = sub.add_parser("audit", help="show local action audit metadata")
    audit.add_argument("--limit", type=int, default=100)
    return parser


def _store(settings: Settings, vault: CredentialVault) -> ApprovalStore:
    return ApprovalStore(settings.database_path, vault)


def _print_pending(store: ApprovalStore) -> None:
    pending = store.list_pending()
    if not pending:
        print("No pending actions.")
        return
    for item in pending:
        print(
            f"{item.id}  {item.connector.value:18} {item.method:6} {item.path}  expires {item.expires_at}"
        )
        print(f"  summary: {item.summary}")
        print(f"  request hash: {item.request_hash}")


def _review_and_approve(
    store: ApprovalStore, action_id: str, settings: Settings
) -> int:
    try:
        preview = store.get_preview(action_id)
    except (ActionError, VaultError) as exc:
        print(f"Cannot review action: {exc}", file=sys.stderr)
        return 2

    phrase = f"APPROVE-{preview.id[:8].upper()}"
    print("\nACTION REVIEW — this request will change a connected service")
    print(f"ID:        {preview.id}")
    print(f"Connector: {preview.connector.value}")
    print(f"Summary:   {preview.summary}")
    print(f"Method:    {preview.method}")
    print(f"API path:  {preview.path}")
    print("Query:")
    print(json.dumps(preview.query, indent=2, ensure_ascii=True, sort_keys=True))
    print("Payload:")
    print(json.dumps(preview.payload, indent=2, ensure_ascii=True, sort_keys=True))
    print(f"Request hash: {preview.request_hash}")
    print("\nCheck the recipient, resource, and every value above before proceeding.")
    confirmation = input(f"Type {phrase} to authorize this one request: ").strip()
    try:
        result = store.execute_after_local_approval(
            action_id,
            confirmation,
            settings=settings,
        )
    except (ActionError, VaultError) as exc:
        print(f"No action executed: {exc}", file=sys.stderr)
        return 2
    print(f"Result: {result.status}; provider HTTP status: {result.response_status}")
    if result.status == "unknown":
        print(
            "The provider may have received the request. Do not retry automatically; inspect the provider first."
        )
        return 3
    return 0 if result.status == "executed" else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = Settings.from_env()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        vault = CredentialVault()

        if args.command == "init":
            api_token = vault.get("api_bearer_token")
            token_created = api_token is None
            if token_created:
                api_token = secrets.token_urlsafe(32)
                vault.set("api_bearer_token", api_token)
            vault.get_or_create("action_encryption_key", nbytes=32)
            ApprovalStore(settings.database_path, vault)
            print("Local keyring and action database initialized.")
            if token_created:
                print(
                    "Copy this local API token to the agent's secret configuration now; it will not be shown again:"
                )
                print(api_token)
                print("Do not paste it into chat or commit it to Git.")
            else:
                print("A local API token already exists; it was not displayed again.")
            return 0

        if args.command == "credentials":
            if args.credentials_command == "set":
                prompt = (
                    f"Enter the provider-issued value for {args.name} (input hidden): "
                )
                value = getpass.getpass(prompt)
                vault.set(args.name, value)
                print(
                    f"Stored {args.name} in the OS keyring. The value was not displayed."
                )
                if args.name == "google_access_token":
                    print(
                        "Note: this is a short-lived access token; browser OAuth and refresh-token support are still pending."
                    )
                return 0
            from .catalog import get_connector_info

            for item in get_connector_info(vault, settings):
                print(f"{item.id.value:20} {item.state.value}")
            return 0

        store = _store(settings, vault)
        if args.command == "pending":
            _print_pending(store)
            return 0
        if args.command == "approve":
            return _review_and_approve(store, args.action_id, settings)
        if args.command == "cancel":
            result = store.cancel(args.action_id)
            print(f"Action {result.id} is {result.status}.")
            return 0
        if args.command == "audit":
            for event in store.audit_events(args.limit):
                print(json.dumps(event, sort_keys=True))
            return 0
        if args.command == "serve":
            import uvicorn

            port = args.port or settings.api_port
            if not 1 <= port <= 65535:
                print("Port must be between 1 and 65535", file=sys.stderr)
                return 2
            print(
                f"Serving locally at http://127.0.0.1:{port} (loopback only; no remote host option)."
            )
            print(
                "Write actions can only be approved by the local CLI; API routes cannot approve or execute them."
            )
            uvicorn.run(
                create_app(settings=settings, vault=vault),
                host="127.0.0.1",
                port=port,
                access_log=False,
            )
            return 0
    except (ActionError, VaultError, CredentialRejected, ValueError) as exc:
        print(f"Connectors Hub setup failed: {exc}", file=sys.stderr)
        return 2
    return 2
