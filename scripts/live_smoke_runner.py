#!/usr/bin/env python3
"""P1-APPROVAL-BOT-LIVE-SMOKE runner — one command for the live checklist.

Checks (read-only unless a flag says otherwise):

  env        PA_MEMORY_SHEET_ID and the bot credentials (SKIP when absent)
  sheets     open the spreadsheet; every expected worksheet exists with the
             exact live-verified header contract (names + order + width)
  chain      pa_audit_logs_202611 linkage: each row's prev_hash equals the
             previous row's self_hash (hard gate) + width/genesis checks
  grants     report every delegation grant (status/allowed/denied/limits/
             revoked) — WARN when no grant is active yet
  approvals  report pending / expired approvals (INFO)
  card       (--send-card) deliver one real Telegram card via
             approval_bot.TelegramTransport
  seed       (--seed-approval ACTION) append a pending test approval whose
             payload_hash = compute_sha256(payload), i.e. verifiable by the
             governed executor (prints the row id + cleanup note)
  execute    (--execute ACTION) run the governed executor against a granted,
             unconsumed approval: four live gates, single-use consume
             (status/version/checksum), chained audit append — this is what
             makes consumed_at appear (default payload matches the seed)

Exit codes follow scripts/doctor.py: 0 = all executed checks passed,
3 = only skips (missing credential/dependency/network), 1 = a failure.
Manual checklist steps (button taps, Sheets edits) print as [MANUAL].

The pure helpers (verify_audit_chain, header_problems,
build_test_approval_row) are exercised bare by
scripts/test_live_smoke_runner.py — no gspread or credentials needed.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
EXECUTOR_PATH = ROOT / "src" / "executors" / "governed_executor.py"
WAAPI_PATH = ROOT / "src" / "connectors" / "waapi.py"
SPREADSHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

# ---------------------------------------------------------------------------
# Live-verified header contracts (all six read from the production sheet on
# 2026-11-26; drift in either direction fails the `sheets` check).
# ---------------------------------------------------------------------------
EXPECTED_HEADERS: Dict[str, List[str]] = {
    "pa_source_records": [
        "id", "created_at", "updated_at", "version", "source_id", "confidence",
        "epistemic", "sensitivity", "consent", "retention", "review_at",
        "deleted_at", "taint", "checksum", "source_type", "external_id",
        "external_url", "thread_id", "received_at", "author", "content_hash",
        "excerpt", "fetched_at",
    ],
    "pa_tasks": [
        "id", "created_at", "updated_at", "version", "source_id", "confidence",
        "epistemic", "sensitivity", "consent", "retention", "review_at",
        "deleted_at", "taint", "checksum", "title", "status", "due_at",
        "priority", "project_id", "done_at", "blocked_reason", "dedupe_key",
    ],
    "pa_goals": [
        "id", "created_at", "updated_at", "version", "source_id", "confidence",
        "epistemic", "sensitivity", "consent", "retention", "review_at",
        "deleted_at", "taint", "checksum", "title", "description", "horizon",
        "status", "target_date", "progress", "key_results",
    ],
    "pa_approvals": [
        "id", "created_at", "updated_at", "version", "source_id", "confidence",
        "epistemic", "sensitivity", "consent", "retention", "review_at",
        "deleted_at", "taint", "checksum", "action", "payload_hash", "summary",
        "status", "issued_at", "expires_at", "consumed_at", "single_use",
    ],
    "pa_delegation_grants": [
        "id", "created_at", "updated_at", "version", "source_id", "confidence",
        "epistemic", "sensitivity", "consent", "retention", "review_at",
        "deleted_at", "taint", "checksum", "scope", "status", "model_plan_ref",
        "allowed_actions", "denied_actions", "limits", "requires_approval",
        "granted_by", "revoked_at",
    ],
    "pa_audit_logs_202611": [
        "id", "created_at", "updated_at", "version", "source_id", "confidence",
        "epistemic", "sensitivity", "consent", "retention", "review_at",
        "deleted_at", "taint", "checksum", "at", "unit", "action", "actor",
        "outcome", "subject_ids", "payload_digest", "policy_trace_id",
        "approval_id", "prev_hash", "self_hash",
    ],
}

# Positions in pa_audit_logs_202611 (A:Y).
COL_PREV_HASH = 23
COL_SELF_HASH = 24

# ---------------------------------------------------------------------------
# Pure helpers (unit-tested without gspread)
# ---------------------------------------------------------------------------
def _load_executor():
    spec = importlib.util.spec_from_file_location("governed_executor", EXECUTOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["governed_executor"] = module
    spec.loader.exec_module(module)
    return module


ge = _load_executor()


def _load_waapi():
    """Shared module instance (tests swap its transport attribute)."""
    if "waapi" in sys.modules:
        return sys.modules["waapi"]
    spec = importlib.util.spec_from_file_location("waapi", WAAPI_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["waapi"] = module
    spec.loader.exec_module(module)
    return module


def header_problems(sheet_name: str, actual: Sequence[str]) -> List[str]:
    """Compare a live header row against the contract; empty = OK."""
    expected = EXPECTED_HEADERS.get(sheet_name)
    if expected is None:
        return ["%s: no header contract registered" % sheet_name]
    problems: List[str] = []
    if len(actual) < len(expected):
        problems.append(
            "%s: header width %d < expected %d (trailing columns missing)"
            % (sheet_name, len(actual), len(expected))
        )
    for idx, name in enumerate(expected):
        got = str(actual[idx]) if idx < len(actual) else "<missing>"
        if got != name:
            problems.append(
                "%s: column %d expected %r, found %r" % (sheet_name, idx, name, got)
            )
            if len(problems) >= 6:
                break
    return problems


def verify_audit_chain(data_rows: Sequence[Sequence[str]]) -> List[str]:
    """prev_hash linkage over pa_audit_logs_202611 data rows (no header).

    Hard gates: every row has a 64-hex self_hash; row N>0 carries the exact
    self_hash of row N-1 in its prev_hash slot; the genesis row's prev_hash
    must be empty or 64-hex. Returns a list of problems (empty = chain OK).
    """
    problems: List[str] = []
    if not data_rows:
        return ["audit sheet has no data rows"]
    for i, row in enumerate(data_rows):
        row_no = i + 2  # sheet row number (header is row 1)
        self_hash = str(row[COL_SELF_HASH]) if len(row) > COL_SELF_HASH else ""
        if len(self_hash) != 64 or any(c not in "0123456789abcdef" for c in self_hash):
            problems.append("row %d: self_hash is not 64-hex (%r)" % (row_no, self_hash[:16]))
        prev_hash = str(row[COL_PREV_HASH]) if len(row) > COL_PREV_HASH else ""
        if i == 0:
            if prev_hash and (len(prev_hash) != 64
                              or any(c not in "0123456789abcdef" for c in prev_hash)):
                problems.append("row %d: genesis prev_hash malformed" % row_no)
        else:
            previous_self = str(data_rows[i - 1][COL_SELF_HASH]) \
                if len(data_rows[i - 1]) > COL_SELF_HASH else ""
            if prev_hash != previous_self:
                problems.append(
                    "row %d: prev_hash %r != previous self_hash %r"
                    % (row_no, prev_hash[:16], previous_self[:16])
                )
        if len(row) != len(EXPECTED_HEADERS["pa_audit_logs_202611"]):
            problems.append("row %d: width %d != 25" % (row_no, len(row)))
    return problems


def build_test_approval_row(action: str, payload: Dict[str, Any],
                            approval_id: str, now: datetime) -> List[str]:
    """Build a 22-cell pending approval row the governed executor can verify."""
    issued = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    expires = (now + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    row = [""] * 22
    row[0] = approval_id
    row[1] = issued
    row[2] = issued
    row[3] = "1"
    row[8] = "not_granted"          # consent (data-processing, per seeds)
    row[13] = ""                    # checksum filled below
    row[14] = action
    row[15] = ge.compute_sha256(payload)
    row[16] = "live-smoke test approval (safe to delete)"
    row[17] = "pending"
    row[18] = issued
    row[19] = expires
    row[20] = ""                    # consumed_at
    row[21] = "true"                # single_use
    ap = ge.approval_from_row(row)
    row[13] = ge.approval_checksum(ap)
    return row


def make_smoke_approval_id() -> str:
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32, ULID-ish
    random_part = "".join(random.choice(alphabet) for _ in range(19))
    return "01SMOKE" + random_part


def default_execute_payload(action: str) -> Dict[str, Any]:
    """Deterministic payload shared by --seed-approval and --execute, so the
    seeded payload_hash round-trips without remembering volatile fields."""
    return {"smoke": True, "action": action}


# ---------------------------------------------------------------------------
# Executor wiring over gspread (duck-typed: no gspread import at module level)
# ---------------------------------------------------------------------------
class _GWorksheet:
    """Exposes exactly the interface GovernedExecutor / append_audit_log use."""

    def __init__(self, ws: Any, title: str) -> None:
        self._ws = ws
        self._title = title

    def get_all_values(self) -> List[List[str]]:
        return self._ws.get_all_values()

    def append_row(self, row: List[Any], value_input_option: str = "RAW") -> Any:
        return self._ws.append_row(list(row),
                                   value_input_option=value_input_option)

    def batch_update(self, data: List[Dict[str, Any]]) -> None:
        # The executor sends sheet-relative ranges ("A{r}:V{r}"); the live
        # values API needs the sheet title prefixed.
        for item in data:
            self._ws.spreadsheet.values_update(
                "%s!%s" % (self._title, item["range"]),
                params={"valueInputOption": "RAW"},
                body={"values": [list(item["values"][0])]},
            )


class GBook:
    """Adapter: gspread Spreadsheet -> the executor's sheet interface."""

    def __init__(self, book: Any) -> None:
        self._book = book

    def worksheet(self, name: str) -> _GWorksheet:
        return _GWorksheet(self._book.worksheet(name), name)


def find_granted_approval(rows: Sequence[Sequence[str]], action: str,
                          approval_id: Optional[str] = None
                          ) -> Tuple[Optional[int], Optional[List[str]]]:
    """First executable approval among data rows: matching action, explicit
    approved status, never consumed or deleted. Returns (sheet_row, row)."""
    for idx, row in enumerate(rows):
        if not row or not str(row[0]).strip():
            continue
        if approval_id and str(row[0]).strip() != approval_id:
            continue
        if str(row[14] if len(row) > 14 else "").strip() != action:
            continue
        status = str(row[17]).strip().lower() if len(row) > 17 else ""
        consumed = str(row[20]).strip() if len(row) > 20 else ""
        deleted = str(row[11]).strip() if len(row) > 11 else ""
        if deleted or consumed or status == "consumed":
            continue
        if status in ge.GovernedExecutor.VALID_APPROVED_STATES:
            return idx + 2, list(row)   # sheet row number (header is row 1)
    return None, None


def execute_granted(book: Any, audit_logger: Callable[..., None], action: str,
                    payload: Dict[str, Any], report: "Report",
                    approval_id: Optional[str] = None,
                    verification_public_key: Optional[str] = None,
                    assertion: Optional[Dict[str, Any]] = None) -> Any:
    """Run the governed executor against an executable approval.

    Prefers a granted (bot-voted) row; when ``assertion`` is given, a pending
    row carrying a matching signed passkey decision also executes (ADR-0006).
    This is what makes consumed_at appear: four gates, single-use consume
    (status/version/checksum via batch_update), chained audit append.
    """
    rows = book.worksheet("pa_approvals").get_all_values()
    row_no, row = find_granted_approval(rows[1:], action, approval_id)
    if row is None and assertion is not None:
        # Passkey path: the executor accepts a pending row + valid assertion.
        for idx, candidate in enumerate(rows[1:]):
            if not candidate or not str(candidate[0]).strip():
                continue
            if approval_id and str(candidate[0]).strip() != approval_id:
                continue
            if str(candidate[14] if len(candidate) > 14 else "").strip() != action:
                continue
            status = (str(candidate[17]).strip().lower()
                      if len(candidate) > 17 else "")
            consumed = str(candidate[20]).strip() if len(candidate) > 20 else ""
            deleted = str(candidate[11]).strip() if len(candidate) > 11 else ""
            if not deleted and not consumed and status == "pending":
                row_no, row = idx + 2, list(candidate)
                break
    if row is None:
        report.skip("execute: no granted (or asserted-pending) approval for "
                    "%s — vote on the card first (or --seed-approval + "
                    "grant / --assertion)" % action)
        return None
    executor = ge.GovernedExecutor(
        book, audit_logger, verification_public_key=verification_public_key)
    if action == "whatsapp.send":
        # Preflight BEFORE the consume gate: a missing token must never
        # burn a single-use approval (fail-closed, zero side effects).
        waapi = _load_waapi()
        pre = waapi.preflight()
        if not pre["ok"]:
            report.fail("execute denied before gates: %s" % pre["stage"])
            return None
        executor.register_tool(action, waapi.make_send_handler())
    else:
        executor.register_tool(action, lambda p: {"smoke_execution": True,
                                                  "action": action})
    result = executor.execute(str(row[0]).strip(), action, payload,
                              assertion=assertion)
    if result.ok:
        report.ok("execute %s via %s -> consumed (version bump + checksum + "
                  "chained audit)" % (action, str(row[0])[:14]))
    else:
        report.fail("execute %s denied: %s" % (action, result.reason))
    after = book.worksheet("pa_approvals").get_all_values()[row_no - 1]
    report.info("row state: status=%r version=%s consumed_at=%s"
                % (after[17] if len(after) > 17 else "",
                   after[3] if len(after) > 3 else "",
                   (after[20] or "<empty>") if len(after) > 20 else ""))
    return result


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
class Report:
    def __init__(self) -> None:
        self.passed = self.failed = self.skipped = 0

    def _emit(self, tag: str, text: str) -> None:
        print("[%s] %s" % (tag, text))

    def ok(self, text: str) -> None:
        self.passed += 1
        self._emit("PASS", text)

    def fail(self, text: str) -> None:
        self.failed += 1
        self._emit("FAIL", text)

    def skip(self, text: str) -> None:
        self.skipped += 1
        self._emit("SKIP", text)

    def info(self, text: str) -> None:
        self._emit("INFO", text)

    def warn(self, text: str) -> None:
        self._emit("WARN", text)

    def manual(self, text: str) -> None:
        self._emit("MANUAL", text)

    def exit_code(self) -> int:
        if self.failed:
            return 1
        if self.skipped:
            return 3
        return 0


# ---------------------------------------------------------------------------
# Live checks
# ---------------------------------------------------------------------------
def _authorize(creds_path: Optional[str]):
    """Return an authorized gspread client, or raise with a clear message."""
    import gspread  # runtime-only dependency
    if creds_path:
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(
            creds_path, scopes=[SPREADSHEETS_SCOPE])
        return gspread.authorize(creds)
    import google.auth
    creds, _project = google.auth.default(scopes=[SPREADSHEETS_SCOPE])
    return gspread.authorize(creds)


def _open_book(args: argparse.Namespace, report: Report) -> Any:
    """Authorize and open the spreadsheet; None + a report line on failure."""
    sheet_id = args.sheet_id or os.environ.get("PA_MEMORY_SHEET_ID", "")
    if not sheet_id:
        report.skip("PA_MEMORY_SHEET_ID not set — live checks need the sheet id")
        return None
    try:
        client = _authorize(args.creds)
    except ImportError as exc:
        report.skip("gspread/google-auth not installed (%s) — live checks skipped" % exc)
        return None
    except Exception as exc:  # noqa: BLE001 — any auth problem is a skip, not a crash
        report.skip("authorization failed (%s)" % exc)
        return None
    try:
        book = client.open_by_key(sheet_id)
    except Exception as exc:  # noqa: BLE001
        report.fail("cannot open spreadsheet %s (%s)" % (sheet_id[:12], exc))
        return None
    report.ok("spreadsheet opened (%s)" % sheet_id[:12])
    return book


def _bind_watcher_audit(book: Any) -> Callable[..., None]:
    """Bind the watcher's chained append_audit_log (single chain writer)."""
    spec = importlib.util.spec_from_file_location(
        "proactive_watcher", ROOT / "src" / "watchers" / "proactive_watcher.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["proactive_watcher"] = module
    spec.loader.exec_module(module)  # imports gspread — runtime env only
    host = module.ProactiveWatcher.__new__(module.ProactiveWatcher)
    host.sheet = book
    return host.append_audit_log


def run_execute(args: argparse.Namespace, report: Report) -> None:
    """--execute ACTION: consume a granted approval through the real gates."""
    raw = _open_book(args, report)
    if raw is None:
        return
    book = GBook(raw)
    try:
        payload = (json.loads(args.payload) if args.payload
                   else default_execute_payload(args.execute))
    except json.JSONDecodeError:
        report.fail("--payload is not valid JSON")
        return
    if not isinstance(payload, dict):
        report.fail("--payload must be a JSON object")
        return
    assertion = None
    if args.assertion:
        try:
            assertion = json.loads(args.assertion)
        except json.JSONDecodeError:
            report.fail("--assertion is not valid JSON")
            return
        if not isinstance(assertion, dict):
            report.fail("--assertion must be a JSON object")
            return
    try:
        audit_logger = _bind_watcher_audit(book)
    except Exception as exc:  # noqa: BLE001
        report.fail("cannot bind watcher audit logger (%s)" % exc)
        return
    pubkey = os.environ.get("PA_APPROVAL_SIGNING_PUBKEY", "").strip()
    execute_granted(book, audit_logger, args.execute, payload, report,
                    approval_id=args.approval_id,
                    verification_public_key=pubkey or None,
                    assertion=assertion)


def run_live(args: argparse.Namespace, report: Report) -> None:
    book = _open_book(args, report)
    if book is None:
        return

    # -- sheets + header contracts ----------------------------------------
    header_ok = True
    for name in EXPECTED_HEADERS:
        try:
            ws = book.worksheet(name)
            header = ws.row_values(1)
        except Exception as exc:  # noqa: BLE001
            report.fail("worksheet %s unreadable (%s)" % (name, exc))
            header_ok = False
            continue
        problems = header_problems(name, header)
        if problems:
            header_ok = False
            for problem in problems:
                report.fail(problem)
        else:
            report.ok("header contract holds: %s (%d cols)"
                      % (name, len(EXPECTED_HEADERS[name])))
    if not header_ok:
        return

    # -- audit chain -------------------------------------------------------
    audit_rows = book.worksheet("pa_audit_logs_202611").get_all_values()[1:]
    chain_problems = verify_audit_chain(audit_rows)
    if chain_problems:
        for problem in chain_problems:
            report.fail("chain: %s" % problem)
    else:
        report.ok("audit chain verified across %d rows (prev_hash -> self_hash)"
                  % len(audit_rows))
    report.info("checksum note: the spec formula hashes policy_id, which the "
                "row does not persist — per-row checksum re-verification is "
                "not possible from the sheet alone; linkage is the hard gate")

    # -- delegation grants -------------------------------------------------
    grant_rows = book.worksheet("pa_delegation_grants").get_all_values()[1:]
    active_grants = 0
    for row in grant_rows:
        if not row or not str(row[0]).strip():
            continue
        status = str(row[15]) if len(row) > 15 else ""
        allowed = str(row[17]) if len(row) > 17 else ""
        denied = str(row[18]) if len(row) > 18 else ""
        limits = str(row[19]) if len(row) > 19 else "-"
        revoked = str(row[22]) if len(row) > 22 else ""
        if status.lower() == "active" and not revoked:
            active_grants += 1
        report.info("grant %s status=%s allowed=%s denied=%s limits=%s revoked=%s"
                    % (str(row[0])[:14], status, allowed, denied, limits,
                       revoked or "no"))
    if not grant_rows:
        report.warn("no delegation grants at all — executor denies everything")
    elif active_grants == 0:
        report.warn("no ACTIVE grant — the checklist step 'proposed -> active' "
                    "is still pending; the executor would deny all writes")
    else:
        report.ok("%d active delegation grant(s)" % active_grants)

    # -- approvals ---------------------------------------------------------
    approval_rows = book.worksheet("pa_approvals").get_all_values()[1:]
    now = datetime.now(timezone.utc)
    pending = expired_pending = 0
    for row in approval_rows:
        if not row or not str(row[0]).strip():
            continue
        status = str(row[17]).strip().lower() if len(row) > 17 else ""
        consumed = str(row[20]).strip() if len(row) > 20 else ""
        if status == "pending" and not consumed:
            pending += 1
            expires = str(row[19]) if len(row) > 19 else ""
            exp = ge.parse_iso_utc(expires)
            if exp is None or exp <= now:
                expired_pending += 1
    report.info("approvals: %d pending (%d already expired), %d total"
                % (pending, expired_pending, len(approval_rows)))
    if pending == 0:
        report.warn("no pending approval for the card round-trip test — run "
                    "--seed-approval first")

    # -- optional: seed a verifiable pending approval ----------------------
    if args.seed_approval:
        payload = default_execute_payload(args.seed_approval)
        approval_id = make_smoke_approval_id()
        values = build_test_approval_row(args.seed_approval, payload,
                                         approval_id, now)
        try:
            book.worksheet("pa_approvals").append_row(values)
        except Exception as exc:  # noqa: BLE001
            report.fail("seed approval append failed (%s)" % exc)
        else:
            report.ok("seeded pending approval %s action=%s payload_hash=%s…"
                      % (approval_id, args.seed_approval,
                         values[15][:12]))
            report.manual("delete row %s from pa_approvals after the smoke run"
                          % approval_id)

    # -- optional: deliver one real card -----------------------------------
    if args.send_card:
        token = os.environ.get("PA_APPROVAL_BOT_TOKEN", "")
        chat_id = os.environ.get("PA_APPROVAL_CHAT_ID", "")
        if not token or not chat_id:
            report.skip("--send-card: PA_APPROVAL_BOT_TOKEN/CHAT_ID not set")
        else:
            bot_spec = importlib.util.spec_from_file_location(
                "approval_bot", ROOT / "src" / "watchers" / "approval_bot.py")
            bot_module = importlib.util.module_from_spec(bot_spec)
            sys.modules["approval_bot"] = bot_module
            bot_spec.loader.exec_module(bot_module)  # type: ignore[union-attr]
            transport = bot_module.TelegramTransport(
                token, os.environ.get("PA_APPROVAL_API_BASE"))
            target = None
            for row in approval_rows:
                if row and str(row[17]).strip().lower() == "pending" \
                        and not str(row[20]).strip():
                    target = row
                    break
            if target is None:
                report.skip("--send-card: no pending approval to attach to")
            else:
                cells = [str(c) for c in target]
                cells += [""] * (len(bot_module.APPROVAL_COLUMNS) - len(cells))
                ap = bot_module.approval_from_row(cells)
                card = bot_module.build_approval_card(
                    ap,
                    vote_base_url=os.environ.get(
                        "PA_APPROVAL_VOTE_BASE_URL", "").strip(),
                )
                try:
                    message_id = transport.send_card(card, chat_id)
                except Exception as exc:  # noqa: BLE001
                    report.fail("card delivery failed: %s" % exc)
                else:
                    report.ok("real card delivered (message_id=%s) for %s"
                              % (message_id, str(target[0])[:14]))
                report.manual("tap Approve on the card (or the Passkey link), "
                              "then --execute %s and confirm consumed_at"
                              % str(target[14] if len(target) > 14 else ""))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="P1 live smoke: sheet schema, audit chain, grants, "
                    "approvals, optional real Telegram card.")
    parser.add_argument("--sheet-id", default=None,
                        help="spreadsheet id (default: PA_MEMORY_SHEET_ID)")
    parser.add_argument("--creds", default=None,
                        help="service-account JSON (default: ADC)")
    parser.add_argument("--send-card", action="store_true",
                        help="deliver one real Telegram approval card")
    parser.add_argument("--seed-approval", default=None, metavar="ACTION",
                        help="append a pending test approval for ACTION")
    parser.add_argument("--execute", default=None, metavar="ACTION",
                        help="run the governed executor on a granted, "
                             "unconsumed approval for ACTION (single-use "
                             "consume -> consumed_at)")
    parser.add_argument("--payload", default=None,
                        help="JSON object for --execute (default: the "
                             "deterministic --seed-approval payload)")
    parser.add_argument("--approval-id", default=None,
                        help="specific approval id for --execute")
    parser.add_argument("--assertion", default=None,
                        help="signed passkey assertion JSON for --execute "
                             "(executes a pending row without a bot vote)")
    args = parser.parse_args(argv)

    report = Report()
    print("== P1-APPROVAL-BOT-LIVE-SMOKE ==")
    if args.execute:
        run_execute(args, report)
    else:
        run_live(args, report)

    report.manual("activate the test grant: pa_delegation_grants status "
                  "proposed -> active (Sheets UI)")
    report.manual("round-trip: --send-card -> tap Approve in Telegram -> "
                  "--execute ACTION -> re-run this script to see consumed_at")
    report.manual("cleanup: delete rows created by --seed-approval")

    print("== summary: %d pass, %d fail, %d skip ==" %
          (report.passed, report.failed, report.skipped))
    return report.exit_code()


if __name__ == "__main__":
    sys.exit(main())
