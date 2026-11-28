#!/usr/bin/env python3
"""Approval bot over Telegram — CI entry point (Actions secrets only).

Architecture (docs/TELEGRAM-APPROVAL-RUNBOOK.md §1): secrets live in
GitHub Actions, never on a developer machine. This script is the single
runner the workflow calls.

Modes:
  test       send one plain proof message        (bot token + chat id)
  run        one full cycle: cards + votes       (+ sheet id + service account)
             + channel-assistant replies when AI2027_ASSISTANT_ENABLED=1
  assistant  live probe: one free-model reply to PA_APPROVAL_CHAT_ID
             (needs python-2027 installed; nothing else is touched)

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
import re
import sys
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "kernel-2027") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "kernel-2027"))
from kernel2027 import redact as kernel_redact  # noqa: E402 — stdlib-only kernel, no install

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
    if not chat and mode != "chatcheck":  # chatcheck diagnoses a missing id itself
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
    """Persistent cross-process state: sent card ids + getUpdates offset.

    An ``assistant`` key (replied message ids + bounded transcript) is
    returned only when the file carries one, so the safe defaults stay
    exactly ``{"sent": [], "offset": None}``.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"sent": [], "offset": None}
    if not isinstance(data, dict):
        return {"sent": [], "offset": None}
    sent = [str(i) for i in data.get("sent", []) if str(i).strip()]
    offset = data.get("offset")
    state: Dict[str, Any] = {
        "sent": sent, "offset": int(offset) if offset is not None else None}
    if isinstance(data.get("assistant"), dict):
        state["assistant"] = data["assistant"]
    return state


def save_state(path: pathlib.Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {"sent": list(state.get("sent", [])),
                               "offset": state.get("offset")}
    if isinstance(state.get("assistant"), dict):
        payload["assistant"] = state["assistant"]
    path.write_text(json.dumps(payload, indent=1, sort_keys=True,
                               ensure_ascii=False),
                    encoding="utf-8")


# ---------------------------------------------------------------------------
# Channel assistant on the approval bot's poll (docs/CHANNEL-ASSISTANT.md §11)
# ---------------------------------------------------------------------------
ASSISTANT_FLAG = "AI2027_ASSISTANT_ENABLED"
ASSISTANT_SEEN_LIMIT = 500


def assistant_enabled() -> bool:
    """The assistant replies only when the flag is explicitly on."""
    return os.getenv(ASSISTANT_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def allowed_sender_ids() -> List[str]:
    raw, _ = resolve("PA_APPROVAL_ALLOWED_SENDERS", "TELEGRAM_ALLOWED_SENDERS")
    return [item.strip() for item in raw.split(",") if item.strip()]


class TelegramAssistantBridge:
    """Fail-closed filter between the approval poll and the assistant.

    It is the ``ApprovalBot.text_handler``. Only plain text from an allowed
    sender (``PA_APPROVAL_ALLOWED_SENDERS``, empty = nobody) inside the
    configured chat reaches ``reply_fn``; commands, other chats, strangers,
    edits and media are ignored. Each ``chat:message_id`` is marked *before*
    the reply is attempted and persisted in the bot state, so a retried
    cycle (offset rolled back after a failure) never answers twice.
    """

    def __init__(self, reply_fn: Callable[[Dict[str, Any]], str], *,
                 allowed_senders: List[str], chat_id: str,
                 seen: Optional[List[str]] = None) -> None:
        self._reply_fn = reply_fn
        self._allowed = {str(s) for s in allowed_senders if str(s).strip()}
        self._chat_id = str(chat_id).strip()
        self.seen: List[str] = [str(s) for s in (seen or [])][-ASSISTANT_SEEN_LIMIT:]
        self.stats: Dict[str, int] = {}

    def _count(self, stage: str) -> str:
        self.stats[stage] = self.stats.get(stage, 0) + 1
        return stage

    def __call__(self, update: Dict[str, Any]) -> str:
        message = update.get("message") if isinstance(update, dict) else None
        if not isinstance(message, dict):
            return self._count("ignored_not_message")
        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            return self._count("ignored_not_text")
        if text.lstrip().startswith("/"):
            return self._count("ignored_command")
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        sender = message.get("from") if isinstance(message.get("from"), dict) else {}
        if not self._chat_id or str(chat.get("id", "")) != self._chat_id:
            return self._count("ignored_other_chat")
        if str(sender.get("id", "")) not in self._allowed:
            return self._count("ignored_sender_not_allowed")
        key = "%s:%s" % (chat.get("id"), message.get("message_id"))
        if key in self.seen:
            return self._count("duplicate")
        self.seen.append(key)
        del self.seen[:-ASSISTANT_SEEN_LIMIT]
        try:
            stage = str(self._reply_fn(update) or "unknown")
        except Exception as exc:  # noqa: BLE001 — classified, never leaked
            stage = "error_%s" % type(exc).__name__
        return self._count(stage)


# Log labels come only from these fixed tuples (CodeQL py/clear-text-logging):
# the reply object is derived from the owner's message and chat id, so its
# fields are never printed directly — only the matching constant, or "other".
ASSISTANT_STAGES = ("replied", "no_provider_available", "duplicate_message",
                    "invalid_message", "not_configured", "paid_not_allowed",
                    "send_failed",
                    # CELIA memory commands (run in code, never by a model)
                    "memory_saved", "memory_forgotten", "memory_listed",
                    "memory_duplicate", "memory_refused_sensitive",
                    "memory_not_found", "memory_ambiguous", "memory_empty",
                    "memory_too_long", "memory_full", "memory_store_failed",
                    "memory_unavailable")
ASSISTANT_MODES = ("companion", "intelligence", "developer", "executive",
                   "creative")
MEMORY_BACKENDS = ("sheet", "state", "none")
#: Why the probe has no sheet — allowlisted, safe for run-page annotations.
SHEET_REASONS = ("ok", "no_credentials", "no_sheet_id", "gspread_missing",
                 "bad_credentials", "open_failed")
#: Probe stages that prove the path works (a model replied, or a memory
#: command ran and was confirmed from code).
PROBE_OK_STAGES = frozenset((
    "replied", "memory_saved", "memory_forgotten", "memory_listed",
    "memory_duplicate", "memory_refused_sensitive", "memory_not_found",
    "memory_ambiguous", "memory_empty", "memory_too_long", "memory_full"))
ASSISTANT_PROVIDERS = ("ollama", "gemini", "nvidia", "groq", "openrouter",
                       "omnirouters", "qwen", "deterministic")
ASSISTANT_TIERS = ("local", "free_tier", "unknown_cost", "paid")


def _label(value: Any, allowed: Tuple[str, ...]) -> str:
    """Return the constant in ``allowed`` equal to ``value`` (enum-aware), else 'other'."""
    text = str(getattr(value, "value", value) or "")
    for item in allowed:
        if item == text:
            return item
    return "other" if text else "-"


def _reply_summary(reply: Any) -> str:
    """One log line built from allowlisted constants and $0 / chunk buckets."""
    cost = "0.00" if not float(getattr(reply, "cost_usd", 0.0) or 0.0) else "NONZERO"
    chunks = int(getattr(reply, "chunks_sent", 0) or 0)
    used = int(getattr(reply, "memories_used", 0) or 0)
    return "mode=%s provider=%s tier=%s cost_usd=%s chunks=%s memories=%s" % (
        _label(getattr(reply, "mode", None), ASSISTANT_MODES),
        _label(getattr(reply, "provider", None), ASSISTANT_PROVIDERS),
        _label(getattr(reply, "billing_tier", None), ASSISTANT_TIERS),
        cost, "0" if chunks <= 0 else ("1" if chunks == 1 else "2+"),
        "0" if used <= 0 else ("1-3" if used <= 3 else "4+"))


CELIA_MEMORY_TAB = "celia_memory"


class SheetMemoryBackend:
    """CELIA's durable memory as the ``celia_memory`` tab of the memory sheet.

    Same spreadsheet as ``pa_approvals`` (``PA_MEMORY_SHEET_ID``), so there
    is no second memory database and the owner can read, correct or delete
    any row by hand. The tab is created on the first write. Rows hold only
    what the owner explicitly asked CELIA to remember.
    """

    def __init__(self, sheet: Any, headers: Tuple[str, ...]) -> None:
        self._sheet = sheet
        self._headers = tuple(headers)

    def _worksheet(self, create: bool) -> Any:
        try:
            return self._sheet.worksheet(CELIA_MEMORY_TAB)
        except Exception as exc:  # noqa: BLE001 — classified below
            # Only a missing tab is normal. Quota / auth / network errors must
            # propagate so the caller falls back instead of reading "empty".
            if type(exc).__name__ != "WorksheetNotFound":
                raise
            if not create:
                return None
            return self._sheet.add_worksheet(title=CELIA_MEMORY_TAB, rows=400,
                                             cols=len(self._headers))

    def load(self) -> List[Dict[str, Any]]:
        ws = self._worksheet(create=False)
        if ws is None:
            return []
        values = ws.get_all_values()
        if not values:
            return []
        header = [h.strip() for h in values[0]]
        return [dict(zip(header, row)) for row in values[1:] if any(row)]

    def save(self, rows: List[Dict[str, Any]]) -> None:
        ws = self._worksheet(create=True)
        matrix = [list(self._headers)] + [
            [str(r.get(h, "")) for h in self._headers] for r in rows]
        ws.clear()
        ws.update(range_name="A1", values=matrix)


def _import_assistant_module() -> Any:
    """Import the assistant from the checkout (``pip install -e ./python-2027``)."""
    src = str(ROOT / "python-2027" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from ai2027_protocol import channel_assistant_runtime  # noqa: PLC0415
    return channel_assistant_runtime


def open_celia_memory(ca: Any, sheet: Any = None,
                      fallback_rows: Any = None) -> Tuple[Any, str, Any]:
    """(PersonalMemory | None, backend label, state-backend | None).

    The sheet tab first; if it cannot be read, the bot state file (still
    durable across cycles via actions/cache); ``none`` only when neither
    exists. A storage problem never stops the cycle or the votes.
    """
    if sheet is not None:
        try:
            return (ca.PersonalMemory(SheetMemoryBackend(sheet, ca.SHEET_HEADERS)),
                    "sheet", None)
        except Exception as exc:  # noqa: BLE001 — fall back, never crash
            print("stage=memory backend=state reason=%s" % type(exc).__name__)
    if fallback_rows is not None:
        backend = ca.ListMemoryBackend(fallback_rows)
        return ca.PersonalMemory(backend), "state", backend
    return None, "none", None


def build_assistant_reply_fn(history: Any = None, *, sheet: Any = None,
                             memory_rows: Any = None,
                             ) -> Tuple[Callable[[Dict[str, Any]], str],
                                        Callable[[], Any]]:
    """(reply_fn, snapshot_fn): CELIA (``CeliaBrain``) for this cycle.

    One Free-First router (local / declared free-tier providers only,
    ``MAX_SPEND=0``); the Telegram sender only calls ``sendMessage`` (never
    ``getUpdates``). Memory: the ``celia_memory`` sheet tab, or the state
    file — ``reply_fn.memory_rows()`` returns the rows to persist in the
    state file (``None`` when the sheet holds them).
    """
    import asyncio  # noqa: PLC0415

    ca = _import_assistant_module()
    store = ca.SnapshotConversationStore(max_turns=ca.env_max_history_turns())
    store.restore(history or [])
    memory, backend_label, state_backend = open_celia_memory(
        ca, sheet, memory_rows if memory_rows is not None else [])
    brain = ca.CeliaBrain(
        router=ca.build_assistant_router(),
        sender_factory=lambda chat: ca.TelegramReplySender(chat_id=chat),
        store=store, memory=memory, fallback_text=ca.CELIA_OWNER_FALLBACK)

    def reply_fn(update: Dict[str, Any]) -> str:
        inbound = ca.parse_telegram_update(update)
        if inbound is None:
            return "ignored_unparsed"
        turn = asyncio.run(brain.handle(inbound))
        stage = _label(turn.stage, ASSISTANT_STAGES)
        print("assistant stage=%s %s memory=%s" % (
            stage, _reply_summary(turn), _label(backend_label, MEMORY_BACKENDS)))
        return stage

    def memory_rows_fn() -> Any:
        return state_backend.rows if state_backend is not None else None

    reply_fn.memory_rows = memory_rows_fn  # type: ignore[attr-defined]
    return reply_fn, store.snapshot


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
    prior_assistant = prior.get("assistant") or {}

    bridge: Optional[TelegramAssistantBridge] = None
    snapshot_fn: Optional[Callable[[], Any]] = None
    if assistant_enabled():
        try:
            reply_fn, snapshot_fn = build_assistant_reply_fn(
                prior_assistant.get("history"), sheet=getattr(bot, "sheet", None),
                memory_rows=prior_assistant.get("memory"))
        except Exception as exc:  # noqa: BLE001 — votes must still run
            print("stage=assistant ok=false reason=%s (approvals continue)"
                  % type(exc).__name__)
        else:
            chat_id, _ = resolve("PA_APPROVAL_CHAT_ID", "TELEGRAM_CHAT_ID")
            bridge = TelegramAssistantBridge(
                reply_fn, allowed_senders=allowed_sender_ids(),
                chat_id=chat_id, seen=prior_assistant.get("seen"))
            bot.text_handler = bridge

    def _assistant_state() -> Dict[str, Any]:
        if bridge is None:
            return prior_assistant
        state = {"seen": bridge.seen,
                 "history": snapshot_fn() if snapshot_fn else []}
        rows_fn = getattr(bridge._reply_fn, "memory_rows", None)
        rows = rows_fn() if callable(rows_fn) else None
        # Sheet-backed memory is not copied into the cache; fallback rows are.
        state["memory"] = rows if rows is not None else prior_assistant.get("memory", [])
        return state

    try:
        report = bot.run_once()
    except Exception as exc:  # noqa: BLE001 — CI must classify, not crash
        # Persist sent-card progress (cards may already be out) but keep the
        # OLD offset so an unapplied callback is retried next cycle — the
        # same semantics as the long-running daemon. Replied message ids are
        # kept too, so the retried cycle does not answer a text twice.
        save_state(state_file,
                   {"sent": sorted(bot.state_snapshot()["sent"]),
                    "offset": prior_offset,
                    "assistant": _assistant_state()})
        print("stage=cycle ok=false reason=%s" % type(exc).__name__)
        print("error=%s" % str(exc)[:300])
        return 1

    snapshot = bot.state_snapshot()
    snapshot["assistant"] = _assistant_state()
    save_state(state_file, snapshot)
    print("stage=cycle ok=true cards_sent=%d callbacks_handled=%d"
          " granted=%d denied=%d"
          % (report.get("cards_sent", 0), report.get("callbacks_handled", 0),
             report.get("granted", 0), report.get("denied", 0)))
    if bridge is not None:
        stats = " ".join("%s=%d" % kv for kv in sorted(bridge.stats.items()))
        print("stage=assistant enabled=true %s" % (stats or "idle"))
    return 0


DEFAULT_ASSISTANT_PROBE = "مرحباً! عرّف بنفسك في سطرين، وقل لي ما الذي تستطيع مساعدتي فيه."


def _probe_sheet() -> Tuple[Any, str]:
    """(memory sheet | None, reason from ``SHEET_REASONS``) for the live probe."""
    raw, _ = resolve("GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_SERVICE_ACCOUNT")
    if not raw:
        return None, "no_credentials"
    sheet_id = os.getenv("PA_MEMORY_SHEET_ID", "").strip()
    if not sheet_id:
        return None, "no_sheet_id"
    try:
        import gspread  # noqa: PLC0415
    except ImportError:
        return None, "gspread_missing"
    try:
        client = gspread.authorize(build_sheet_credentials())
    except Exception:  # noqa: BLE001 — malformed JSON / key; never echo it
        return None, "bad_credentials"
    try:
        return client.open_by_key(sheet_id), "ok"
    except Exception:  # noqa: BLE001 — not shared with the SA, wrong id, API off
        return None, "open_failed"


def mode_assistant() -> int:
    """Live probe: one assistant reply into PA_APPROVAL_CHAT_ID.

    Proves end to end that the bot token works and which free provider
    answers. Exit 0 only when a real free model replied and Telegram
    accepted it; 3 when only the $0 fallback answered (no free provider
    configured/reachable) — so the run page tells the two apart.
    """
    import asyncio  # noqa: PLC0415

    ca = _import_assistant_module()
    chat_id, _ = resolve("PA_APPROVAL_CHAT_ID", "TELEGRAM_CHAT_ID")
    probe = os.getenv("AI2027_ASSISTANT_PROBE_TEXT", "").strip() or DEFAULT_ASSISTANT_PROBE
    readiness = []
    from ai2027_protocol.ai_providers import (  # noqa: PLC0415
        build_default_providers,
        describe_provider_readiness,
    )
    providers = build_default_providers()
    for item in describe_provider_readiness(providers):
        readiness.append("%s=%s" % (item.provider, item.credential_state))
    print("assistant providers: %s" % " ".join(readiness))
    sheet, sheet_reason = _probe_sheet()
    memory, backend_label, _ = open_celia_memory(ca, sheet)
    brain = ca.CeliaBrain(
        router=ca.build_assistant_router(providers=providers),
        sender_factory=lambda chat: ca.TelegramReplySender(chat_id=chat),
        store=ca.SnapshotConversationStore(), memory=memory,
        fallback_text=ca.CELIA_OWNER_FALLBACK)
    inbound = ca.ChannelInboundMessage(
        channel=ca.Channel.TELEGRAM, chat_id=chat_id, sender_id=chat_id,
        text=probe[:2000], message_id="probe-%d" % os.getpid())
    turn = asyncio.run(brain.handle(inbound))
    result = _label(turn.stage, ASSISTANT_STAGES)
    summary = "result=%s %s memory=%s sheet=%s" % (
        result, _reply_summary(turn), _label(backend_label, MEMORY_BACKENDS),
        _label(sheet_reason, SHEET_REASONS))
    print("stage=assistant-probe %s" % summary)
    if os.getenv("GITHUB_ACTIONS") == "true":
        # Run-page annotation: job logs may be unreachable. Allowlisted labels only.
        print("::notice title=assistant probe::%s" % summary)
    if result in PROBE_OK_STAGES:
        return 0
    if result == "no_provider_available":
        print("::warning title=assistant probe::no free provider answered —"
              " the $0 Arabic notice was sent instead. Add GEMINI_API_KEY /"
              " GROQ_API_KEY / OPENROUTER_API_KEY / NVIDIA_API_KEY secrets.")
        return 3
    return 1


def mode_welcome() -> int:
    """Reply to whatever the user sent (welcome loop). Sender's chat id is
    taken from the update itself — the stored PA_APPROVAL_CHAT_ID is only
    needed for preflight/configured(); replies never target it."""
    ab = _import_bot_module()
    transport = ab.TelegramTransport()
    updates = transport.fetch_updates() or []
    replied = 0
    for upd in updates:
        msg = upd.get("message") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is None:
            continue
        text = str(msg.get("text") or "")
        if text.startswith("/start"):
            body = ("مرحباً! هذا بوت موافقات CELIA — يعمل الآن عبر "
                    "GitHub Actions (مجاني). اكتب اختبار لتجربة المسار.")
        else:
            body = ("وصلت رسالتك ✓ — البوت حيّ ويرد عليك "
                    "(اختبار الرد التلقائي).")
        transport.send_text(body, chat_id=str(cid))
        replied += 1
    if updates:
        offset = max(int(u.get("update_id", 0)) for u in updates) + 1
        transport.fetch_updates(offset=offset)  # confirm: don't reply twice
    print("stage=welcome ok=true received=%d replied=%d"
          % (len(updates), replied))
    return 0


#: Telegram bot-token shape (``<bot id>:<secret>``), the one credential this
#: runner handles that kernel2027.SECRET_PATTERNS does not recognise — with the
#: ``bot`` prefix it has inside every Bot API URL (even truncated) or bare.
_TELEGRAM_TOKEN = re.compile(r"bot\d+:[A-Za-z0-9_-]{8,}|\b\d{5,}:[A-Za-z0-9_-]{30,}")


def diagnose_chat_id(raw: str, discovered: List[str], bot_id: str) -> List[str]:
    """Explain why Telegram may answer "chat not found" — WITHOUT echoing raw.

    Chat ids are not credentials (docs/SECRETS.md), but the stored value is
    still never printed: only its shape, its relation to the bot's own id, and
    whether it equals a chat that actually messaged this bot. The discovered
    ids are printed — the diagnostic step already publishes them.
    """
    raw = raw.strip()
    if not raw:
        return ["the stored chat id is EMPTY"]
    hints: List[str] = []
    shape = "digits-only" if re.fullmatch(r"-?\d+", raw) else "NOT digits-only"
    hints.append("stored value: %d chars, %s" % (len(raw), shape))
    if raw[:1] in "\"'" or raw[-1:] in "\"'":
        hints.append("it is wrapped in quotes — store the bare digits, no quotes")
    if raw.startswith("@"):
        hints.append("it is a @username — Telegram needs the numeric chat id")
    if "t.me" in raw or "://" in raw:
        hints.append("it is a link — store only the numeric chat id")
    if _TELEGRAM_TOKEN.search(raw):  # the token SHAPE — a bare ':' is also in every https:// link
        hints.append("it has the shape of a BOT TOKEN, not a chat id (rotate it if it was pasted anywhere public)")
    if bot_id and raw == bot_id:
        hints.append("it is the BOT's own id — a bot cannot message itself; use YOUR chat id")
    if discovered:
        if raw in discovered:
            hints.append("it MATCHES a chat that messaged this bot — so the token in use belongs to a different bot")
        else:
            hints.append("it matches none of the chats that messaged this bot: %s — store %s"
                         % (", ".join(discovered), discovered[0]))
    else:
        hints.append("no chat has messaged this bot yet — open the bot in Telegram, press Start, send any message")
    return hints


def _telegram_get(api: str, token: str, method: str) -> Dict[str, Any]:
    url = "%s/bot%s/%s" % (api.rstrip("/"), token, method)
    req = urllib.request.Request(url, headers={"User-Agent": "ai2027-approval-bot-ci"})
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 — fixed https base
        return json.loads(resp.read().decode("utf-8"))


def mode_chatcheck(source: str = "") -> int:
    """Failure-only diagnostic: compare the stored chat id with real chats.

    ``source`` is the secret name the id came from, captured by main() BEFORE
    apply_aliases() copies TELEGRAM_* onto the canonical keys.
    """
    token, _ = resolve("PA_APPROVAL_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")
    raw, _ = resolve("PA_APPROVAL_CHAT_ID", "TELEGRAM_CHAT_ID")
    api = os.getenv("PA_APPROVAL_API_BASE", "https://api.telegram.org")
    # getUpdates WITHOUT an offset only peeks: nothing is confirmed or consumed.
    me = _telegram_get(api, token, "getMe").get("result") or {}
    updates = _telegram_get(api, token, "getUpdates").get("result") or []
    discovered: List[str] = []
    for upd in updates:
        for key in ("message", "edited_message", "channel_post"):
            cid = ((upd.get(key) or {}).get("chat") or {}).get("id")
            if cid is not None and str(cid) not in discovered:
                discovered.append(str(cid))
    hints = diagnose_chat_id(raw, discovered, str(me.get("id") or ""))
    lines = ["source secret: %s" % (source or "none")] + hints
    for line in lines:
        print("chat-check: %s" % line)
        if os.getenv("GITHUB_ACTIONS") == "true":
            print("::notice title=chat-check::%s" % redact(line))
    return 0


def redact(text: str) -> str:
    """Strip anything token-shaped before it can reach a log or annotation.

    Delegates to the kernel (all provider patterns + the literal values this
    process was handed), then masks the Telegram token shape on top.
    """
    known = [os.getenv(name, "") for pair in ALIASES[:1] for name in pair]
    known.append(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", ""))
    # Free-tier model keys the assistant mode receives through env.
    known.extend(os.getenv(name, "") for name in (
        "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "NVIDIA_API_KEY",
        "AI2027_ASSISTANT_TELEGRAM_TOKEN"))
    return _TELEGRAM_TOKEN.sub("[redacted-token]", kernel_redact(text, secrets=known))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Telegram approval bot — CI runner (fail-closed)")
    parser.add_argument("--mode", choices=("test", "run", "welcome", "chatcheck",
                                           "assistant"),
                        default="test",
                        help="test: proof message · run: cards + votes"
                             " cycle · welcome: auto-reply loop · chatcheck:"
                             " why 'chat not found' (never prints the id)")
    args = parser.parse_args(argv)

    _, chat_source = resolve("PA_APPROVAL_CHAT_ID", "TELEGRAM_CHAT_ID")
    apply_aliases()
    missing = preflight(args.mode)
    if missing:
        print("preflight failed — missing configuration (names only):")
        for name in missing:
            print("  - %s" % name)
        # Surface the NAMES as a workflow annotation so the run page (and
        # the API) show exactly what to create — values never appear here.
        if os.getenv("GITHUB_ACTIONS") == "true":
            print("::error title=approval-bot preflight::missing"
                  " configuration (names only): %s" % " · ".join(missing))
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
        if args.mode == "welcome":
            return mode_welcome()
        if args.mode == "chatcheck":
            return mode_chatcheck(chat_source)
        if args.mode == "assistant":
            return mode_assistant()
        return mode_run(state_file)
    except Exception as exc:  # noqa: BLE001 — classified below, never leaked
        print("stage=runner ok=false reason=%s" % type(exc).__name__)
        detail = str(exc)
        if isinstance(exc, urllib.error.HTTPError):
            # The provider's description lives in the body — the only place
            # that says "chat not found" vs other 400s. Redacted below.
            try:
                detail = "%s | body=%s" % (
                    detail, exc.read().decode("utf-8", "replace")[:300])
            except Exception:  # noqa: BLE001 — body may be unreadable
                pass
        print("error=%s" % redact(detail)[:400])
        # Surface the failure on the run page (job logs may be unreachable);
        # redacted first — a token must never reach an annotation.
        if os.getenv("GITHUB_ACTIONS") == "true":
            print("::error title=approval-bot::%s: %s"
                  % (type(exc).__name__, redact(detail)[:400]))
        return 1


if __name__ == "__main__":
    sys.exit(main())
