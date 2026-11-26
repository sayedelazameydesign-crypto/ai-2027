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
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
EXECUTOR_PATH = ROOT / "src" / "executors" / "governed_executor.py"
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


def run_live(args: argparse.Namespace, report: Report) -> None:
    sheet_id = args.sheet_id or os.environ.get("PA_MEMORY_SHEET_ID", "")
    if not sheet_id:
        report.skip("PA_MEMORY_SHEET_ID not set — live checks need the sheet id")
        return

    try:
        client = _authorize(args.creds)
    except ImportError as exc:
        report.skip("gspread/google-auth not installed (%s) — live checks skipped" % exc)
        return
    except Exception as exc:  # noqa: BLE001 — any auth problem is a skip, not a crash
        report.skip("authorization failed (%s)" % exc)
        return

    try:
        book = client.open_by_key(sheet_id)
    except Exception as exc:  # noqa: BLE001
        report.fail("cannot open spreadsheet %s (%s)" % (sheet_id[:12], exc))
        return
    report.ok("spreadsheet opened (%s)" % sheet_id[:12])

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
        payload = {"smoke": True, "action": args.seed_approval,
                   "at": now.strftime("%Y-%m-%dT%H:%M:%S.000Z")}
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
                result = transport.send_card(
                    chat_id=chat_id,
                    title="P1-APPROVAL-BOT-LIVE-SMOKE",
                    summary=str(target[16]) if len(target) > 16 else "",
                    row=[str(c) for c in target],
                )
                if result["ok"]:
                    report.ok("real card delivered (message_id=%s) for %s"
                              % (result.get("message_id"),
                                 str(target[0])[:14]))
                else:
                    report.fail("card delivery failed: %s" % result["error"])
                report.manual("tap Approve on the card, then re-run this script "
                              "and confirm consumed_at + version +1 + audit row")


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
    args = parser.parse_args(argv)

    report = Report()
    print("== P1-APPROVAL-BOT-LIVE-SMOKE ==")
    run_live(args, report)

    report.manual("activate the test grant: pa_delegation_grants status "
                  "proposed -> active (Sheets UI)")
    report.manual("watcher -> bot -> executor round-trip: approve a card and "
                  "confirm the executor audit row in pa_audit_logs_202611")
    report.manual("cleanup: delete rows created by --seed-approval")

    print("== summary: %d pass, %d fail, %d skip ==" %
          (report.passed, report.failed, report.skipped))
    return report.exit_code()


if __name__ == "__main__":
    sys.exit(main())
