"""One integrated Telegram bot: the approval runner's side of the relay.

Telegram delivers a bot's updates EITHER to a webhook OR to ``getUpdates`` —
never both. When @CeliaFashionBot's webhook points at the Supabase Edge
Function ``celia-telegram`` (CELIA's instant chat), that function queues every
update it does not answer itself (approval buttons, other chats, strangers)
and this module reads them back with ``getUpdates`` semantics: same update
ids, same offset confirmation. The approval logic in
``src/watchers/approval_bot.py`` is therefore untouched.

Discovery is automatic and needs NO new GitHub secret or variable:

* ``getWebhookInfo`` says where the bot's webhook points. Only a URL ending in
  :data:`WEBHOOK_SUFFIX` enables the relay; anything else (no webhook, another
  host) keeps the historical ``getUpdates`` path.
* The runner authenticates with ``X-Celia-Relay-Secret =
  sha256("celia-telegram-relay:" + bot token)`` — it already holds the token,
  and so does the function. The secret is never logged.
* Rollback = delete the webhook (``CELIA_WEBHOOK=off`` + ``/setup``). Rule:
  **queue first while non-empty, then poll** (:func:`select_source`). The
  runner remembers the relay it last used; a cycle without a webhook first
  drains that queue (presses the function already acknowledged to Telegram,
  which Telegram will never redeliver) and only polls ``getUpdates`` once the
  queue answers empty. If the queue cannot be read, the cycle does NOT poll
  (that would apply later presses before earlier ones) — until
  :data:`DRAIN_DEADLINE_SECONDS`, after which the remaining queued presses are
  declared orphaned (loud warning; their cards stay ``pending`` and can be
  pressed again) and polling resumes inside Telegram's 24 h retention.
* The opposite switch (poll -> queue) loses nothing: ``setWebhook`` is called
  with ``drop_pending_updates=false``, so Telegram hands every unconfirmed
  update to the webhook, and the queue drops ids below the stored offset.

The same channel carries CELIA's ONE memory: :class:`RelayMemoryBackend` makes
``PersonalMemory`` read and replace the Supabase ``celia_memory`` table, so the
Google Sheet tab is no longer written at all once the bot is integrated.

Stdlib only (the approval runner installs nothing for the transport).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Iterable, List, Optional

WEBHOOK_SUFFIX = "/functions/v1/celia-telegram/telegram"
#: The Edge Function origin must be a Supabase project (or local) URL.
_RELAY_ORIGIN = re.compile(r"^https://[a-z0-9-]+\.supabase\.(co|in)$|^http://(localhost|127\.0\.0\.1)(:\d+)?$")
_OWNER_ID = re.compile(r"^\d{1,20}$")

HttpPost = Callable[[str, Dict[str, Any], Dict[str, str]], Dict[str, Any]]
HttpGet = Callable[[str], Dict[str, Any]]


def relay_secret(token: str) -> str:
    """The header value the Edge Function expects from the approval runner."""
    return hashlib.sha256(("celia-telegram-relay:" + token).encode("utf-8")).hexdigest()


def _urllib_get(url: str) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "ai2027-approval-bot-ci"})
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 — fixed https base
        return json.loads(resp.read().decode("utf-8"))


def _urllib_post(url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "ai2027-approval-bot-ci", **headers})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 — discovered Supabase origin
        return json.loads(resp.read().decode("utf-8"))


def relay_base_from_webhook(url: str) -> Optional[str]:
    """``https://<ref>.supabase.co/functions/v1/celia-telegram`` or ``None``."""
    url = (url or "").strip()
    if not url.endswith(WEBHOOK_SUFFIX):
        return None
    parsed = urllib.parse.urlsplit(url)
    origin = "%s://%s" % (parsed.scheme, parsed.netloc)
    if not _RELAY_ORIGIN.match(origin) or parsed.query or parsed.fragment:
        return None
    return url[: -len("/telegram")]


class RelayClient:
    """Authenticated calls to ``POST <base>/relay`` (updates + shared memory)."""

    def __init__(self, base: str, token: str, http_post: Optional[HttpPost] = None) -> None:
        self.base = base.rstrip("/")
        self._headers = {"X-Celia-Relay-Secret": relay_secret(token)}
        self._post = http_post or _urllib_post
        #: ``instant`` when CELIA answers the owner's chat in the function,
        #: ``shadow`` while the function is transport only (the default).
        self.chat_mode = "shadow"

    def _call(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        response = self._post(self.base + "/relay", payload, self._headers)
        if not isinstance(response, dict) or not response.get("ok"):
            raise RuntimeError("celia relay call failed (op=%s)" % payload.get("op"))
        return response

    def fetch_updates(self, offset: Optional[int] = None,
                      owners: Iterable[str] = ()) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {"op": "updates"}
        if offset is not None:
            payload["offset"] = int(offset)
        allowed = [str(o).strip() for o in owners if _OWNER_ID.match(str(o).strip())]
        if allowed:
            payload["owners"] = allowed
        response = self._call(payload)
        self.chat_mode = "instant" if response.get("chat") == "instant" else "shadow"
        result = response.get("result")
        return [u for u in result if isinstance(u, dict)] if isinstance(result, list) else []

    def memory_load(self) -> List[Dict[str, Any]]:
        rows = self._call({"op": "memory_load"}).get("rows")
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    def memory_replace(self, rows: List[Dict[str, Any]]) -> int:
        return int(self._call({"op": "memory_replace", "rows": rows}).get("count") or 0)


def discover(token: str, api_base: str = "https://api.telegram.org",
             http_get: Optional[HttpGet] = None,
             http_post: Optional[HttpPost] = None) -> Optional[RelayClient]:
    """A :class:`RelayClient` when the bot's webhook is CELIA's function, else ``None``.

    Any failure to ask Telegram returns ``None``: the caller keeps the
    historical ``getUpdates`` path (which then reports its own error).
    """
    if not token:
        return None
    get = http_get or _urllib_get
    try:
        info = get("%s/bot%s/getWebhookInfo" % (api_base.rstrip("/"), token))
    except Exception:  # noqa: BLE001 — never let discovery break a cycle
        return None
    result = info.get("result") if isinstance(info, dict) else None
    url = result.get("url", "") if isinstance(result, dict) else ""
    base = relay_base_from_webhook(str(url or ""))
    return RelayClient(base, token, http_post) if base else None


class RelayTransport:
    """``TelegramTransport`` whose ``fetch_updates`` reads the relay queue.

    Every other method (``send_card``, ``send_text``, ``answer_callback``,
    ``configured`` …) is the original transport's — sending is unaffected by
    a webhook. The approval runner's allowlist rides along on each fetch so
    the function knows the owners without a second copy in Supabase.
    """

    def __init__(self, transport: Any, client: RelayClient, owners: Iterable[str] = ()) -> None:
        self._transport = transport
        self._client = client
        self._owners = [str(o) for o in owners]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._transport, name)

    def fetch_updates(self, offset: Optional[int] = None, timeout: int = 0) -> List[Dict[str, Any]]:
        del timeout  # the queue answers at once
        return self._client.fetch_updates(offset=offset, owners=self._owners)


#: How long a rollback keeps waiting for an unreadable queue before polling.
#: Well inside Telegram's 24 h retention of updates for ``getUpdates``.
DRAIN_DEADLINE_SECONDS = 6 * 3600


class DrainThenPollTransport(RelayTransport):
    """After a rollback: the relay queue first, ``getUpdates`` once it is empty.

    ``run_once`` fetches once per cycle with the stored offset. That fetch
    confirms (deletes) queued rows below the offset and returns the rest; if
    any remain, they are this cycle's updates and Telegram is not polled. Only
    an EMPTY answer falls through to ``getUpdates`` with the same offset —
    Telegram's update ids continue the same sequence, so order is preserved.
    A failed queue read raises: the cycle keeps its old offset and does not
    poll, exactly like any other failed fetch.
    """

    def __init__(self, transport: Any, client: RelayClient, owners: Iterable[str] = ()) -> None:
        super().__init__(transport, client, owners)
        #: ``None`` until fetched; then ``True`` once the queue answered empty.
        self.drained: Optional[bool] = None
        self.drained_count = 0

    def fetch_updates(self, offset: Optional[int] = None, timeout: int = 0) -> List[Dict[str, Any]]:
        pending = self._client.fetch_updates(offset=offset, owners=self._owners)
        if pending:
            self.drained = False
            self.drained_count = len(pending)
            return pending
        self.drained = True
        return self._transport.fetch_updates(offset=offset, timeout=timeout)


def stored_relay_base(value: Any) -> Optional[str]:
    """A relay base remembered in the runner state, re-validated like discovery."""
    if not isinstance(value, str) or not value:
        return None
    return relay_base_from_webhook(value.rstrip("/") + "/telegram")


def select_source(live: Optional[RelayClient], remembered: Any, lost_at: Any,
                  token: str, now: float,
                  http_post: Optional[HttpPost] = None) -> Dict[str, Any]:
    """Which queue this cycle reads — the cross-transport delivery rule.

    Returns ``{"source", "client", "relay", "lost_at"}`` where ``source`` is
    one of ``relay`` (webhook on CELIA), ``drain`` (webhook gone, remembered
    queue drained before polling), ``abandoned`` (queue unreadable past the
    deadline: poll, warn) or ``getUpdates``; ``relay``/``lost_at`` are what to
    persist for the next cycle.
    """
    if live is not None:
        return {"source": "relay", "client": live, "relay": live.base, "lost_at": None}
    base = stored_relay_base(remembered)
    if base is None or not token:
        return {"source": "getUpdates", "client": None, "relay": None, "lost_at": None}
    try:
        since = float(lost_at)
    except (TypeError, ValueError):
        since = now
    if now - since > DRAIN_DEADLINE_SECONDS:
        return {"source": "abandoned", "client": None, "relay": None, "lost_at": None}
    return {"source": "drain", "client": RelayClient(base, token, http_post),
            "relay": base, "lost_at": since}


class RelayMemoryBackend:
    """``PersonalMemory`` backend over the Supabase ``celia_memory`` table."""

    def __init__(self, client: RelayClient) -> None:
        self._client = client

    def load(self) -> List[Dict[str, Any]]:
        return self._client.memory_load()

    def save(self, rows: List[Dict[str, Any]]) -> None:
        self._client.memory_replace(rows)


_KINDS = ("personal", "project", "procedural", "semantic")
MAX_IMPORT_ROWS = 300  # PersonalMemory's default capacity


def _clean_created_at(value: Any, now: str) -> str:
    text = str(value or "").strip()
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return now
    return text if 10 <= len(text) <= 40 else now


def sanitize_legacy_rows(*sources: Any, now: Optional[str] = None) -> List[Dict[str, Any]]:
    """Legacy rows -> rows the Supabase ``celia_memory`` constraints accept.

    Sources are the read-only Google Sheet tab and the old state-file memory
    (Sheet values arrive as strings). Invalid rows are skipped, duplicates
    (same text, case/space-insensitive) are kept once, ids are renumbered
    1..n. Doing this up front means one bad cell can never make the single
    atomic import fail again and again.
    """
    stamp = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    seen = set()
    rows: List[Dict[str, Any]] = []
    for source in sources:
        for raw in source or []:
            if not isinstance(raw, dict):
                continue
            text = " ".join(str(raw.get("text") or "").split())
            kind = str(raw.get("kind") or "").strip().lower()
            key = text.casefold()
            if not text or len(text) > 500 or kind not in _KINDS or key in seen:
                continue
            try:
                confidence = float(raw.get("confidence", 1) or 1)
            except (TypeError, ValueError):
                confidence = 1.0
            seen.add(key)
            rows.append({
                "kind": kind,
                "text": text,
                "source": (str(raw.get("source") or "").strip() or "legacy")[:120],
                "created_at": _clean_created_at(raw.get("created_at"), stamp),
                "confidence": confidence if 0.0 <= confidence <= 1.0 else 1.0,
            })
    rows = rows[-MAX_IMPORT_ROWS:]
    for index, row in enumerate(rows, start=1):
        row["id"] = index
    return rows


def import_legacy_rows(client: RelayClient, *sources: Any) -> int:
    """Copy legacy memory into Supabase ONCE, only while Supabase is empty.

    One ``memory_replace`` call = one Postgres transaction (all rows or none),
    so a failure leaves the table empty and the next cycle simply retries.
    Never overwrites: when Supabase already holds memory, nothing happens.
    Returns the number of rows imported.
    """
    rows = sanitize_legacy_rows(*sources)
    if not rows or client.memory_load():
        return 0
    return client.memory_replace(rows)
