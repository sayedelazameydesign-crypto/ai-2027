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
import tempfile
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

    def test_welcome_replies_to_plain_text_and_skips_non_messages(self):
        updates = [
            {"update_id": 9,
             "message": {"chat": {"id": 42}, "text": "مرحبا"}},
            {"update_id": 10, "callback_query": {"id": "cb"}},  # no chat
        ]
        rc, fake, out = self._run_welcome(updates)
        self.assertEqual(rc, 0)
        self.assertEqual(len(fake.sent), 1)
        self.assertEqual(fake.sent[0][1], "42")
        self.assertEqual(fake.offsets, [11])
        self.assertIn("received=2 replied=1", out)

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

        def build(history):
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

    def test_assistant_build_failure_keeps_approvals_running(self):
        def build(history):
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

            def is_configured(self):
                return True

            async def complete(self, request, *, timeout_s):
                return text

        return P()

    def test_cycle_reply_fn_answers_in_the_sender_chat_with_history(self):
        _, posts, patches = self._patches([self._free("أهلاً بك!")])
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
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    rc = ci.mode_assistant()
                self.assertEqual(rc, expected_rc, out.getvalue())
                sent = posts[0][1]["text"]
                self.assertEqual(sent, expected_text or ca.ARABIC_NO_PROVIDER_FALLBACK)
                self.assertIn("stage=assistant-probe", out.getvalue())


class TestRedactionCoversModelKeys(unittest.TestCase):
    def test_free_tier_model_keys_are_masked(self):
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "plainvalue-groq-123456",
                                          "OPENROUTER_API_KEY": "or-plain-987654321"}):
            out = ci.redact("failed with plainvalue-groq-123456 and or-plain-987654321")
        self.assertNotIn("plainvalue-groq-123456", out)
        self.assertNotIn("or-plain-987654321", out)
