#!/usr/bin/env python3
"""Approval bot over Telegram — CI entry point (Actions secrets only).

Architecture (docs/TELEGRAM-APPROVAL-RUNBOOK.md §1): secrets live in
GitHub Actions, never on a developer machine. This script is the single
runner the workflow calls.

Modes:
  test  send one plain proof message        (bot token + chat id)
  run   one full cycle: cards + votes       (+ sheet id + service account)

Fail-closed rules:
  * missing required secrets -> print the missing NAMES (never values),
    exit 2;
  * empty PA_APPROVAL_ALLOWED_SENDERS -> every vote will be denied
    (by design, AD-004) — loud notice, not a failure;
  * no secret value is ever printed, logged, or written to disk.

Exit codes: 0 success · 1 runtime failure · 2 configuration/preflight.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Any, Dict, List, Optional, Tuple

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Aliases: the workflow may expose either the .env.example name or the
# shorter TELEGRAM_* spelling; the runtime only ever reads the former.
ALIASES = (
    ("PA_APPROVAL_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"),
    ("PA_APPROVAL_CHAT_ID", "TELEGRAM_CHAT_ID"),
    ("PA_APPROVAL_ALLOWED_SENDERS", "TELEGRAM_ALLOWED_SENDERS"),
)

SPREADSHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


def resolve(*names: str) -> Tuple[str, str]:
    """First non-empty value among names -> (value, source_name)."""
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value, name
    return "", ""


def apply_aliases() -> None:
    """Copy TELEGRAM_* secrets onto the canonical PA_APPROVAL_* keys."""
    for canonical, alias in ALIASES:
        value, _ = resolve(canonical, alias)
        if value:
            os.environ[canonical] = value


def preflight(mode: str) -> List[str]:
    """Missing required configuration -> list of NAMES (never values)."""
    missing: List[str] = []
    token, _ = resolve("PA_APPROVAL_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")
    if not token:
        missing.append("PA_APPROVAL_BOT_TOKEN (or TELEGRAM_BOT_TOKEN)")
    chat, _ = resolve("PA_APPROVAL_CHAT_ID", "TELEGRAM_CHAT_ID")
    if not chat:
        missing.append("PA_APPROVAL_CHAT_ID (or TELEGRAM_CHAT_ID)")
    if mode == "run":
        if not os.getenv("PA_MEMORY_SHEET_ID", "").strip():
            missing.append("PA_MEMORY_SHEET_ID")
        raw, _ = resolve("GOOGLE_SERVICE_ACCOUNT_JSON",
                         "GOOGLE_SERVICE_ACCOUNT")
        if not raw:
            missing.append("GOOGLE_SERVICE_ACCOUNT_JSON")
        else:
            try:
                parsed = json.loads(raw)
            except (ValueError, TypeError):
                parsed = None
            if not isinstance(parsed, dict) or "client_email" not in parsed:
                missing.append(
                    "GOOGLE_SERVICE_ACCOUNT_JSON (value is not a service"
                    " account JSON object)")
    return missing


def load_state(path: pathlib.Path) -> Dict[str, Any]:
    """Persistent cross-process state: sent card ids + getUpdates offset."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"sent": [], "offset": None}
    if not isinstance(data, dict):
        return {"sent": [], "offset": None}
    sent = [str(i) for i in data.get("sent", []) if str(i).strip()]
    offset = data.get("offset")
    return {"sent": sent, "offset": int(offset) if offset is not None else None}


def save_state(path: pathlib.Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"sent": list(state.get("sent", [])),
                    "offset": state.get("offset")},
                   indent=1, sort_keys=True),
        encoding="utf-8")


def build_sheet_credentials() -> Any:
    """Service-account creds straight from the secret (no file on disk)."""
    from google.oauth2 import service_account

    raw, _ = resolve("GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_SERVICE_ACCOUNT")
    info = json.loads(raw)  # preflight validated shape already
    return service_account.Credentials.from_service_account_info(
        info, scopes=[SPREADSHEETS_SCOPE])


def _import_bot_module() -> Any:
    sys.path.insert(0, str(ROOT / "src" / "watchers"))
    import approval_bot  # noqa: PLC0415 — after env is fully prepared
    return approval_bot


def mode_test() -> int:
    ab = _import_bot_module()
    transport = ab.TelegramTransport()
    message_id = transport.send_text(
        "✅ approval bot test — Telegram path is live "
        "(free tier, Actions secrets).")
    print("stage=test ok=true message_id=%s" % message_id)
    return 0


def mode_run(state_file: pathlib.Path) -> int:
    ab = _import_bot_module()
    creds = build_sheet_credentials()
    bot = ab.ApprovalBot(creds)

    prior = load_state(state_file)
    bot.restore_state(sent_ids=prior["sent"], offset=prior["offset"])
    prior_offset = prior["offset"]

    try:
        report = bot.run_once()
    except Exception as exc:  # noqa: BLE001 — CI must classify, not crash
        # Persist sent-card progress (cards may already be out) but keep the
        # OLD offset so an unapplied callback is retried next cycle — the
        # same semantics as the long-running daemon.
        save_state(state_file,
                   {"sent": sorted(bot.state_snapshot()["sent"]),
                    "offset": prior_offset})
        print("stage=cycle ok=false reason=%s" % type(exc).__name__)
        print("error=%s" % str(exc)[:300])
        return 1

    save_state(state_file, bot.state_snapshot())
    print("stage=cycle ok=true cards_sent=%d callbacks_handled=%d"
          " granted=%d denied=%d"
          % (report.get("cards_sent", 0), report.get("callbacks_handled", 0),
             report.get("granted", 0), report.get("denied", 0)))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Telegram approval bot — CI runner (fail-closed)")
    parser.add_argument("--mode", choices=("test", "run"), default="test",
                        help="test: proof message · run: cards + votes cycle")
    args = parser.parse_args(argv)

    apply_aliases()
    missing = preflight(args.mode)
    if missing:
        print("preflight failed — missing configuration (names only):")
        for name in missing:
            print("  - %s" % name)
        print("see docs/TELEGRAM-APPROVAL-RUNBOOK.md (Actions secrets)")
        return 2

    if args.mode == "run" and not resolve(
            "PA_APPROVAL_ALLOWED_SENDERS", "TELEGRAM_ALLOWED_SENDERS")[0]:
        print("notice: PA_APPROVAL_ALLOWED_SENDERS is empty — every vote"
              " will be denied (fail-closed by design, AD-004)")

    state_file = pathlib.Path(
        os.getenv("BOT_STATE_FILE", ".bot-state.json")).expanduser()
    try:
        if args.mode == "test":
            return mode_test()
        return mode_run(state_file)
    except Exception as exc:  # noqa: BLE001 — classified below, never leaked
        print("stage=runner ok=false reason=%s" % type(exc).__name__)
        print("error=%s" % str(exc)[:300])
        return 1


if __name__ == "__main__":
    sys.exit(main())
