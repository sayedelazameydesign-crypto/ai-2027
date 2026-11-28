"""One integrated bot — the approval runner's relay client (scripts/celia_relay.py).

Hermetic: a fake Telegram (getWebhookInfo) and a fake Edge Function that
implements the same /relay contract as supabase/functions/celia-telegram
(index_test.ts proves the TypeScript side of the same contract).
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("celia_relay", ROOT / "scripts" / "celia_relay.py")
assert _spec and _spec.loader
relay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(relay)

TOKEN = "123:TEST"
FUNCTION = "https://ziaedqqhvsmcwcmlbcpi.supabase.co/functions/v1/celia-telegram"


class FakeEdge:
    """The /relay contract of the celia-telegram Edge Function, in memory."""

    def __init__(self, chat: str = "relay") -> None:
        self.queue: Dict[int, Dict[str, Any]] = {}
        self.memory: List[Dict[str, Any]] = []
        self.owners: List[str] = []
        self.calls: List[Dict[str, Any]] = []
        self.chat = chat
        self.down = False

    def post(self, url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        self.calls.append({"url": url, "payload": payload, "headers": headers})
        if self.down:
            raise OSError("connection refused")
        if headers.get("X-Celia-Relay-Secret") != relay.relay_secret(TOKEN):
            return {"ok": False}
        op = payload.get("op")
        if op == "updates":
            if "owners" in payload:
                self.owners = list(payload["owners"])
            offset = payload.get("offset")
            if offset is not None:
                self.queue = {k: v for k, v in self.queue.items() if k >= offset}
            return {"ok": True, "chat": self.chat,
                    "result": [self.queue[k] for k in sorted(self.queue)][:100]}
        if op == "memory_load":
            return {"ok": True, "rows": [dict(r) for r in self.memory]}
        if op == "memory_replace":
            self.memory = [dict(r) for r in payload["rows"]]
            return {"ok": True, "count": len(self.memory)}
        return {"ok": False}


def telegram_get(webhook_url: str):
    def get(url: str) -> Dict[str, Any]:
        assert url.endswith("/bot%s/getWebhookInfo" % TOKEN), url
        return {"ok": True, "result": {"url": webhook_url, "pending_update_count": 0}}
    return get


class TestAckPoint(unittest.TestCase):
    """The queue's ONLY delete-by-progress is ``update_id < p_offset`` at the
    start of a fetch — the same rule FakeEdge implements and the crash tests
    rely on. Reading never deletes; there is no 'deleted when read'."""

    def test_sql_and_fake_share_one_ack_rule(self):
        root = Path(__file__).resolve().parents[1]
        sql = "\n".join(p.read_text() for p in sorted((root / "supabase" / "migrations").glob("*.sql")))
        deletes = [line.strip() for line in sql.splitlines()
                   if "delete from public.celia_relay" in line.lower()]
        self.assertEqual(deletes, [
            "delete from public.celia_relay where update_id < p_offset;",
            "delete from public.celia_relay where received_at < now() - interval '7 days';",
        ])
        edge = FakeEdge()
        edge.queue = {5: {"update_id": 5}, 6: {"update_id": 6}}
        client = relay.RelayClient(FUNCTION, TOKEN, edge.post)
        self.assertEqual(len(client.fetch_updates(offset=None)), 2)
        self.assertEqual(sorted(edge.queue), [5, 6], "a read without progress deletes nothing")
        client.fetch_updates(offset=6)
        self.assertEqual(sorted(edge.queue), [6], "only ids below the saved pointer go")


class TestSecretParity(unittest.TestCase):
    def test_relay_secret_matches_the_edge_function(self):
        # Same constant is asserted in supabase/functions/celia-telegram/index_test.ts.
        self.assertEqual(relay.relay_secret(TOKEN),
                         "bb383f437d96b3aafee1e51a64091a3132549abadb7dbb1d62a40941540399e1")


class TestDiscovery(unittest.TestCase):
    def test_celia_webhook_enables_the_relay(self):
        client = relay.discover(TOKEN, http_get=telegram_get(FUNCTION + "/telegram"))
        self.assertIsNotNone(client)
        self.assertEqual(client.base, FUNCTION)

    def test_no_webhook_or_foreign_webhook_keeps_get_updates(self):
        for url in ("", "https://example.com/functions/v1/celia-telegram/telegram",
                    "http://ziaedqqhvsmcwcmlbcpi.supabase.co/functions/v1/celia-telegram/telegram",
                    FUNCTION + "/telegram?x=1", "https://x.supabase.co/functions/v1/other/telegram"):
            with self.subTest(url=url):
                self.assertIsNone(relay.discover(TOKEN, http_get=telegram_get(url)))

    def test_telegram_unreachable_or_no_token_falls_back(self):
        def boom(url):
            raise OSError("network down")
        self.assertIsNone(relay.discover(TOKEN, http_get=boom))
        for junk in ([], {"result": []}, {"result": {"url": None}}, None):
            self.assertIsNone(relay.discover(TOKEN, http_get=lambda url, j=junk: j))
        self.assertIsNone(relay.discover("", http_get=telegram_get(FUNCTION + "/telegram")))


class TestRelayClient(unittest.TestCase):
    def setUp(self):
        self.edge = FakeEdge()
        self.client = relay.RelayClient(FUNCTION, TOKEN, self.edge.post)

    def test_get_updates_semantics_and_owner_allowlist(self):
        self.edge.queue = {7: {"update_id": 7}, 8: {"update_id": 8}}
        got = self.client.fetch_updates(owners=["777", "not-an-id", " 778 "])
        self.assertEqual([u["update_id"] for u in got], [7, 8])
        self.assertEqual(self.edge.owners, ["777", "778"])
        self.assertNotIn("offset", self.edge.calls[0]["payload"], "no offset = peek")
        self.client.fetch_updates(offset=8)
        self.assertEqual(sorted(self.edge.queue), [8])
        self.assertEqual(self.edge.calls[0]["url"], FUNCTION + "/relay")

    def test_the_token_itself_never_travels(self):
        self.client.fetch_updates()
        self.assertNotIn(TOKEN, repr(self.edge.calls))

    def test_rejected_or_unreachable_relay_raises(self):
        bad = relay.RelayClient(FUNCTION, "wrong:token", self.edge.post)
        with self.assertRaises(RuntimeError):
            bad.fetch_updates()
        self.edge.down = True
        with self.assertRaises(OSError):
            self.client.fetch_updates()

    def test_chat_mode_is_reported(self):
        self.edge.chat = "instant"
        self.client.fetch_updates()
        self.assertEqual(self.client.chat_mode, "instant")


class TestRelayTransport(unittest.TestCase):
    def test_reads_the_queue_but_sends_through_the_original_transport(self):
        edge = FakeEdge()
        edge.queue = {3: {"update_id": 3, "callback_query": {"id": "q", "data": "appr:A1:grant"}}}

        class Original:
            configured = True
            sent: List[str] = []

            def send_text(self, text, chat_id=""):
                self.sent.append(text)
                return "1"

            def fetch_updates(self, offset=None, timeout=0):
                raise AssertionError("getUpdates must not be called when integrated")

        original = Original()
        transport = relay.RelayTransport(original, relay.RelayClient(FUNCTION, TOKEN, edge.post),
                                         owners=["777"])
        self.assertEqual(transport.fetch_updates(offset=None)[0]["update_id"], 3)
        self.assertTrue(transport.configured)
        transport.send_text("hi")
        self.assertEqual(original.sent, ["hi"])
        self.assertEqual(edge.owners, ["777"])


class TestOneMemory(unittest.TestCase):
    ROW = {"id": 1, "kind": "personal", "text": "أفضّل الردود القصيرة",
           "source": "telegram:1", "created_at": "2026-11-28T12:00:00Z", "confidence": 1.0}

    def test_backend_round_trips_through_supabase(self):
        edge = FakeEdge()
        backend = relay.RelayMemoryBackend(relay.RelayClient(FUNCTION, TOKEN, edge.post))
        backend.save([self.ROW])
        self.assertEqual(edge.memory, [self.ROW])
        self.assertEqual(backend.load(), [self.ROW])

    def test_legacy_rows_are_imported_once_and_never_overwrite(self):
        edge = FakeEdge()
        client = relay.RelayClient(FUNCTION, TOKEN, edge.post)
        self.assertEqual(relay.import_legacy_rows(client, [self.ROW, "junk"]), 1)
        newer = dict(self.ROW, id=2, text="معلومة أحدث")
        edge.memory = [newer]
        self.assertEqual(relay.import_legacy_rows(client, [self.ROW]), 0)
        self.assertEqual(edge.memory, [newer])
        self.assertEqual(relay.import_legacy_rows(client, []), 0)


class TestLegacyCopy(unittest.TestCase):
    SHEET = [  # what gspread returns: every cell a string
        {"id": "1", "kind": "personal", "text": "أفضّل  الردود القصيرة", "source": "telegram:4",
         "created_at": "2026-11-27T10:00:00Z", "confidence": "1.0"},
        {"id": "2", "kind": "secret-ish", "text": "نوع غير معروف", "source": "x", "created_at": "", "confidence": ""},
        {"id": "3", "kind": "project", "text": "x" * 501, "source": "x", "created_at": "", "confidence": ""},
        {"id": "4", "kind": "project", "text": "قرار المشروع", "source": "", "created_at": "not a date",
         "confidence": "7"},
    ]
    STATE = [{"id": 1, "kind": "personal", "text": "أفضّل الردود القصيرة", "source": "telegram:9",
              "created_at": "2026-11-28T09:00:00Z", "confidence": 1.0},
             {"id": 2, "kind": "semantic", "text": "معلومة من ملف الحالة", "source": "telegram:10",
              "created_at": "2026-11-28T09:30:00Z", "confidence": 0.5}]

    def test_rows_are_cleaned_to_what_the_table_accepts(self):
        rows = relay.sanitize_legacy_rows(self.SHEET, self.STATE, now="2026-11-28T12:00:00Z")
        self.assertEqual([r["text"] for r in rows],
                         ["أفضّل الردود القصيرة", "قرار المشروع", "معلومة من ملف الحالة"])
        self.assertEqual([r["id"] for r in rows], [1, 2, 3])
        bad = rows[1]
        self.assertEqual((bad["source"], bad["created_at"], bad["confidence"]),
                         ("legacy", "2026-11-28T12:00:00Z", 1.0))

    def test_copy_is_all_or_nothing_and_retried_next_cycle(self):
        edge = FakeEdge()
        client = relay.RelayClient(FUNCTION, TOKEN, edge.post)
        real_post = edge.post

        def failing(url, payload, headers):
            if payload.get("op") == "memory_replace":
                return {"ok": False}  # e.g. the transaction rolled back
            return real_post(url, payload, headers)

        edge.post = failing
        with self.assertRaises(RuntimeError):
            relay.import_legacy_rows(relay.RelayClient(FUNCTION, TOKEN, edge.post), self.SHEET, self.STATE)
        self.assertEqual(edge.memory, [], "nothing half-copied")
        edge.post = real_post
        self.assertEqual(relay.import_legacy_rows(client, self.SHEET, self.STATE), 3)
        self.assertEqual(relay.import_legacy_rows(client, self.SHEET, self.STATE), 0, "only once")


class _Polling:
    """The historical getUpdates transport, recording every poll."""

    def __init__(self, updates=()):
        self.updates = list(updates)
        self.polls: List[Any] = []

    def fetch_updates(self, offset=None, timeout=0):
        self.polls.append(offset)
        return list(self.updates)


class TestDrainThenPoll(unittest.TestCase):
    """Rollback: presses already acknowledged by the queue are never abandoned."""

    def _press(self, uid):
        return {"update_id": uid, "callback_query": {"id": "q%d" % uid, "data": "appr:A1:grant"}}

    def test_queued_presses_come_first_and_telegram_is_not_polled(self):
        edge, polling = FakeEdge(), _Polling([{"update_id": 90}])
        edge.queue = {41: self._press(41), 42: self._press(42)}
        t = relay.DrainThenPollTransport(polling, relay.RelayClient(FUNCTION, TOKEN, edge.post))
        self.assertEqual([u["update_id"] for u in t.fetch_updates(offset=41)], [41, 42])
        self.assertEqual(polling.polls, [], "no poll while the queue holds presses")
        self.assertIs(t.drained, False)

    def test_an_empty_queue_falls_through_to_getupdates_with_the_same_offset(self):
        edge, polling = FakeEdge(), _Polling([{"update_id": 90}])
        edge.queue = {41: self._press(41)}
        t = relay.DrainThenPollTransport(polling, relay.RelayClient(FUNCTION, TOKEN, edge.post))
        self.assertEqual(t.fetch_updates(offset=42), [{"update_id": 90}])
        self.assertEqual(edge.queue, {}, "processed rows confirmed on the way out")
        self.assertEqual(polling.polls, [42])
        self.assertIs(t.drained, True)

    def test_an_unreadable_queue_raises_and_never_polls(self):
        edge, polling = FakeEdge(), _Polling([{"update_id": 90}])
        edge.down = True
        t = relay.DrainThenPollTransport(polling, relay.RelayClient(FUNCTION, TOKEN, edge.post))
        with self.assertRaises(OSError):
            t.fetch_updates(offset=42)
        self.assertEqual(polling.polls, [], "later presses must not overtake queued ones")


class TestSelectSource(unittest.TestCase):
    NOW = 1_800_000_000.0

    def test_live_webhook_reads_the_relay_and_remembers_it(self):
        live = relay.RelayClient(FUNCTION, TOKEN)
        pick = relay.select_source(live, None, None, TOKEN, self.NOW)
        self.assertEqual((pick["source"], pick["relay"], pick["lost_at"]), ("relay", FUNCTION, None))

    def test_webhook_gone_with_a_remembered_queue_drains_first(self):
        pick = relay.select_source(None, FUNCTION, None, TOKEN, self.NOW)
        self.assertEqual(pick["source"], "drain")
        self.assertEqual(pick["client"].base, FUNCTION)
        self.assertEqual(pick["lost_at"], self.NOW, "the drain clock starts now")
        again = relay.select_source(None, FUNCTION, self.NOW, TOKEN, self.NOW + 600)
        self.assertEqual((again["source"], again["lost_at"]), ("drain", self.NOW))

    def test_the_clock_starts_at_the_first_cycle_that_sees_the_webhook_gone(self):
        # Rollback at 22:00, next cycle at 06:00 (8 h later, e.g. Actions paused):
        # the runner cannot know when the webhook really went, so the 6 h window
        # opens at 06:00 — the first drain attempt is never already "orphaned".
        rollback_22h, first_cycle_06h = self.NOW, self.NOW + 8 * 3600
        pick = relay.select_source(None, FUNCTION, None, TOKEN, first_cycle_06h)
        self.assertEqual((pick["source"], pick["lost_at"]), ("drain", first_cycle_06h))
        self.assertGreater(first_cycle_06h - rollback_22h, relay.DRAIN_DEADLINE_SECONDS)
        later = relay.select_source(None, FUNCTION, pick["lost_at"], TOKEN,
                                    first_cycle_06h + relay.DRAIN_DEADLINE_SECONDS - 60)
        self.assertEqual(later["source"], "drain", "still inside the window it opened")

    def test_past_the_deadline_the_queue_is_abandoned_loudly(self):
        late = self.NOW + relay.DRAIN_DEADLINE_SECONDS + 1
        pick = relay.select_source(None, FUNCTION, self.NOW, TOKEN, late)
        self.assertEqual((pick["source"], pick["client"], pick["relay"]), ("abandoned", None, None))
        self.assertLess(relay.DRAIN_DEADLINE_SECONDS, 24 * 3600, "inside Telegram's retention")

    def test_nothing_remembered_or_a_foreign_url_is_plain_polling(self):
        for remembered in (None, "", "https://evil.example.com/functions/v1/celia-telegram", 7):
            pick = relay.select_source(None, remembered, None, TOKEN, self.NOW)
            self.assertEqual(pick["source"], "getUpdates", remembered)


if __name__ == "__main__":
    unittest.main()
