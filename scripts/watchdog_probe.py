#!/usr/bin/env python3
"""24x7 watchdog probes for .github/workflows/watchdog-24x7.yml (stdlib only; gspread only for audit-chain).

    python3 scripts/watchdog_probe.py probe                  # public webhook: /healthz + fail-closed gate
    python3 scripts/watchdog_probe.py audit-chain --creds F  # pa_audit_logs prev_hash linkage, read-only
    python3 scripts/watchdog_probe.py notify --status failure|success

Design rules (this repository's Actions logs are PUBLIC):

* ``probe`` sends NO secret. It proves liveness with ``GET /healthz`` and proves the
  signature gate is enforced with an *unsigned* ``POST /webhook`` that must be refused
  with 401. (The Meta verify-token handshake is deliberately not used: that token is
  also the YCloud signing-secret fallback in src/connectors/whatsapp_cloud.py, and a
  secret in a URL ends up in proxy/access logs.)
* ``audit-chain`` reads only the audit worksheet and prints counts and row numbers —
  never cell values, hashes, grants or approvals.
* ``notify`` alerts on state CHANGES only (first failure, recovery) plus a reminder
  every ``--remind-every`` consecutive failures, so an outage cannot become 144
  Telegram messages a day. If the run history cannot be read it alerts anyway
  (fail towards telling a human).

Exit codes: 0 = pass or deliberate skip (``::notice`` explains), 1 = failure.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, List, Optional, Sequence, Tuple

USER_AGENT = "ai2027-watchdog/1.0"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_AUDIT_WORKSHEET = "pa_audit_logs_202611"  # same name as live_smoke_runner / governed_executor

Check = Tuple[str, bool, str]


def _log(level: str, title: str, message: str) -> None:
    """GitHub annotation when running in Actions, plain text otherwise."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::{level} title={title}::{message}")
    else:
        print(f"[{level.upper()}] {title}: {message}")


def _summary(lines: Sequence[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


# ── probe ────────────────────────────────────────────────────────────────────
def public_base_url(raw: str) -> str:
    """Normalise AI2027_WEBHOOK_PUBLIC_URL (with or without /webhook) to a base URL.

    Refuses anything that could leak or mislead: credentials or a query string in the
    URL, a non-https scheme (http is allowed only for loopback tests).
    """
    url = urllib.parse.urlsplit(raw.strip())
    if url.scheme not in ("https", "http") or not url.hostname:
        raise ValueError("AI2027_WEBHOOK_PUBLIC_URL must be an absolute https:// URL")
    if url.scheme == "http" and url.hostname not in LOOPBACK_HOSTS:
        raise ValueError("AI2027_WEBHOOK_PUBLIC_URL must use https:// (http only for loopback)")
    if url.username or url.password:
        raise ValueError("AI2027_WEBHOOK_PUBLIC_URL must not embed credentials")
    if url.query or url.fragment:
        raise ValueError("AI2027_WEBHOOK_PUBLIC_URL must not carry a query string (no tokens in URLs)")
    path = url.path.rstrip("/")
    if path.endswith("/webhook"):
        path = path[: -len("/webhook")]
    return urllib.parse.urlunsplit((url.scheme, url.netloc, path, "", ""))


def _request(url: str, *, method: str = "GET", data: Optional[bytes] = None,
             timeout: float = 15.0) -> Tuple[int, bytes]:
    headers = {"User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (scheme validated)
            return resp.status, resp.read(4096)
    except urllib.error.HTTPError as exc:
        body = exc.read(4096)
        exc.close()
        return exc.code, body


def _with_retry(fn: Callable[[], Tuple[int, bytes]], retries: int, delay: float) -> Tuple[int, bytes]:
    for attempt in range(retries + 1):
        try:
            return fn()
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            if attempt == retries:
                raise
            time.sleep(delay)
    raise AssertionError("unreachable")


def run_probe(base: str, *, timeout: float = 15.0, retries: int = 1, delay: float = 5.0) -> List[Check]:
    checks: List[Check] = []

    try:
        status, body = _with_retry(lambda: _request(f"{base}/healthz", timeout=timeout), retries, delay)
        ok = False
        if status == 200:
            try:
                ok = json.loads(body.decode("utf-8")).get("ok") is True
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                ok = False
        checks.append(("healthz", ok, f"HTTP {status}" + ("" if ok or status != 200 else " (body not ok:true)")))
    except Exception as exc:  # noqa: BLE001 — report the class only, never a URL or body
        checks.append(("healthz", False, f"unreachable ({type(exc).__name__})"))

    try:
        status, _ = _with_retry(
            lambda: _request(f"{base}/webhook", method="POST", data=b"{}", timeout=timeout), retries, delay
        )
        if status == 401:
            checks.append(("signature_gate", True, "unsigned POST refused (401)"))
        elif 200 <= status < 300:
            checks.append(("signature_gate", False, f"SECURITY: unsigned POST accepted (HTTP {status})"))
        else:
            checks.append(("signature_gate", False, f"unexpected HTTP {status} (expected 401)"))
    except Exception as exc:  # noqa: BLE001
        checks.append(("signature_gate", False, f"unreachable ({type(exc).__name__})"))
    return checks


def cmd_probe(args: argparse.Namespace) -> int:
    raw = os.environ.get("AI2027_WEBHOOK_PUBLIC_URL", "").strip()
    if not raw:
        _log("notice", "webhook-probe", "SKIP — repository variable AI2027_WEBHOOK_PUBLIC_URL is not set yet")
        _summary(["### Webhook probe", "SKIP — `AI2027_WEBHOOK_PUBLIC_URL` not configured"])
        return 0
    try:
        base = public_base_url(raw)
    except ValueError as exc:
        _log("error", "webhook-probe", str(exc))
        return 1
    checks = run_probe(base, timeout=args.timeout, retries=args.retries, delay=args.retry_delay)
    host = urllib.parse.urlsplit(base).hostname or "?"
    lines = [f"### Webhook probe · `{host}`", "| check | result | detail |", "|---|---|---|"]
    for name, ok, detail in checks:
        lines.append(f"| {name} | {'✅' if ok else '❌'} | {detail} |")
        _log("notice" if ok else "error", f"webhook-{name}", detail)
    _summary(lines)
    return 0 if all(ok for _, ok, _ in checks) else 1


# ── audit chain ──────────────────────────────────────────────────────────────
def summarise_chain_problems(problems: Sequence[str]) -> str:
    """Reduce verify_audit_chain() output to row numbers only (no hash fragments)."""
    leading = [re.match(r"row (\d+):", p) for p in problems]  # only the row the problem is ABOUT
    rows = sorted({int(m.group(1)) for m in leading if m})
    other = sum(1 for m in leading if not m)
    parts = []
    if rows:
        shown = ", ".join(map(str, rows[:20])) + (" …" if len(rows) > 20 else "")
        parts.append(f"{len(rows)} broken row(s): {shown}")
    if other:
        parts.append(f"{other} sheet-level problem(s)")
    return "; ".join(parts) or "no problems"


def cmd_audit_chain(args: argparse.Namespace) -> int:
    sheet_id = os.environ.get("PA_MEMORY_SHEET_ID", "").strip()
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not sheet_id or not (sa_json or args.creds):
        _log("notice", "audit-chain", "SKIP — PA_MEMORY_SHEET_ID / service-account credentials not configured")
        _summary(["### Audit chain", "SKIP — secrets not configured"])
        return 0
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scripts.live_smoke_runner import verify_audit_chain  # pure helper, no network

    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    try:
        import gspread
        from google.oauth2 import service_account

        if sa_json:  # CI: key text from env, never written to disk (same as approval_bot_ci.py)
            creds = service_account.Credentials.from_service_account_info(json.loads(sa_json), scopes=scopes)
        else:
            creds = service_account.Credentials.from_service_account_file(args.creds, scopes=scopes)
        rows = gspread.authorize(creds).open_by_key(sheet_id).worksheet(args.worksheet).get_all_values()[1:]
    except Exception as exc:  # noqa: BLE001 — secrets ARE configured, so not reading is a failure
        _log("error", "audit-chain", f"could not read the audit worksheet ({type(exc).__name__})")
        return 1
    problems = verify_audit_chain(rows)
    if problems:
        _log("error", "audit-chain", f"chain BROKEN across {len(rows)} rows — {summarise_chain_problems(problems)}")
        _summary(["### Audit chain", f"❌ {summarise_chain_problems(problems)} ({len(rows)} rows)"])
        return 1
    _log("notice", "audit-chain", f"intact across {len(rows)} rows (prev_hash -> self_hash)")
    _summary(["### Audit chain", f"✅ intact across {len(rows)} rows"])
    return 0


# ── notify ───────────────────────────────────────────────────────────────────
def failure_streak(conclusions: Sequence[str]) -> int:
    """Consecutive failures at the head of a newest-first list (cancelled/skipped ignored)."""
    streak = 0
    for c in conclusions:
        if c in ("cancelled", "skipped", "neutral", ""):
            continue
        if c in ("failure", "timed_out", "startup_failure"):
            streak += 1
            continue
        break
    return streak


def decide(status: str, previous_streak: Optional[int], remind_every: int) -> Optional[str]:
    """Return 'alert' | 'reminder' | 'recovery' | None."""
    failed = status != "success"
    if previous_streak is None:  # history unknown -> tell a human about failures, stay quiet on success
        return "alert" if failed else None
    if failed:
        if previous_streak == 0:
            return "alert"
        if remind_every > 0 and (previous_streak + 1) % remind_every == 0:
            return "reminder"
        return None
    return "recovery" if previous_streak > 0 else None


def previous_conclusions(repo: str, workflow: str, current_run_id: str, token: str,
                         api: str = "https://api.github.com") -> List[str]:
    url = f"{api}/repos/{repo}/actions/workflows/{urllib.parse.quote(workflow)}/runs?status=completed&per_page=100"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    })
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        runs = json.loads(resp.read().decode("utf-8")).get("workflow_runs", [])
    return [str(r.get("conclusion") or "") for r in runs if str(r.get("id")) != str(current_run_id)]


def build_message(kind: str, streak_now: int, run_url: str) -> str:
    if kind == "recovery":
        return f"✅ AI-2027 Watchdog: عاد كل شيء للعمل بعد {streak_now} فحص(اً) فاشلاً.\n{run_url}"
    label = "🚨 AI-2027 Watchdog Alert" if kind == "alert" else "⏰ AI-2027 Watchdog: ما زال الفشل مستمراً"
    return (
        f"{label}\n"
        f"فشل فحص المراقبة الدورية (Webhook أو Audit Chain أو الفحص الذاتي). الفشل المتتالي: {streak_now}\n"
        f"التفاصيل: {run_url}"
    )


def send_telegram(token: str, chat_id: str, text: str, api: str = "https://api.telegram.org") -> int:
    payload = json.dumps({"chat_id": chat_id, "text": text, "disable_web_page_preview": True}).encode("utf-8")
    req = urllib.request.Request(f"{api}/bot{token}/sendMessage", data=payload, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        return resp.status


def cmd_notify(args: argparse.Namespace) -> int:
    status = args.status
    prev: Optional[int]
    try:
        prev = failure_streak(previous_conclusions(
            os.environ["GITHUB_REPOSITORY"], args.workflow, os.environ.get("GITHUB_RUN_ID", ""),
            os.environ["GITHUB_TOKEN"], os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        ))
    except Exception as exc:  # noqa: BLE001
        _log("warning", "watchdog-history", f"run history unavailable ({type(exc).__name__}); failing loud")
        prev = None
    kind = decide(status, prev, args.remind_every)
    streak_now = (prev or 0) + 1 if status != "success" else (prev or 0)
    if kind is None:
        print(f"no notification (status={status}, previous_failure_streak={prev})")
        return 0
    token = os.environ.get("BOT_TOKEN", "").strip()
    chat_id = os.environ.get("CHAT_ID", "").strip()
    if not token or not chat_id:
        _log("warning", "watchdog-alert", f"would send '{kind}' but PA_APPROVAL_BOT_TOKEN / PA_APPROVAL_CHAT_ID are not set")
        return 0
    try:
        code = send_telegram(token, chat_id, build_message(kind, streak_now, os.environ.get("RUN_URL", "")),
                             os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org"))
        print(f"{kind} sent (HTTP {code})")
    except urllib.error.HTTPError as exc:
        _log("warning", "watchdog-alert", f"Telegram refused the {kind} (HTTP {exc.code})")
        exc.close()
    except Exception as exc:  # noqa: BLE001 — never echo the URL: it contains the bot token
        _log("warning", "watchdog-alert", f"Telegram unreachable ({type(exc).__name__})")
    return 0  # the job's own status already carries the failure; alert delivery must not mask it


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="AI-2027 24x7 watchdog probes")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe", help="public webhook liveness + fail-closed signature gate")
    p.add_argument("--timeout", type=float, default=15.0)
    p.add_argument("--retries", type=int, default=1)
    p.add_argument("--retry-delay", type=float, default=5.0)
    a = sub.add_parser("audit-chain", help="read-only prev_hash linkage check of the audit worksheet")
    a.add_argument("--creds", default="", help="service-account JSON file")
    a.add_argument("--worksheet", default=os.environ.get("AI2027_AUDIT_WORKSHEET", DEFAULT_AUDIT_WORKSHEET))
    n = sub.add_parser("notify", help="Telegram alert on state change")
    n.add_argument("--status", required=True, choices=["success", "failure", "cancelled"])
    n.add_argument("--workflow", default="watchdog-24x7.yml")
    n.add_argument("--remind-every", type=int, default=36, help="reminder every N consecutive failures (36 ≈ 6h)")
    args = parser.parse_args(argv)
    return {"probe": cmd_probe, "audit-chain": cmd_audit_chain, "notify": cmd_notify}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
