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
    "GOOGLE_SERVICE_ACCOUNT", "BOT_STATE_FILE",
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
