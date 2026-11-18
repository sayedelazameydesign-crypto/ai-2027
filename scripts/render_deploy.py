#!/usr/bin/env python3
"""Deploy to Render — through this repository's own policy gate, not around it.

`RENDER_API_KEY` is now a repository secret. The risk that creates is not the key
itself: it is a workflow that can destroy production on a typo. So this wrapper
does not re-implement the Render client — it drives
`render_control_adapter/adapter.py`, which already enforces three rules in code:

* reads are automatic, **mutations need an approval token** (`deploy` is a
  mutation);
* secret values are never returned or logged;
* a `200` on a deploy request does **not** mean the deploy worked — only
  `wait_for_deploy()` reaching a terminal status (plus `service_is_live()`) may
  set `verified: true`.

What this file adds on top:

1. the key comes from the environment only — never argv, never a URL;
2. **reachability first**: if `api.render.com` cannot be reached, the answer is
   `BLOCKED` (exit 3), not "the deploy failed". Previous sessions measured that
   this sandbox gets a TLS reset from Render while GitHub answers 200 — a wrapper
   that reported that as a failed deploy would be lying about a deploy that was
   never attempted;
3. **a deploy needs the service's exact name as confirmation**. No confirmation
   ⇒ dry run ⇒ nothing is sent. This is the guard against "it deployed because
   someone pressed a button".

Exit codes
    0  verified live (or a dry run that changed nothing)
    1  attempted and failed (status in the report says why)
    2  no RENDER_API_KEY → SKIPPED, never reported as a failed deploy
    3  api.render.com unreachable → BLOCKED
    4  refused: missing or mismatched --confirm (nothing was sent)

    python3 scripts/render_deploy.py                      # plan, changes nothing
    python3 scripts/render_deploy.py --confirm ai-2027-live-agent
    python3 scripts/render_deploy.py --service-id srv-123 --confirm ai-2027-live-agent --json

HONEST LIMIT: `render_control_adapter` was written from Render's documentation and
has never been exercised against the live API (its own docstring says so). The
first real run of this script is the evidence that changes that sentence.
Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "render_control_adapter"))

DEFAULT_SERVICE_NAME = "ai-2027-live-agent"
API_HOST = "https://api.render.com/"
OWNERS_URL = "https://api.render.com/v1/owners"

OK, FAILED, NO_KEY, BLOCKED, REFUSED = 0, 1, 2, 3, 4


def fingerprint(key: str) -> str:
    """Identify the credential in a report without disclosing it."""
    if not key:
        return "(none)"
    head = key[:4] if len(key) > 8 else "***"
    return f"{head}…({len(key)} chars)"


def scrub(text, key: str = "") -> str:
    """Provider errors can quote the token back; never store it verbatim."""
    if not text:
        return text
    out = str(text)
    if key:
        out = out.replace(key, "[redacted-key]")
    return out


def resolve_service(client, service_id: str | None, service_name: str) -> dict:
    """Find the service by id, else by exact name. Never guess a near-match."""
    if service_id:
        svc = client.get_service(service_id)
        return {"id": service_id, "name": svc.get("name"), "suspended": svc.get("suspended")}
    for svc in client.list_services(limit=100):
        if svc.get("name") == service_name:
            return {"id": svc.get("id"), "name": svc.get("name"),
                    "suspended": svc.get("suspended")}
    raise LookupError(f"no Render service named {service_name!r} — "
                      "pass --service-id or fix the name")


def plan(report: dict, service: dict, confirm: str | None) -> dict:
    """What would happen, and whether it is allowed to happen."""
    would_send = bool(confirm) and confirm == service.get("name")
    report["plan"] = {
        "service": service,
        "confirmation_required": service.get("name"),
        "confirmation_given": confirm or "",
        "allowed": would_send,
        "mutations_would_be_sent": would_send,
    }
    return report


def run(env: dict | None = None, service_id: str | None = None,
        service_name: str = DEFAULT_SERVICE_NAME, confirm: str | None = None,
        wait: bool = True, timeout: float = 900.0, poll: float = 5.0,
        client=None, probe_fn=None, sleep=time.sleep) -> dict:
    """The whole deploy decision, as data. `main` only formats and exits."""
    env = os.environ if env is None else env
    key = (env.get("RENDER_API_KEY") or "").strip()
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "key_fingerprint": fingerprint(key),
        "key_present": bool(key),
        "service_id": service_id or env.get("RENDER_SERVICE_ID") or None,
        "service_name": service_name,
        "ok": False,
        "status": "not_started",
        "verified": False,
        "mutations_sent": 0,
    }
    if not key:
        report["status"] = "skipped_no_key"
        return report

    import adapter
    import network_preflight

    if probe_fn is None:
        def probe_fn(url: str):
            return network_preflight.probe(url)

    reach = probe_fn(OWNERS_URL)
    reachable = bool(getattr(reach, "reachable", False))
    report["api_reachable"] = reachable
    if not reachable:
        report["status"] = "blocked_unreachable"
        report["blocked_by"] = str(getattr(reach, "error", "unreachable"))[:120]
        return report

    client = client or adapter.RenderClient(api_key=key)
    try:
        service = resolve_service(client, report["service_id"], service_name)
    except LookupError as exc:
        report["status"] = "service_not_found"
        report["error"] = scrub(str(exc), key)
        return report
    except Exception as exc:                      # auth/rate-limit/etc.
        report["status"] = "lookup_failed"
        report["error"] = scrub(f"{type(exc).__name__}: {exc}", key)[:200]
        return report

    report["service"] = service
    plan(report, service, confirm)
    if not report["plan"]["allowed"]:
        report["status"] = ("refused_confirmation_mismatch" if confirm
                            else "dry_run")
        return report

    sid = service["id"]
    approval = client.approvals.mint("deploy", sid, reason="scripts/render_deploy.py")
    report["approval"] = {"operation": approval.operation, "single_use": approval.single_use,
                          "expires_in_s": round(approval.expires_at - time.time(), 1)}
    try:
        started = client.deploy(sid, approval_token=approval.token)
    except Exception as exc:
        report["status"] = "deploy_request_failed"
        report["error"] = scrub(f"{type(exc).__name__}: {exc}", key)[:200]
        return report
    report["mutations_sent"] = 1
    # A 200 here proves only that Render accepted the request.
    report.update({"status": "accepted", "deploy_id": started.get("deploy_id"),
                   "accepted_status": started.get("status"), "verified": False})

    if not wait:
        return report

    final = client.wait_for_deploy(sid, started.get("deploy_id"), timeout=timeout,
                                   poll=poll, sleep=sleep)
    report["deploy"] = {k: final.get(k) for k in ("status", "verified", "elapsed_s")}
    if not final.get("verified"):
        report["status"] = "deploy_not_verified"
        report["error"] = f"deploy ended as {final.get('status')!r}"
        return report

    live = client.service_is_live(sid, probes=2, sleep=sleep)
    report["liveness"] = {"live": live.get("live"), "claim": live.get("claim")}
    report["verified"] = bool(final.get("verified")) and bool(live.get("live"))
    report["ok"] = report["verified"]
    report["status"] = "verified_live" if report["ok"] else "live_check_failed"
    return report


def main(argv: list[str] | None = None, env: dict | None = None, client=None,
         probe_fn=None, sleep=time.sleep) -> int:
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(description="Deploy to Render through the policy gate.")
    ap.add_argument("--service-id", default=None)
    ap.add_argument("--service-name", default=env.get("RENDER_SERVICE_NAME")
                    or DEFAULT_SERVICE_NAME)
    ap.add_argument("--confirm", default=None,
                    help="the service's exact name; without it this is a dry run")
    ap.add_argument("--no-wait", action="store_true",
                    help="do not poll the deploy (accepts, verifies nothing)")
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--poll", type=float, default=5.0)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", type=Path, default=None,
                    help="write the JSON report here (contains no secrets)")
    args = ap.parse_args(argv)

    report = run(env=env, service_id=args.service_id, service_name=args.service_name,
                 confirm=args.confirm, wait=not args.no_wait, timeout=args.timeout,
                 poll=args.poll, client=client, probe_fn=probe_fn, sleep=sleep)

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print("RENDER DEPLOY — one decision, gated and reported")
        print("=" * 66)
        print(f"  key        : {report['key_fingerprint']}")
        print(f"  service    : {report.get('service', {}).get('name', args.service_name)}"
              f" ({report.get('service', {}).get('id', 'unresolved')})")
        print(f"  status     : {report['status']}")
        print(f"  verified   : {report['verified']}  · mutations sent: {report['mutations_sent']}")
        if report.get("deploy_id"):
            print(f"  deploy     : {report['deploy_id']} → {report.get('deploy', {}).get('status')}")
        if report.get("error"):
            print(f"  reason     : {report['error']}")
        if report["status"] == "dry_run":
            print(f"  nothing was sent. To deploy: --confirm {args.service_name}")
        print("=" * 66)

    status = report["status"]
    if report["ok"] or status == "dry_run":
        return OK
    if status == "skipped_no_key":
        return NO_KEY
    if status == "blocked_unreachable":
        return BLOCKED
    if status in ("refused_confirmation_mismatch",):
        return REFUSED
    return FAILED


if __name__ == "__main__":
    raise SystemExit(main())
