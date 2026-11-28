"""Hermetic tests for the CI runner (scripts/approval_bot_ci.py).

No network, no secrets: preflight must fail closed with NAMES only, the
TELEGRAM_* aliases must map onto the canonical keys, and the persistent
state store must round-trip (sent card ids + getUpdates offset).
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CI_PATH = ROOT / "scripts" / "approval_bot_ci.py"
BOT_PATH = ROOT / "src" / "watchers" / "approval_bot.py"

# Third-party stubs so importing approval_bot never touches the network
# (same pattern as test_approval_bot.py).
from tests.test_proactive_watcher import (  # noqa: E402
    _ensure_module,
    _load_watcher_module,
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ensure_module("gspread", authorize=lambda creds: None)
_ensure_module("google.oauth2.credentials", Credentials=object)
_ensure_module("googleapiclient.discovery", build=lambda *a, **k: None)
_load_watcher_module()

ci = _load("approval_bot_ci", CI_PATH)
ab = _load("approval_bot", BOT_PATH)

ENV_KEYS = (
    "PA_APPROVAL_BOT_TOKEN", "TELEGRAM_BOT_TOKEN",
    "PA_APPROVAL_CHAT_ID", "TELEGRAM_CHAT_ID",
    "PA_APPROVAL_ALLOWED_SENDERS", "TELEGRAM_ALLOWED_SENDERS",
    "PA_MEMORY_SHEET_ID", "GOOGLE_SERVICE_ACCOUNT_JSON",
    "GOOGLE_SERVICE_ACCOUNT", "BOT_STATE_FILE", "GITHUB_ACTIONS",
)


@contextlib.contextmanager
def clean_env():
    saved = {k: os.environ.pop(k, None) for k in ENV_KEYS}
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)  # created inside the context
            else:
                os.environ[key] = value


class TestPreflight(unittest.TestCase):
    def test_run_mode_lists_all_missing_names(self):
        with clean_env():
            missing = ci.preflight("run")
        joined = "\n".join(missing)
        self.assertIn("PA_APPROVAL_BOT_TOKEN", joined)
        self.assertIn("PA_APPROVAL_CHAT_ID", joined)
        self.assertIn("PA_MEMORY_SHEET_ID", joined)
        self.assertIn("GOOGLE_SERVICE_ACCOUNT_JSON", joined)

    def test_test_mode_needs_only_token_and_chat(self):
        with clean_env():
            os.environ["PA_APPROVAL_BOT_TOKEN"] = "t"
            os.environ["PA_APPROVAL_CHAT_ID"] = "c"
            self.assertEqual(ci.preflight("test"), [])

    def test_invalid_service_account_json_is_flagged(self):
        with clean_env():
            os.environ["PA_APPROVAL_BOT_TOKEN"] = "t"
            os.environ["PA_APPROVAL_CHAT_ID"] = "c"
            os.environ["PA_MEMORY_SHEET_ID"] = "s"
            os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = "not json"
            missing = ci.preflight("run")
        self.assertTrue(any("not a service account" in m for m in missing),
                        missing)

    def test_main_preflight_output_never_leaks_values(self):
        with clean_env():
            os.environ["TELEGRAM_BOT_TOKEN"] = "SUPER-SECRET-TOKEN-VALUE"
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                rc = ci.main(["--mode", "test"])
        out = buffer.getvalue()
        self.assertEqual(rc, 2)
        self.assertIn("PA_APPROVAL_CHAT_ID", out)  # name is printed
        self.assertNotIn("SUPER-SECRET-TOKEN-VALUE", out)  # value never

    def test_main_preflight_emits_annotation_in_actions(self):
        with clean_env():
            os.environ["GITHUB_ACTIONS"] = "true"
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                rc = ci.main(["--mode", "test"])
        out = buffer.getvalue()
        self.assertEqual(rc, 2)
        self.assertIn("::error title=approval-bot preflight::", out)
        self.assertIn("PA_APPROVAL_BOT_TOKEN", out)


class TestAliases(unittest.TestCase):
    def test_telegram_alias_maps_to_canonical_key(self):
        with clean_env():
            os.environ["TELEGRAM_BOT_TOKEN"] = "tok"
            os.environ["TELEGRAM_CHAT_ID"] = "42"
            ci.apply_aliases()
            self.assertEqual(os.environ.get("PA_APPROVAL_BOT_TOKEN"), "tok")
            self.assertEqual(os.environ.get("PA_APPROVAL_CHAT_ID"), "42")

    def test_canonical_key_wins_over_alias(self):
        with clean_env():
            os.environ["PA_APPROVAL_BOT_TOKEN"] = "canonical"
            os.environ["TELEGRAM_BOT_TOKEN"] = "alias"
            ci.apply_aliases()
            self.assertEqual(os.environ.get("PA_APPROVAL_BOT_TOKEN"),
                             "canonical")


class TestStateStore(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            ci.save_state(path, {"sent": ["A", "B"], "offset": 7})
            loaded = ci.load_state(path)
        self.assertEqual(loaded["sent"], ["A", "B"])
        self.assertEqual(loaded["offset"], 7)

    def test_corrupt_file_returns_safe_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{not json", encoding="utf-8")
            loaded = ci.load_state(path)
        self.assertEqual(loaded, {"sent": [], "offset": None})

    def test_non_dict_json_returns_safe_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("[1, 2]", encoding="utf-8")
            loaded = ci.load_state(path)
        self.assertEqual(loaded, {"sent": [], "offset": None})


class TestApprovalBotState(unittest.TestCase):
    """restore_state / state_snapshot — the CI process bridge."""

    @staticmethod
    def _bot():
        # __init__ would authorize gspread; the state methods only touch
        # _sent_cards/_offset, so build the object without side effects.
        bot = ab.ApprovalBot.__new__(ab.ApprovalBot)
        bot._sent_cards = set()
        bot._offset = None
        return bot

    def test_restore_and_snapshot_roundtrip(self):
        bot = self._bot()
        bot.restore_state(sent_ids=["01A", "01B"], offset=99)
        snap = bot.state_snapshot()
        self.assertEqual(snap["sent"], ["01A", "01B"])
        self.assertEqual(snap["offset"], 99)

    def test_restore_is_idempotent(self):
        bot = self._bot()
        bot.restore_state(sent_ids=["01A"], offset=1)
        bot.restore_state(sent_ids=["01A"], offset=1)
        self.assertEqual(bot.state_snapshot()["sent"], ["01A"])

    def test_restore_without_offset_keeps_none(self):
        bot = self._bot()
        bot.restore_state(sent_ids=["01A"])
        self.assertIsNone(bot.state_snapshot()["offset"])


class TestTransportSendText(unittest.TestCase):
    def test_send_text_posts_plain_payload(self):
        calls = []

        def post(url, payload):
            calls.append((url, payload))
            return {"ok": True, "result": {"message_id": "7"}}

        transport = ab.TelegramTransport(
            bot_token="T", chat_id="C", api_base="https://tg.example",
            http_post=post)
        message_id = transport.send_text("hello")
        self.assertEqual(message_id, "7")
        url, payload = calls[0]
        self.assertEqual(url, "https://tg.example/botT/sendMessage")
        self.assertEqual(payload, {"chat_id": "C", "text": "hello"})

    def test_send_text_unconfigured_fails_closed(self):
        transport = ab.TelegramTransport(
            bot_token="", chat_id="", api_base="https://tg.example",
            http_post=lambda u, p: {"ok": True})
        with self.assertRaises(RuntimeError):
            transport.send_text("hello")


if __name__ == "__main__":
    unittest.main()


class WelcomeModeTests(unittest.TestCase):
    def _run_welcome(self, updates):
        """Run mode_welcome with a fake transport; returns (rc, fake)."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "ci_under_test", ROOT / "scripts" / "approval_bot_ci.py")
        ci = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ci)

        class FakeTransport:
            def __init__(self):
                self.sent = []
                self.offsets = []

            def fetch_updates(self, offset=None, timeout=0):
                if offset is not None:
                    self.offsets.append(offset)
                    return []
                return list(updates)

            def send_text(self, text, chat_id=""):
                self.sent.append((text, str(chat_id)))
                return 42

        fake = FakeTransport()
        import types
        fake_module = types.SimpleNamespace(
            TelegramTransport=lambda: fake)
        orig = ci._import_bot_module
        ci._import_bot_module = lambda: fake_module
        try:
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                rc = ci.mode_welcome()
            return rc, fake, buf.getvalue()
        finally:
            ci._import_bot_module = orig

    def test_welcome_replies_to_start_and_confirms_offset(self):
        updates = [{"update_id": 5,
                    "message": {"chat": {"id": 777}, "text": "/start go"}}]
        rc, fake, out = self._run_welcome(updates)
        self.assertEqual(rc, 0)
        self.assertEqual(len(fake.sent), 1)
        text, chat_id = fake.sent[0]
        self.assertEqual(chat_id, "777")
        self.assertIn("مرحبا", text)
        self.assertEqual(fake.offsets, [6])
        self.assertIn("stage=welcome ok=true received=1 replied=1", out)

    def test_welcome_never_confirms_a_button_press_it_did_not_apply(self):
        updates = [
            {"update_id": 9,
             "message": {"chat": {"id": 42}, "text": "مرحبا"}},
            {"update_id": 10, "callback_query": {"id": "cb"}},  # a vote: run mode's job
            {"update_id": 11,
             "message": {"chat": {"id": 42}, "text": "بعد الزر"}},
        ]
        rc, fake, out = self._run_welcome(updates)
        self.assertEqual(rc, 0)
        self.assertEqual(len(fake.sent), 1, "stops at the press: nothing after it is consumed")
        self.assertEqual(fake.sent[0][1], "42")
        self.assertEqual(fake.offsets, [10], "confirms up to, NOT including, the press")
        self.assertIn("received=3 replied=1 held=2", out)

    def test_welcome_with_a_press_first_confirms_nothing(self):
        updates = [{"update_id": 7, "callback_query": {"id": "cb"}},
                   {"update_id": 8, "message": {"chat": {"id": 42}, "text": "hi"}}]
        rc, fake, out = self._run_welcome(updates)
        self.assertEqual(rc, 0)
        self.assertEqual((fake.sent, fake.offsets), ([], []))
        self.assertIn("received=2 replied=0 held=2", out)

    def test_welcome_no_updates_is_a_noop_success(self):
        rc, fake, out = self._run_welcome([])
        self.assertEqual(rc, 0)
        self.assertEqual(fake.sent, [])
        self.assertEqual(fake.offsets, [])
        self.assertIn("received=0 replied=0", out)


class TestRedaction(unittest.TestCase):
    """redact() delegates to kernel2027.redact and keeps Telegram coverage.

    kernel2027.SECRET_PATTERNS has no Telegram bot-token shape, so a bare
    swap to the kernel would pass scripts/kernel_guard.py while leaking the
    one credential this runner actually holds. These cases pin both halves.
    """

    TOKEN = "7123456789:AAHk3vQ9xY2zW8pL5mN0bC4dE7fG1hJ6kTq"  # shape only, not real

    def test_bot_api_url_token_is_masked(self):
        out = ci.redact("POST https://api.telegram.org/bot%s/sendMessage 400" % self.TOKEN)
        self.assertNotIn(self.TOKEN.split(":")[1], out)
        self.assertIn("[redacted-token]", out)
        self.assertIn("400", out)

    def test_truncated_url_token_is_still_masked(self):
        # The previous local regex masked bot<id>:<8+ chars>; that must hold.
        out = ci.redact("url=https://api.telegram.org/bot71234:AAHk3vQ9 cut")
        self.assertNotIn("AAHk3vQ9", out)

    def test_bare_token_without_bot_prefix_is_masked(self):
        out = ci.redact("token=%s" % self.TOKEN)
        self.assertNotIn(self.TOKEN.split(":")[1], out)

    def test_known_env_value_is_masked_whatever_its_shape(self):
        odd = "not-a-telegram-shape-but-secret-123"
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": odd, "PA_APPROVAL_BOT_TOKEN": ""}):
            out = ci.redact("auth failed for %s" % odd)
        self.assertNotIn(odd, out)

    def test_kernel_provider_patterns_now_apply(self):
        gh = "ghp_" + "A" * 36
        out = ci.redact("leaked %s and Bearer abcdefghijklmnop" % gh)
        self.assertNotIn(gh, out)
        self.assertNotIn("abcdefghijklmnop", out)

    def test_ordinary_diagnostics_are_untouched(self):
        text = "HTTPError: HTTP Error 400: Bad Request | body={\"description\":\"chat not found\"} at 12:30"
        self.assertEqual(ci.redact(text), text)

    def test_redact_is_kernel_delegation(self):
        with mock.patch.object(ci, "kernel_redact", wraps=ci.kernel_redact) as spy:
            ci.redact("x")
        spy.assert_called_once()


class TestChatCheck(unittest.TestCase):
    """--mode chatcheck explains "chat not found" without echoing the stored id."""

    BOT_ID = "8293176243"
    MINE = "722947046"

    def test_wrong_number_points_to_the_discovered_chat(self):
        hints = ci.diagnose_chat_id("722947064", [self.MINE], self.BOT_ID)
        self.assertIn("digits-only", hints[0])
        self.assertTrue(any("store %s" % self.MINE in h for h in hints))
        self.assertFalse(any("722947064" in h for h in hints), "stored value echoed")

    def test_common_paste_mistakes_are_named(self):
        cases = {
            '"722947046"': "quotes",
            "@sayed_elazamey": "@username",
            "https://t.me/sayed_elazamey": "link",
            "8293176243": "BOT's own id",
            "8293176243:AAHk3vQ9xY2zW8pL5mN0bC4dE7fG1hJ6kTq": "BOT TOKEN",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw[:12]):
                hints = ci.diagnose_chat_id(raw, [self.MINE], self.BOT_ID)
                self.assertTrue(any(expected in h for h in hints), hints)
                self.assertFalse(any(raw in h for h in hints), "stored value echoed")

    def test_a_link_is_not_misreported_as_a_token(self):
        # Measured in Actions 2026-11-28: a 44-char https:// link tripped the old
        # bare-':' rule and told the owner to rotate a token that was never leaked.
        for raw in ("https://t.me/sayed_elazamey", "https://web.telegram.org/k/#722947046"):
            with self.subTest(raw=raw):
                hints = ci.diagnose_chat_id(raw, [self.MINE], self.BOT_ID)
                self.assertTrue(any("link" in h for h in hints), hints)
                self.assertFalse(any("TOKEN" in h for h in hints), hints)

    def test_match_means_the_token_belongs_to_another_bot(self):
        hints = ci.diagnose_chat_id(self.MINE, [self.MINE], self.BOT_ID)
        self.assertTrue(any("different bot" in h for h in hints))

    def test_empty_and_no_chats(self):
        self.assertEqual(ci.diagnose_chat_id("  ", [], self.BOT_ID), ["the stored chat id is EMPTY"])
        self.assertTrue(any("press Start" in h for h in ci.diagnose_chat_id("1", [], self.BOT_ID)))

    def test_mode_runs_against_a_fake_telegram_and_never_prints_value_or_token(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        token = "%s:AAHk3vQ9xY2zW8pL5mN0bC4dE7fG1hJ6kTq" % self.BOT_ID
        seen = []

        class Fake(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                seen.append(self.path)
                if self.path.endswith("/getMe"):
                    body = {"ok": True, "result": {"id": int(TestChatCheck.BOT_ID)}}
                else:
                    body = {"ok": True, "result": [
                        {"update_id": 1, "message": {"chat": {"id": int(TestChatCheck.MINE)}}}]}
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                return

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        env = {"PA_APPROVAL_BOT_TOKEN": token, "PA_APPROVAL_CHAT_ID": "",
               "TELEGRAM_CHAT_ID": "@sayed_elazamey", "TELEGRAM_BOT_TOKEN": "",
               "PA_APPROVAL_API_BASE": "http://127.0.0.1:%d" % srv.server_address[1],
               "GITHUB_ACTIONS": "true"}
        try:
            with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()) as out:
                rc = ci.main(["--mode", "chatcheck"])
        finally:
            srv.shutdown()
            srv.server_close()
        text = out.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("source secret: TELEGRAM_CHAT_ID", text)
        self.assertIn("@username", text)
        self.assertIn("store %s" % self.MINE, text)
        self.assertIn("::notice title=chat-check::", text)
        self.assertNotIn("sayed_elazamey", text)
        self.assertNotIn(token.split(":")[1], text)
        self.assertTrue(all("offset" not in p for p in seen), "chatcheck must not consume updates")


# ---------------------------------------------------------------------------
# Channel assistant on the approval poll (docs/CHANNEL-ASSISTANT.md §11)
# ---------------------------------------------------------------------------
OWNER_CHAT = "5550001"
OWNER = "777"


def _text_update(update_id, text, *, chat=OWNER_CHAT, sender=OWNER, message_id=None):
    return {"update_id": update_id, "message": {
        "message_id": message_id if message_id is not None else update_id,
        "chat": {"id": int(chat)}, "from": {"id": int(sender)},
        "date": 1795878000, "text": text}}


class TestTelegramAssistantBridge(unittest.TestCase):
    def _bridge(self, allowed=(OWNER,), chat=OWNER_CHAT, seen=None, reply="replied"):
        calls = []

        def reply_fn(update):
            calls.append(update["message"]["text"])
            if isinstance(reply, Exception):
                raise reply
            return reply

        return ci.TelegramAssistantBridge(
            reply_fn, allowed_senders=list(allowed), chat_id=chat, seen=seen), calls

    def test_owner_text_in_owner_chat_is_answered(self):
        bridge, calls = self._bridge()
        self.assertEqual(bridge(_text_update(1, "ما الجديد؟")), "replied")
        self.assertEqual(calls, ["ما الجديد؟"])
        self.assertEqual(bridge.seen, ["%s:1" % OWNER_CHAT])

    def test_fail_closed_filters_never_reach_the_model(self):
        bridge, calls = self._bridge()
        cases = {
            "ignored_sender_not_allowed": _text_update(2, "hi", sender="666"),
            "ignored_other_chat": _text_update(3, "hi", chat="999"),
            "ignored_command": _text_update(4, "/start"),
            "ignored_not_text": {"update_id": 5, "message": {
                "message_id": 5, "chat": {"id": int(OWNER_CHAT)},
                "from": {"id": int(OWNER)}, "photo": [{}]}},
            "ignored_not_message": {"update_id": 6, "callback_query": {"id": "cb"}},
        }
        for expected, update in cases.items():
            with self.subTest(case=expected):
                self.assertEqual(bridge(update), expected)
        self.assertEqual(calls, [])

    def test_empty_allowlist_answers_nobody(self):
        bridge, calls = self._bridge(allowed=())
        self.assertEqual(bridge(_text_update(1, "hi")), "ignored_sender_not_allowed")
        self.assertEqual(calls, [])

    def test_missing_chat_id_answers_nobody(self):
        bridge, calls = self._bridge(chat="")
        self.assertEqual(bridge(_text_update(1, "hi")), "ignored_other_chat")
        self.assertEqual(calls, [])

    def test_restored_seen_ids_prevent_a_second_answer(self):
        bridge, calls = self._bridge(seen=["%s:1" % OWNER_CHAT])
        self.assertEqual(bridge(_text_update(1, "again")), "duplicate")
        self.assertEqual(calls, [])

    def test_reply_errors_are_classified_and_still_marked_seen(self):
        bridge, _ = self._bridge(reply=RuntimeError("boom"))
        self.assertEqual(bridge(_text_update(1, "hi")), "error_RuntimeError")
        self.assertEqual(bridge(_text_update(1, "hi")), "duplicate")  # at-most-once
        self.assertEqual(bridge.stats, {"error_RuntimeError": 1, "duplicate": 1})

    def test_seen_list_is_bounded(self):
        bridge, _ = self._bridge(seen=["x:%d" % i for i in range(ci.ASSISTANT_SEEN_LIMIT + 50)])
        self.assertEqual(len(bridge.seen), ci.ASSISTANT_SEEN_LIMIT)
        bridge(_text_update(1, "hi"))
        self.assertEqual(len(bridge.seen), ci.ASSISTANT_SEEN_LIMIT)
        self.assertEqual(bridge.seen[-1], "%s:1" % OWNER_CHAT)

    def test_flag_parsing(self):
        with mock.patch.dict(os.environ, {ci.ASSISTANT_FLAG: ""}):
            self.assertFalse(ci.assistant_enabled())
        for value in ("1", "true", "On"):
            with mock.patch.dict(os.environ, {ci.ASSISTANT_FLAG: value}):
                self.assertTrue(ci.assistant_enabled())


class TestAssistantState(unittest.TestCase):
    def test_assistant_state_roundtrips_and_defaults_stay_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            ci.save_state(path, {"sent": ["A"], "offset": 3, "assistant": {
                "seen": ["1:2"], "history": [{"channel": "telegram", "chat_id": "1",
                                              "turns": [["user", "سؤال"]]}]}})
            loaded = ci.load_state(path)
            self.assertEqual(loaded["assistant"]["seen"], ["1:2"])
            self.assertEqual(loaded["assistant"]["history"][0]["turns"], [["user", "سؤال"]])
            ci.save_state(path, {"sent": [], "offset": None})
            self.assertEqual(ci.load_state(path), {"sent": [], "offset": None})


class TestRunOnceTextHandler(unittest.TestCase):
    @staticmethod
    def _bot(updates):
        bot = ab.ApprovalBot.__new__(ab.ApprovalBot)
        bot._sent_cards = set()
        bot._offset = None
        bot.transport = mock.Mock()
        bot.transport.fetch_updates.return_value = list(updates)
        bot.gate = mock.Mock()
        bot.gate.send_pending_cards.return_value = []
        bot.gate.handle_callback.return_value = {"ok": True, "state": ab.STATE_GRANTED}
        return bot

    UPDATES = [
        _text_update(10, "سؤال"),
        {"update_id": 11, "callback_query": {"id": "cb"}},
        _text_update(12, "سؤال آخر"),
    ]

    def test_without_handler_behaviour_and_report_are_unchanged(self):
        bot = self._bot(self.UPDATES)
        report = bot.run_once()
        self.assertEqual(report, {"cards_sent": 0, "callbacks_handled": 1,
                                  "granted": 1, "denied": 0})
        self.assertEqual(bot._offset, 13)

    def test_handler_gets_texts_and_votes_still_apply(self):
        bot = self._bot(self.UPDATES)
        seen = []
        bot.text_handler = lambda u: seen.append(u["update_id"])
        report = bot.run_once()
        self.assertEqual(seen, [10, 12])
        self.assertEqual(report["callbacks_handled"], 1)
        self.assertEqual(report["texts_handled"], 2)
        self.assertEqual(report["text_errors"], 0)
        bot.gate.handle_callback.assert_called_once()

    def test_handler_failure_never_blocks_a_vote(self):
        bot = self._bot(self.UPDATES)

        def boom(update):
            raise RuntimeError("model down")

        bot.text_handler = boom
        report = bot.run_once()
        self.assertEqual(report["text_errors"], 2)
        self.assertEqual(report["granted"], 1)
        self.assertEqual(bot._offset, 13)


class TestModeRunAssistantWiring(unittest.TestCase):
    def _run(self, *, flag, build=None, updates=()):
        fake_bot = TestRunOnceTextHandler._bot(updates)
        fake_module = mock.Mock()
        fake_module.ApprovalBot.return_value = fake_bot
        env = {ci.ASSISTANT_FLAG: flag, "PA_APPROVAL_CHAT_ID": OWNER_CHAT,
               "PA_APPROVAL_ALLOWED_SENDERS": OWNER}
        with tempfile.TemporaryDirectory() as tmp, clean_env(), \
                mock.patch.dict(os.environ, env), \
                mock.patch.object(ci, "_import_bot_module", return_value=fake_module), \
                mock.patch.object(ci, "build_sheet_credentials", return_value=object()), \
                mock.patch.object(ci, "build_assistant_reply_fn", build or mock.Mock()) as b:
            path = Path(tmp) / "state.json"
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc = ci.mode_run(path)
            return rc, ci.load_state(path), out.getvalue(), fake_bot, b

    def test_flag_off_never_builds_the_assistant(self):
        rc, state, out, bot, build = self._run(flag="0", updates=[_text_update(1, "hi")])
        self.assertEqual(rc, 0)
        build.assert_not_called()
        self.assertNotIn("stage=assistant", out)
        self.assertFalse(getattr(bot, "text_handler", None))

    def test_flag_on_replies_and_persists_seen_and_history(self):
        answered = []
        seen_kwargs = {}

        def build(history, **kwargs):
            seen_kwargs.update(kwargs)
            return (lambda u: answered.append(u["message"]["text"]) or "replied",
                    lambda: [{"channel": "telegram", "chat_id": OWNER_CHAT,
                              "turns": [["user", "hi"], ["assistant", "أهلاً"]]}])

        rc, state, out, _, _ = self._run(flag="1", build=build,
                                         updates=[_text_update(1, "hi"),
                                                  _text_update(2, "x", sender="666")])
        self.assertEqual(rc, 0)
        self.assertEqual(answered, ["hi"])
        self.assertEqual(state["assistant"]["seen"], ["%s:1" % OWNER_CHAT])
        self.assertEqual(state["assistant"]["history"][0]["turns"][1], ["assistant", "أهلاً"])
        self.assertIn("ignored_sender_not_allowed=1", out)
        self.assertIn("replied=1", out)
        # CELIA's durable memory is offered the bot's own sheet handle.
        self.assertIn("sheet", seen_kwargs)
        self.assertIn("memory_rows", seen_kwargs)

    def test_assistant_build_failure_keeps_approvals_running(self):
        def build(history, **kwargs):
            raise ImportError("python-2027 not installed")

        rc, state, out, bot, _ = self._run(
            flag="1", build=build,
            updates=[{"update_id": 1, "callback_query": {"id": "cb"}}])
        self.assertEqual(rc, 0)
        self.assertIn("stage=assistant ok=false reason=ImportError", out)
        self.assertIn("callbacks_handled=1", out)


def _real_assistant():
    try:
        return ci._import_assistant_module()
    except Exception:  # noqa: BLE001 — python-2027 not installed (e.g. py3.10)
        return None


@unittest.skipUnless(_real_assistant(), "python-2027 not installed")
class TestRealAssistantOverTelegram(unittest.TestCase):
    """Real channel_assistant + router; fake free model and fake Bot API."""

    def _patches(self, providers):
        ca = _real_assistant()
        from ai2027_protocol.budget import BudgetPolicy
        from ai2027_protocol.router import FreeFirstRouter

        posts = []

        async def fake_post(self_, url, payload):
            posts.append((url.rsplit("/", 1)[-1], payload))
            return {"ok": True}

        def router(providers_=None, **_):
            return FreeFirstRouter(providers=providers_ or providers,
                                   budget_policy=BudgetPolicy(max_spend_usd=0.0))

        return ca, posts, [
            mock.patch.object(ca.TelegramReplySender, "_httpx_post", fake_post),
            mock.patch.object(ca, "build_assistant_router", router),
            mock.patch.dict(os.environ, {"PA_APPROVAL_BOT_TOKEN": "tok",
                                         "PA_APPROVAL_CHAT_ID": OWNER_CHAT}),
        ]

    @staticmethod
    def _free(text):
        from ai2027_protocol.contracts import BillingTier, ProviderKind

        class P:
            kind = ProviderKind.GROQ
            model = "groq-model"
            is_remote = True
            billing_tier = BillingTier.FREE_TIER
            system_prompts = []

            def is_configured(self):
                return True

            async def complete(self, request, *, timeout_s):
                self.system_prompts.append(request.system_prompt)
                return text

        return P()

    def test_cycle_reply_fn_answers_in_the_sender_chat_with_history(self):
        free = self._free("أهلاً بك!")
        ca, posts, patches = self._patches([free])
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            reply_fn, snapshot = ci.build_assistant_reply_fn(None)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                stage = reply_fn(_text_update(1, "مرحبا"))
        self.assertEqual(stage, "replied")
        self.assertEqual(posts, [("sendMessage", {"chat_id": OWNER_CHAT, "text": "أهلاً بك!"})])
        self.assertNotIn("getUpdates", [m for m, _ in posts])
        self.assertEqual(snapshot()[0]["turns"], [["user", "مرحبا"], ["assistant", "أهلاً بك!"]])
        self.assertIn("provider=groq", out.getvalue())
        self.assertNotIn("tok", out.getvalue())
        # The owner talks to CELIA in owner scope, never the generic prompt.
        self.assertEqual(free.system_prompts, [ca.CELIA_OWNER_PROMPT])

    def test_probe_mode_exit_codes(self):
        from ai2027_protocol.ai_providers import DeterministicFallbackProvider

        for providers, expected_rc, expected_text in (
                ([self._free("أنا مساعد AI-2027.")], 0, "أنا مساعد AI-2027."),
                ([DeterministicFallbackProvider()], 3, None)):
            ca, posts, patches = self._patches(providers)
            with self.subTest(rc=expected_rc), contextlib.ExitStack() as stack:
                for p in patches:
                    stack.enter_context(p)
                stack.enter_context(mock.patch(
                    "ai2027_protocol.ai_providers.build_default_providers",
                    return_value=tuple(providers)))
                stack.enter_context(mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}))
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    rc = ci.mode_assistant()
                self.assertEqual(rc, expected_rc, out.getvalue())
                sent = posts[0][1]["text"]
                self.assertEqual(sent, expected_text or ca.CELIA_OWNER_FALLBACK)
                self.assertIn("stage=assistant-probe", out.getvalue())
                expected = "replied" if expected_rc == 0 else "no_provider_available"
                self.assertIn("::notice title=assistant probe::result=%s" % expected,
                              out.getvalue())
                self.assertNotIn("أنا مساعد", out.getvalue())  # reply text never logged


class TestRedactionCoversModelKeys(unittest.TestCase):
    def test_free_tier_model_keys_are_masked(self):
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "plainvalue-groq-123456",
                                          "OPENROUTER_API_KEY": "or-plain-987654321"}):
            out = ci.redact("failed with plainvalue-groq-123456 and or-plain-987654321")
        self.assertNotIn("plainvalue-groq-123456", out)
        self.assertNotIn("or-plain-987654321", out)


class TestAssistantLogAllowlist(unittest.TestCase):
    """Only allowlisted constants reach the log (CodeQL clear-text logging)."""

    def test_unknown_values_never_printed(self):
        reply = types.SimpleNamespace(
            stage="replied", provider="chat-1234567890-secret", billing_tier="free_tier",
            cost_usd=0.0, chunks_sent=3)
        line = ci._reply_summary(reply)
        self.assertEqual(
            line, "mode=- provider=other tier=free_tier cost_usd=0.00 chunks=2+ memories=0")
        self.assertEqual(ci._label("weird private text", ci.ASSISTANT_STAGES), "other")
        self.assertEqual(ci._label(None, ci.ASSISTANT_STAGES), "-")

    def test_enum_values_are_recognised(self):
        enum_like = types.SimpleNamespace(value="groq")
        self.assertEqual(ci._label(enum_like, ci.ASSISTANT_PROVIDERS), "groq")
        self.assertIn("cost_usd=NONZERO", ci._reply_summary(types.SimpleNamespace(cost_usd=0.5)))


class _FakeWorksheet:
    def __init__(self):
        self.values = []

    def get_all_values(self):
        return [list(r) for r in self.values]

    def clear(self):
        self.values = []

    def update(self, range_name=None, values=None):
        assert range_name == "A1"
        self.values = [list(r) for r in values]


class WorksheetNotFound(Exception):
    """Same class name as ``gspread.WorksheetNotFound``."""


class _FakeSheet:
    def __init__(self, broken=False):
        self.tabs = {}
        self.broken = broken

    def worksheet(self, title):
        if self.broken:
            raise OSError("quota")
        if title not in self.tabs:
            raise WorksheetNotFound(title)
        return self.tabs[title]

    def add_worksheet(self, title, rows, cols):
        self.tabs[title] = _FakeWorksheet()
        return self.tabs[title]


class TestSheetMemoryBackend(unittest.TestCase):
    HEADERS = ("id", "kind", "text", "source", "created_at", "confidence")

    def test_tab_is_created_on_first_write_and_round_trips(self):
        sheet = _FakeSheet()
        backend = ci.SheetMemoryBackend(sheet, self.HEADERS)
        self.assertEqual(backend.load(), [])
        self.assertEqual(sheet.tabs, {})  # reading never creates the tab
        backend.save([{"id": 1, "kind": "personal", "text": "أحب الشاي",
                       "source": "telegram:1", "created_at": "2026-11-28T10:00:00Z",
                       "confidence": 1.0}])
        ws = sheet.tabs[ci.CELIA_MEMORY_TAB]
        self.assertEqual(ws.values[0], list(self.HEADERS))
        self.assertEqual(backend.load()[0]["text"], "أحب الشاي")
        self.assertEqual(backend.load()[0]["id"], "1")  # sheet cells are text

    def test_a_broken_sheet_raises_instead_of_reading_empty(self):
        backend = ci.SheetMemoryBackend(_FakeSheet(broken=True), self.HEADERS)
        with self.assertRaises(OSError):
            backend.load()


@unittest.skipUnless(_real_assistant(), "python-2027 not installed")
class TestCeliaMemoryOverTelegram(TestRealAssistantOverTelegram):
    """CELIA end to end: commands, durable sheet memory, restart, fallback."""

    def _cycle(self, sheet, texts, *, memory_rows=None, model_text="تمام."):
        free = self._free(model_text)
        ca, posts, patches = self._patches([free])
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            reply_fn, _ = ci.build_assistant_reply_fn(
                None, sheet=sheet, memory_rows=memory_rows)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                stages = [reply_fn(_text_update(i + 1, t)) for i, t in enumerate(texts)]
        return stages, [p["text"] for _, p in posts], free, out.getvalue(), reply_fn

    def test_remember_survives_the_next_cycle_and_feeds_the_model(self):
        sheet = _FakeSheet()
        stages, sent, free, out, _ = self._cycle(
            sheet, ["تذكّري: مشروع CELIA يستعمل ورقة الذاكرة نفسها"])
        self.assertEqual(stages, ["memory_saved"])
        self.assertTrue(sent[0].startswith("حفظتُ ✅ رقم 1"))
        self.assertEqual(free.system_prompts, [])  # no model call for a command
        self.assertIn("memory=sheet", out)
        self.assertNotIn("ورقة الذاكرة", out)  # memory text is never logged

        # Next cycle = a brand-new process: the fact comes back from the sheet.
        stages, sent, free, out, _ = self._cycle(
            sheet, ["ماذا تتذكرين؟", "ما خطة مشروع CELIA اليوم؟"])
        self.assertEqual(stages, ["memory_listed", "replied"])
        self.assertIn("مشروع CELIA يستعمل ورقة الذاكرة نفسها", sent[0])
        self.assertIn("مشروع CELIA يستعمل ورقة الذاكرة نفسها", free.system_prompts[0])
        self.assertIn("mode=executive", out)
        self.assertIn("memories=1-3", out)

    def test_secret_is_refused_and_nothing_reaches_the_sheet(self):
        sheet = _FakeSheet()
        stages, sent, _, _, _ = self._cycle(sheet, ["احفظي: كلمة السر hunter2"])
        self.assertEqual(stages, ["memory_refused_sensitive"])
        self.assertEqual(sheet.tabs, {})
        self.assertNotIn("hunter2", sent[0])

    def test_broken_sheet_falls_back_to_the_state_file(self):
        stages, _, _, out, reply_fn = self._cycle(
            _FakeSheet(broken=True), ["تذكّري: أفضّل الردود القصيرة"], memory_rows=[])
        self.assertEqual(stages, ["memory_saved"])
        self.assertIn("memory=state", out)
        rows = reply_fn.memory_rows()
        self.assertEqual(rows[0]["text"], "أفضّل الردود القصيرة")
        # ...and the next cycle reads them back from the state file.
        stages, sent, _, _, _ = self._cycle(None, ["ماذا تتذكرين؟"], memory_rows=rows)
        self.assertIn("أفضّل الردود القصيرة", sent[0])

    def test_probe_runs_memory_commands_with_the_sheet(self):
        sheet = _FakeSheet()
        free = self._free("unused")
        _, posts, patches = self._patches([free])
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(mock.patch.object(ci, "_probe_sheet",
                                                  return_value=(sheet, "ok")))
            stack.enter_context(mock.patch(
                "ai2027_protocol.ai_providers.build_default_providers",
                return_value=(free,)))
            stack.enter_context(mock.patch.dict(os.environ, {
                "GITHUB_ACTIONS": "true",
                "AI2027_ASSISTANT_PROBE_TEXT": "تذكّري: اختبار الذاكرة الحي"}))
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc = ci.mode_assistant()
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("result=memory_saved", out.getvalue())
        self.assertIn("memory=sheet sheet=ok", out.getvalue())
        self.assertEqual(sheet.tabs[ci.CELIA_MEMORY_TAB].values[1][2], "اختبار الذاكرة الحي")


class TestProbeSheetReasons(unittest.TestCase):
    def _reason(self, env):
        with mock.patch.dict(os.environ, env, clear=True):
            return ci._probe_sheet()

    def test_each_missing_piece_is_named_without_leaking_values(self):
        self.assertEqual(self._reason({}), (None, "no_credentials"))
        self.assertEqual(self._reason({"GOOGLE_SERVICE_ACCOUNT_JSON": "{}"}),
                         (None, "no_sheet_id"))
        with mock.patch.dict(sys.modules, {"gspread": None}):
            self.assertEqual(self._reason({"GOOGLE_SERVICE_ACCOUNT_JSON": "{}",
                                           "PA_MEMORY_SHEET_ID": "s"}),
                             (None, "gspread_missing"))

    def test_open_failure_is_distinguished_from_bad_credentials(self):
        fake = types.SimpleNamespace(authorize=mock.Mock(side_effect=ValueError("k")))
        env = {"GOOGLE_SERVICE_ACCOUNT_JSON": "{}", "PA_MEMORY_SHEET_ID": "s"}
        with mock.patch.dict(sys.modules, {"gspread": fake}), \
                mock.patch.object(ci, "build_sheet_credentials", return_value=object()):
            self.assertEqual(self._reason(env), (None, "bad_credentials"))
            client = mock.Mock()
            client.open_by_key.side_effect = PermissionError("not shared")
            fake.authorize = mock.Mock(return_value=client)
            self.assertEqual(self._reason(env), (None, "open_failed"))
            client.open_by_key.side_effect = None
            client.open_by_key.return_value = "SHEET"
            self.assertEqual(self._reason(env), ("SHEET", "ok"))


# ---------------------------------------------------------------------------
# One integrated bot: @CeliaFashionBot's webhook -> celia-telegram -> relay
# ---------------------------------------------------------------------------
from tests.test_celia_relay import FUNCTION, TOKEN, FakeEdge  # noqa: E402


class TestIntegratedBotCycle(unittest.TestCase):
    """mode_run reads the relay queue; a REAL vote is applied by the REAL gate."""

    def _cycle(self, edge, *, flag="0", build=None):
        from tests import test_approval_bot as tab

        sheet = tab.FakeSheet()
        tab.seed_approval(sheet.worksheet("pa_approvals"), approval_id="A1")
        original = tab.FakeTransport()
        built = {}

        def approval_bot(creds, transport=None):
            bot = ab.ApprovalBot.__new__(ab.ApprovalBot)
            bot._sent_cards, bot._offset, bot.text_handler = set(), None, None
            bot.transport = transport
            bot.gate = ab.ApprovalGate(sheet, transport, allowed_senders=[OWNER],
                                       now_fn=lambda: tab.NOW)
            built["transport"] = transport
            return bot

        fake_module = types.SimpleNamespace(ApprovalBot=approval_bot,
                                            TelegramTransport=lambda: original)
        client = ci.celia_relay.RelayClient(FUNCTION, TOKEN, edge.post)
        env = {ci.ASSISTANT_FLAG: flag, "PA_APPROVAL_CHAT_ID": OWNER_CHAT,
               "PA_APPROVAL_ALLOWED_SENDERS": OWNER}
        with tempfile.TemporaryDirectory() as tmp, clean_env(), \
                mock.patch.dict(os.environ, env), \
                mock.patch.object(ci, "_import_bot_module", return_value=fake_module), \
                mock.patch.object(ci, "build_sheet_credentials", return_value=object()), \
                mock.patch.object(ci, "discover_relay", return_value=client), \
                mock.patch.object(ci, "build_assistant_reply_fn", build or mock.Mock()):
            path = Path(tmp) / "state.json"
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc = ci.mode_run(path)
            return rc, ci.load_state(path), out.getvalue(), sheet, original, built

    def test_real_button_press_arrives_through_the_relay_and_is_applied(self):
        edge = FakeEdge()
        edge.queue = {41: {"update_id": 41, "callback_query": {
            "id": "q41", "data": "appr:A1:grant", "from": {"id": int(OWNER)},
            "message": {"message_id": 3, "chat": {"id": int(OWNER_CHAT)}}}}}
        rc, state, out, sheet, original, built = self._cycle(edge)
        self.assertEqual(rc, 0)
        self.assertIn("stage=transport updates=relay", out)
        self.assertIn("granted=1", out)
        from tests import test_approval_bot as tab
        self.assertEqual(sheet.worksheet("pa_approvals").rows[1][tab.ab.COL_STATUS], "granted")
        self.assertEqual(original.answers[0]["id"], "q41", "answered through the same bot")
        self.assertEqual(state["offset"], 42)
        self.assertEqual(edge.owners, [OWNER], "allowlist pushed to the function")

    def test_next_cycle_confirms_the_offset_so_nothing_is_applied_twice(self):
        edge = FakeEdge()
        edge.queue = {41: {"update_id": 41, "callback_query": {
            "id": "q41", "data": "appr:A1:grant", "from": {"id": int(OWNER)}}}}
        self._cycle(edge)
        # The runner sent offset=None on its first fetch (fresh state) — the
        # next cycle restores offset 42, which confirms (deletes) update 41.
        client = ci.celia_relay.RelayClient(FUNCTION, TOKEN, edge.post)
        client.fetch_updates(offset=42)
        self.assertEqual(edge.queue, {})

    def test_assistant_receives_the_relay_for_one_memory(self):
        kwargs = {}

        def build(history, **kw):
            kwargs.update(kw)
            return (lambda u: "replied"), (lambda: [])

        self._cycle(FakeEdge(), flag="1", build=build)
        self.assertIsNotNone(kwargs.get("relay"))

    def test_without_a_celia_webhook_the_historical_transport_is_used(self):
        fake_module = types.SimpleNamespace(TelegramTransport=lambda: "ORIGINAL")
        self.assertEqual(ci.updates_transport(fake_module, None), "ORIGINAL")


class TestRollbackDrainsTheQueue(unittest.TestCase):
    """CELIA_WEBHOOK=off + /setup with a press still queued: drain, then poll."""

    def _cycle(self, edge, state, *, polled=(), now=1_800_000_000.0):
        from tests import test_approval_bot as tab

        sheet = tab.FakeSheet()
        tab.seed_approval(sheet.worksheet("pa_approvals"), approval_id="A1")
        original = tab.FakeTransport()
        original.updates = list(polled)
        original.polls = []
        fetch = original.fetch_updates

        def recording_fetch(offset=None, timeout=0):
            original.polls.append(offset)
            return fetch(offset=offset, timeout=timeout)

        original.fetch_updates = recording_fetch

        def approval_bot(creds, transport=None):
            bot = ab.ApprovalBot.__new__(ab.ApprovalBot)
            bot._sent_cards, bot._offset, bot.text_handler = set(), None, None
            bot.transport = transport
            bot.gate = ab.ApprovalGate(sheet, transport, allowed_senders=[OWNER],
                                       now_fn=lambda: tab.NOW)
            return bot

        fake_module = types.SimpleNamespace(ApprovalBot=approval_bot,
                                            TelegramTransport=lambda: original)
        real_client = ci.celia_relay.RelayClient

        env = {"PA_APPROVAL_CHAT_ID": OWNER_CHAT, "PA_APPROVAL_ALLOWED_SENDERS": OWNER,
               "PA_APPROVAL_BOT_TOKEN": TOKEN}
        with tempfile.TemporaryDirectory() as tmp, clean_env(), \
                mock.patch.dict(os.environ, env), \
                mock.patch.object(ci, "_import_bot_module", return_value=fake_module), \
                mock.patch.object(ci, "build_sheet_credentials", return_value=object()), \
                mock.patch.object(ci, "discover_relay", return_value=None), \
                mock.patch.object(ci.time, "time", return_value=now), \
                mock.patch.object(ci.celia_relay, "RelayClient",
                                  lambda base, token, post=None: real_client(base, token, edge.post)):
            path = Path(tmp) / "state.json"
            ci.save_state(path, state)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc = ci.mode_run(path)
            return rc, ci.load_state(path), out.getvalue(), sheet, original

    def _press(self, uid, verb):
        return {"update_id": uid, "callback_query": {
            "id": "q%d" % uid, "data": "appr:A1:%s" % verb, "from": {"id": int(OWNER)}}}

    def test_a_queued_press_is_applied_before_polling_resumes(self):
        from tests import test_approval_bot as tab
        edge = FakeEdge()
        edge.queue = {41: self._press(41, "deny")}
        # A LATER press arrived via getUpdates after the rollback.
        later = [self._press(42, "grant")]
        rc, state, out, sheet, original = self._cycle(
            edge, {"sent": [], "offset": 41, "relay": FUNCTION}, polled=later)
        self.assertEqual(rc, 0)
        self.assertIn("stage=transport updates=drain", out)
        self.assertIn("stage=drain applied=1 done=false", out)
        self.assertEqual(original.polls, [], "Telegram not polled while the queue held a press")
        self.assertEqual(sheet.worksheet("pa_approvals").rows[1][tab.ab.COL_STATUS], "denied")
        self.assertEqual(state["offset"], 42)
        self.assertEqual(state["relay"], FUNCTION, "still draining until the queue answers empty")

    def test_once_the_queue_is_empty_polling_resumes_and_the_relay_is_forgotten(self):
        edge = FakeEdge()
        edge.queue = {41: self._press(41, "deny")}  # already applied (offset 42)
        rc, state, out, _, original = self._cycle(
            edge, {"sent": [], "offset": 42, "relay": FUNCTION, "relay_lost_at": 1_800_000_000.0},
            polled=[self._press(42, "grant")])
        self.assertEqual(rc, 0)
        self.assertEqual(edge.queue, {}, "confirmed on the way out")
        self.assertEqual(original.polls, [42], "same offset, same sequence")
        self.assertIn("stage=drain applied=0 done=true", out)
        self.assertNotIn("relay", state)
        self.assertNotIn("relay_lost_at", state)

    def test_an_unreadable_queue_blocks_polling_and_keeps_the_offset(self):
        edge = FakeEdge()
        edge.down = True
        rc, state, out, _, original = self._cycle(
            edge, {"sent": [], "offset": 41, "relay": FUNCTION}, polled=[self._press(42, "grant")])
        self.assertEqual(rc, 1)
        self.assertIn("stage=drain blocked=true reason=OSError", out)
        self.assertEqual(original.polls, [])
        self.assertEqual(state["offset"], 41)
        self.assertEqual(state["relay"], FUNCTION)
        self.assertEqual(state["relay_lost_at"], 1_800_000_000.0, "deadline clock persisted")

    def test_past_the_deadline_polling_resumes_with_a_warning(self):
        edge = FakeEdge()
        edge.down = True
        start = 1_800_000_000.0
        rc, state, out, _, original = self._cycle(
            edge, {"sent": [], "offset": 41, "relay": FUNCTION, "relay_lost_at": start},
            polled=[], now=start + ci.celia_relay.DRAIN_DEADLINE_SECONDS + 60)
        self.assertEqual(rc, 0)
        self.assertIn("stage=transport updates=abandoned", out)
        self.assertIn("::warning title=relay-abandoned::", out)
        self.assertEqual(original.polls, [41])
        self.assertEqual(edge.calls, [], "the dead queue is not touched again")
        self.assertNotIn("relay", state)

    def test_the_live_relay_is_remembered_for_a_future_rollback(self):
        path_state = {}
        rc, state, out, *_ = TestIntegratedBotCycle()._cycle(FakeEdge())
        path_state.update(state)
        self.assertEqual(rc, 0)
        self.assertEqual(path_state.get("relay"), FUNCTION)


class TestCrashMidCycleIsAtLeastOnce(unittest.TestCase):
    """Ack point, pinned: a queue row is deleted only by a LATER fetch whose
    offset was saved by a cycle that finished. A cycle that applies 3 of 5
    presses and then fails saves the offset from BEFORE all five; the next
    cycle re-reads all five, the first three come back ``not_pending``
    (first-wins) and only 4 and 5 are applied. Same rule on the live relay
    and on the post-rollback drain."""

    IDS = ("A1", "A2", "A3", "A4", "A5")

    def _cycle(self, edge, sheet, state, *, live, crash_on=None):
        from tests import test_approval_bot as tab

        original = tab.FakeTransport()
        original.polls = []
        poll = original.fetch_updates

        def recording_poll(offset=None, timeout=0):
            original.polls.append(offset)
            return poll(offset=offset, timeout=timeout)

        original.fetch_updates = recording_poll

        def approval_bot(creds, transport=None):
            bot = ab.ApprovalBot.__new__(ab.ApprovalBot)
            bot._sent_cards, bot._offset, bot.text_handler = set(), None, None
            bot.transport = transport
            bot.gate = ab.ApprovalGate(sheet, transport, allowed_senders=[OWNER],
                                       now_fn=lambda: tab.NOW)
            real = bot.gate.handle_callback

            def handle(query):
                if query.get("id") == crash_on:
                    raise RuntimeError("sheet write failed")  # process dies mid-batch
                return real(query)

            bot.gate.handle_callback = handle
            return bot

        fake_module = types.SimpleNamespace(ApprovalBot=approval_bot,
                                            TelegramTransport=lambda: original)
        real_client = ci.celia_relay.RelayClient
        live_client = real_client(FUNCTION, TOKEN, edge.post) if live else None
        env = {"PA_APPROVAL_CHAT_ID": OWNER_CHAT, "PA_APPROVAL_ALLOWED_SENDERS": OWNER,
               "PA_APPROVAL_BOT_TOKEN": TOKEN}
        with tempfile.TemporaryDirectory() as tmp, clean_env(), \
                mock.patch.dict(os.environ, env), \
                mock.patch.object(ci, "_import_bot_module", return_value=fake_module), \
                mock.patch.object(ci, "build_sheet_credentials", return_value=object()), \
                mock.patch.object(ci, "discover_relay", return_value=live_client), \
                mock.patch.object(ci.time, "time", return_value=1_800_000_000.0), \
                mock.patch.object(ci.celia_relay, "RelayClient",
                                  lambda base, token, post=None: real_client(base, token, edge.post)):
            path = Path(tmp) / "state.json"
            ci.save_state(path, state)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc = ci.mode_run(path)
            return rc, ci.load_state(path), out.getvalue(), original

    def _scenario(self, live):
        from tests import test_approval_bot as tab

        sheet = tab.FakeSheet()
        ws = sheet.worksheet("pa_approvals")
        for aid in self.IDS:
            tab.seed_approval(ws, approval_id=aid)
        writes = []
        record = ws.batch_update

        def counting(data):
            writes.extend(item["range"] for item in data)
            return record(data)

        ws.batch_update = counting
        edge = FakeEdge()
        for n, aid in enumerate(self.IDS, start=1):  # updates 101..105 grant A1..A5
            uid = 100 + n
            edge.queue[uid] = {"update_id": uid, "callback_query": {
                "id": "q%d" % n, "data": "appr:%s:grant" % aid, "from": {"id": int(OWNER)}}}
        start = {"sent": list(self.IDS), "offset": 101}
        if not live:
            start["relay"] = FUNCTION  # rolled back: the remembered queue is drained

        # Cycle 1: presses 1-3 applied, the 4th crashes the process.
        rc, state, out, original = self._cycle(edge, sheet, start, live=live, crash_on="q4")
        self.assertEqual(rc, 1)
        self.assertEqual(state["offset"], 101, "saved pointer sits BEFORE the batch")
        self.assertEqual(sorted(edge.queue), [101, 102, 103, 104, 105], "nothing deleted")
        self.assertEqual(original.polls, [], "Telegram never polled by the failed cycle")
        status = [ws.rows[i][tab.ab.COL_STATUS] for i in range(1, 6)]
        self.assertEqual(status, ["granted"] * 3 + ["pending"] * 2)

        # Cycle 2 (restart): all five re-read; 1-3 are no-ops, 4-5 applied.
        rc, state, out, original = self._cycle(edge, sheet, state, live=live)
        self.assertEqual(rc, 0, out)
        self.assertIn("callbacks_handled=5 granted=2", out)
        self.assertEqual(state["offset"], 106)
        status = [ws.rows[i][tab.ab.COL_STATUS] for i in range(1, 6)]
        self.assertEqual(status, ["granted"] * 5)
        for row_no in range(2, 7):  # sheet rows A2..A6 hold A1..A5
            touched = [r for r in writes if r.startswith("A%d:" % row_no)]
            self.assertEqual(len(touched), 1, "approval row %d written exactly once" % row_no)
        self.assertEqual([ws.rows[i][tab.ab.COL_VERSION] for i in range(1, 6)], ["2"] * 5)

        # Cycle 3: the saved pointer now confirms (deletes) the batch.
        rc, state, out, _ = self._cycle(edge, sheet, state, live=live)
        self.assertEqual((rc, edge.queue), (0, {}))
        return out

    def test_live_relay_crash_after_three_of_five(self):
        self._scenario(live=True)

    def test_drain_crash_after_three_of_five(self):
        out = self._scenario(live=False)
        self.assertIn("stage=drain applied=0 done=true", out)


class TestCeliaMemoryInSupabase(TestRealAssistantOverTelegram):
    """Integrated bot: CELIA's memory is the Supabase table; the sheet is never written."""

    def _cycle(self, edge, sheet, texts):
        free = self._free("تمام.")
        ca, posts, patches = self._patches([free])
        client = ci.celia_relay.RelayClient(FUNCTION, TOKEN, edge.post)
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                reply_fn, _ = ci.build_assistant_reply_fn(None, sheet=sheet, memory_rows=[], relay=client)
                stages = [reply_fn(_text_update(i + 1, t)) for i, t in enumerate(texts)]
        return stages, [p["text"] for _, p in posts], out.getvalue()

    def test_remember_goes_to_supabase_not_the_sheet(self):
        edge, sheet = FakeEdge(), _FakeSheet()
        stages, sent, out = self._cycle(edge, sheet, ["تذكّري: أفضّل الردود القصيرة"])
        self.assertEqual(stages, ["memory_saved"])
        self.assertIn("memory=supabase", out)
        self.assertEqual(sheet.tabs, {}, "the Google Sheet is read-only legacy now")
        self.assertEqual(edge.memory[0]["text"], "أفضّل الردود القصيرة")
        # What CELIA's instant path saved (same table) is recalled by the runner.
        edge.memory.append({"id": 2, "kind": "project", "text": "قرار محفوظ من سيليا الفورية",
                            "source": "telegram:9", "created_at": "2026-11-28T12:00:00+00:00",
                            "confidence": 1})
        stages, sent, _ = self._cycle(edge, sheet, ["ماذا تتذكرين؟"])
        self.assertIn("قرار محفوظ من سيليا الفورية", sent[0])

    def test_supabase_down_means_no_memory_never_a_sheet_write(self):
        edge, sheet = FakeEdge(), _FakeSheet()
        edge.down = True
        stages, sent, out = self._cycle(edge, sheet, ["تذكّري: شيء"])
        self.assertEqual(stages, ["memory_unavailable"])
        self.assertEqual(sheet.tabs, {})
        self.assertIn("memory=none", out)

    def test_legacy_sheet_rows_are_copied_once_and_the_sheet_is_never_written(self):
        edge, sheet = FakeEdge(), _FakeSheet()
        tab = sheet.add_worksheet(ci.CELIA_MEMORY_TAB, rows=10, cols=6)
        tab.values = [list(TestSheetMemoryBackend.HEADERS),
                      ["1", "project", "قرار قديم من الورقة", "telegram:1", "2026-11-20T10:00:00Z", "1"]]
        before = [list(r) for r in tab.values]
        stages, sent, out = self._cycle(edge, sheet, ["ماذا تتذكرين؟"])
        self.assertIn("imported_legacy_rows=1", out)
        self.assertIn("قرار قديم من الورقة", sent[0])
        self.assertEqual(edge.memory[0]["text"], "قرار قديم من الورقة")
        stages, sent, out = self._cycle(edge, sheet, ["تذكّري: معلومة جديدة"])
        self.assertNotIn("imported_legacy_rows", out, "never copied twice")
        self.assertEqual(tab.values, before, "the legacy tab is read-only")
        self.assertEqual(len(edge.memory), 2)
