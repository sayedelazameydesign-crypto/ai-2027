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
* Rollback = delete the webhook (``CELIA_WEBHOOK=off`` + ``/setup``): the next
  cycle sees no webhook and polls ``getUpdates`` again by itself.

The same channel carries CELIA's ONE memory: :class:`RelayMemoryBackend` makes
``PersonalMemory`` read and replace the Supabase ``celia_memory`` table, so the
Google Sheet tab is no longer written at all once the bot is integrated.

Stdlib only (the approval runner installs nothing for the transport).
"""

from __future__ import annotations

import hashlib
import json
import re
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
        #: ``instant`` when CELIA answers the owner's chat in the function.
        self.chat_mode = "relay"

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
        self.chat_mode = "instant" if response.get("chat") == "instant" else "relay"
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


class RelayMemoryBackend:
    """``PersonalMemory`` backend over the Supabase ``celia_memory`` table."""

    def __init__(self, client: RelayClient) -> None:
        self._client = client

    def load(self) -> List[Dict[str, Any]]:
        return self._client.memory_load()

    def save(self, rows: List[Dict[str, Any]]) -> None:
        self._client.memory_replace(rows)


def import_legacy_rows(client: RelayClient, legacy: Any) -> int:
    """Copy rows from the old state-file memory into Supabase ONCE (if it is empty).

    Never overwrites: when Supabase already holds memory, nothing happens.
    Returns the number of rows imported.
    """
    rows = [r for r in (legacy or []) if isinstance(r, dict)]
    if not rows or client.memory_load():
        return 0
    return client.memory_replace(rows)
