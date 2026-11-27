#!/usr/bin/env python3
"""Hermetic tests for the P1 proactive watcher (stdlib only).

No network, no credentials, no third-party packages. The watcher imports
``gspread`` / ``googleapiclient`` at module scope because those are runtime
dependencies of the Google connector layer — deliberately *outside* the
zero-dependency kernel guarded by ``scripts/ci_guard.py imports`` (ADR-0002).
To keep this suite runnable on a bare interpreter, missing connector modules
are stubbed in ``sys.modules`` before the watcher is loaded from its file path.

What these tests actually prove (evidence for the P1 self-audit claims):

- column-count contracts: pa_source_records = 23 (A:W), pa_tasks = 22 (A:V),
  pa_audit_logs_202611 = 25 (A:Y);
- every externally ingested row is ``tainted`` with ``consent = not_granted``;
- extracted tasks are ``epistemic = inferred`` with a non-empty dedupe_key;
- audit rows chain ``prev_hash -> self_hash`` across cycles;
- ssGateway redaction fires on sensitive patterns and caps the excerpt;
- dedupe by external_id prevents re-ingesting the same Gmail/calendar item.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "watchers" / "proactive_watcher.py"


def _ensure_module(name: str, **attrs: object) -> None:
    """Import ``name`` if it exists; otherwise install a stub with ``attrs``."""
    try:
        __import__(name)
        return
    except ImportError:
        pass
    parts = name.split(".")
    for i in range(1, len(parts) + 1):
        pkg = ".".join(parts[:i])
        if pkg not in sys.modules:
            sys.modules[pkg] = types.ModuleType(pkg)
        if i > 1:
            parent = sys.modules[".".join(parts[: i - 1])]
            setattr(parent, parts[i - 1], sys.modules[pkg])
    for key, value in attrs.items():
        setattr(sys.modules[name], key, value)


def _load_watcher_module():
    _ensure_module("gspread", authorize=lambda creds: None)
    _ensure_module("google.oauth2.credentials", Credentials=object)
    _ensure_module("googleapiclient.discovery", build=lambda *a, **k: None)
    spec = importlib.util.spec_from_file_location("proactive_watcher", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pw = _load_watcher_module()


# ---------------------------------------------------------------------------
# Fakes — in-memory stand-ins for gspread worksheets and Google API clients.
# ---------------------------------------------------------------------------
class FakeWorksheet:
    def __init__(self, width: int) -> None:
        self.rows: list[list] = [["col_%d" % i for i in range(width)]]
        self.raw_option_calls: list = []

    def get_all_values(self) -> list[list]:
        return [list(r) for r in self.rows]

    def append_row(self, row: list, value_input_option: str = "USER_ENTERED") -> None:
        self.raw_option_calls.append(value_input_option)
        self.rows.append(list(row))


class FakeSheet:
    def __init__(self) -> None:
        # 23 (A:W) / 22 (A:V) / 25 (A:Y) — the published column contracts.
        self.worksheets = {
            "pa_source_records": FakeWorksheet(23),
            "pa_tasks": FakeWorksheet(22),
            "pa_audit_logs_202611": FakeWorksheet(25),
        }

    def worksheet(self, name: str) -> FakeWorksheet:
        return self.worksheets[name]


class _Chain:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def execute(self) -> dict:
        return self._payload


class FakeGmail:
    """Serves ``users().messages().list()`` and ``.get()`` from fixed data."""

    def __init__(self, messages: list[dict]) -> None:
        self._messages = messages

    def users(self) -> "FakeGmail":
        return self

    def messages(self) -> "FakeGmail":
        return self

    def list(self, userId: str, q: str, maxResults: int) -> _Chain:
        return _Chain({"messages": [{"id": m["id"]} for m in self._messages]})

    def get(self, userId: str, id: str, format: str, metadataHeaders: list) -> _Chain:
        # Gmail returns the full message envelope: {payload: {headers: [...]},
        # snippet, threadId} — exactly what gmail_message() builds.
        for m in self._messages:
            if m["id"] == id:
                return _Chain(m)
        raise AssertionError("unknown message id %r" % id)


class FakeCalendar:
    def __init__(self, items: list[dict]) -> None:
        self._items = items

    def events(self) -> "FakeCalendar":
        return self

    def list(self, calendarId: str, timeMin: str, timeMax: str,
             singleEvents: bool, orderBy: str) -> _Chain:
        return _Chain({"items": self._items})


def make_watcher(gmail_messages: list[dict] | None = None,
                 calendar_items: list[dict] | None = None):
    watcher = pw.ProactiveWatcher.__new__(pw.ProactiveWatcher)
    watcher.sheet = FakeSheet()
    watcher.gmail = FakeGmail(gmail_messages or [])
    watcher.calendar = FakeCalendar(calendar_items or [])
    return watcher


def gmail_message(msg_id: str, subject: str, snippet: str = "") -> dict:
    return {
        "id": msg_id,
        "payload": {
            "headers": [
                {"name": "From", "value": "sender@example.com"},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Wed, 26 Nov 2026 09:00:00 +0000"},
            ]
        },
        "snippet": snippet,
        "threadId": "thread-" + msg_id,
    }


def calendar_event(ev_id: str, summary: str) -> dict:
    return {
        "id": ev_id,
        "summary": summary,
        "start": {"dateTime": "2026-11-27T10:00:00+02:00"},
        "organizer": {"email": "organizer@example.com"},
        "htmlLink": "https://calendar.google.com/event?eid=" + ev_id,
    }


class TestSanitization(unittest.TestCase):
    """ssGateway local redaction rules."""

    def test_egyptian_national_id_is_redacted(self) -> None:
        excerpt, sensitivity = pw.sanitize_and_redact("رقمي القومي 29901010123456 مسجّل")
        self.assertEqual(sensitivity, "restricted")
        self.assertNotIn("29901010123456", excerpt)
        self.assertIn("[REDACTED_SENSITIVE]", excerpt)

    def test_password_assignment_is_redacted(self) -> None:
        excerpt, sensitivity = pw.sanitize_and_redact("login password: hunter2secret")
        self.assertEqual(sensitivity, "restricted")
        self.assertNotIn("hunter2secret", excerpt)

    def test_clean_text_stays_personal_and_capped(self) -> None:
        excerpt, sensitivity = pw.sanitize_and_redact("meeting about the roadmap " * 50)
        self.assertEqual(sensitivity, "personal")
        self.assertLessEqual(len(excerpt), 280)

    def test_empty_text(self) -> None:
        self.assertEqual(pw.sanitize_and_redact(""), ("", "personal"))


class TestIdentifiers(unittest.TestCase):
    def test_ulid_like_is_26_chars(self) -> None:
        value = pw.generate_ulid_like()
        self.assertEqual(len(value), 26)
        self.assertTrue(value.startswith("01"))

    def test_sha256_is_deterministic_and_key_sorted(self) -> None:
        a = pw.compute_sha256({"b": 1, "a": 2})
        b = pw.compute_sha256({"a": 2, "b": 1})
        self.assertEqual(a, b)
        self.assertEqual(len(a), 64)


class TestCycleContracts(unittest.TestCase):
    """The full run_cycle() against fakes — column and taint contracts."""

    def test_run_cycle_column_counts_and_taint_flags(self) -> None:
        watcher = make_watcher(
            gmail_messages=[gmail_message("m1", "عاجل: فاتورة شهرية مستحقة")],
            calendar_items=[calendar_event("ev1", "موعد تسليم الفاتورة")],
        )
        report = watcher.run_cycle()

        self.assertEqual(report, {
            "gmail_ingested": 1,
            "calendar_ingested": 1,
            "tasks_created": 2,
        })

        src = watcher.sheet.worksheet("pa_source_records")
        self.assertEqual(len(src.rows), 3)  # header + gmail + calendar
        for row in src.rows[1:]:
            self.assertEqual(len(row), 23, "pa_source_records must be A:W")
            self.assertEqual(row[12], "tainted", "M: external input must be tainted")
            self.assertEqual(row[8], "not_granted", "I: consent must not be granted")
            self.assertEqual(row[6], "fact")
            self.assertTrue(row[15], "P: external_id must be set")
            self.assertTrue(row[13], "N: checksum must be set")

        tasks = watcher.sheet.worksheet("pa_tasks")
        self.assertEqual(len(tasks.rows), 3)  # header + 2 tasks
        for row in tasks.rows[1:]:
            self.assertEqual(len(row), 22, "pa_tasks must be A:V")
            self.assertEqual(row[6], "inferred", "G: epistemic must be inferred")
            self.assertEqual(row[8], "not_granted", "I: consent must not be granted")
            self.assertTrue(row[21], "V: dedupe_key must be set")

        audit = watcher.sheet.worksheet("pa_audit_logs_202611")
        self.assertEqual(len(audit.rows), 2)  # header + 1 cycle log
        log = audit.rows[1]
        self.assertEqual(len(log), 25, "pa_audit_logs_202611 must be A:Y")
        self.assertEqual(log[16], "proactive_poll_cycle")
        self.assertEqual(log[21], "P1-WATCHER-READONLY")

        # Every write must be RAW, never USER_ENTERED (formula injection guard).
        for ws in watcher.sheet.worksheets.values():
            self.assertTrue(all(opt == "RAW" for opt in ws.raw_option_calls))

    def test_non_actionable_mail_creates_no_task(self) -> None:
        watcher = make_watcher(
            gmail_messages=[gmail_message("m2", "newsletter: weekly digest")],
        )
        report = watcher.run_cycle()
        self.assertEqual(report["gmail_ingested"], 1)
        self.assertEqual(report["tasks_created"], 0)
        self.assertEqual(len(watcher.sheet.worksheet("pa_tasks").rows), 1)


class TestDeduplication(unittest.TestCase):
    def test_same_external_id_is_not_reingested(self) -> None:
        messages = [gmail_message("m1", "عاجل: موعد مهم")]
        events = [calendar_event("ev1", "deadline للفاتورة")]
        watcher = make_watcher(messages, events)

        first = watcher.run_cycle()
        self.assertEqual(first["gmail_ingested"], 1)
        self.assertEqual(first["calendar_ingested"], 1)

        second = watcher.run_cycle()
        self.assertEqual(second["gmail_ingested"], 0, "dedupe by external_id (P)")
        self.assertEqual(second["calendar_ingested"], 0, "dedupe by external_id (P)")
        self.assertEqual(second["tasks_created"], 0, "dedupe by dedupe_key (V)")

        src = watcher.sheet.worksheet("pa_source_records")
        self.assertEqual(len(src.rows), 3, "no duplicate source rows")
        tasks = watcher.sheet.worksheet("pa_tasks")
        # header + 2 tasks from cycle 1; cycle 2 must add none.
        self.assertEqual(len(tasks.rows), 3, "no duplicate tasks")


class TestAuditHashChain(unittest.TestCase):
    def test_prev_hash_links_to_previous_self_hash(self) -> None:
        watcher = make_watcher(
            gmail_messages=[gmail_message("m1", "عاجل: فاتورة")],
        )
        watcher.run_cycle()
        watcher.run_cycle()

        audit = watcher.sheet.worksheet("pa_audit_logs_202611")
        rows = audit.rows[1:]
        self.assertEqual(len(rows), 2)
        # Genesis for an empty sheet.
        self.assertTrue(rows[0][23].startswith("GENESIS"))
        # Second cycle must chain onto the first cycle's self_hash (X -> Y).
        self.assertEqual(rows[1][23], rows[0][24])
        self.assertTrue(rows[1][24])
        # self_hash must differ from prev_hash (chain is not degenerate).
        self.assertNotEqual(rows[1][23], rows[1][24])


if __name__ == "__main__":
    unittest.main()
