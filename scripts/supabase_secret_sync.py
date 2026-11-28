#!/usr/bin/env python3
"""Move the webhook secrets that live in GitHub Actions into Supabase Vault.

GitHub Actions secrets are write-only: no API, connector or person can read a
value back — only a workflow run sees it. So the run itself carries them over:

1. asks GitHub for an OIDC token (audience ``ai2027-supabase-import``); GitHub
   signs who we are: repo id, ``refs/heads/main``, this exact workflow file,
   ``workflow_dispatch``. No shared credential exists anywhere.
2. POSTs the allowlisted values once, over TLS, to the edge function's
   ``/import-secrets`` route, which verifies that token against GitHub's JWKS,
   burns its ``jti`` and stores the values in Supabase Vault.
3. compares the SHA-256 digests the server returns with its own (values never
   come back), then proves the deployed function really uses them: a signed
   probe must get 200 and a wrongly-signed one 401.

The destination is a constant — never a repo variable — so a mis-set variable
cannot redirect secrets to another host. Values are never printed; GitHub also
masks them in logs. Stdlib only.

    python3 scripts/supabase_secret_sync.py              # in the workflow
    python3 scripts/supabase_secret_sync.py --dry-run    # presence report only
    python3 scripts/supabase_secret_sync.py --ycloud-api # take YCLOUD_WEBHOOK_SECRET from YCloud

``--ycloud-api`` (needs the ``YCLOUD_API_KEY`` Actions secret) finds the YCloud
webhook endpoint whose URL is exactly ``WEBHOOK_URL`` — or creates it, subscribed
to inbound messages — and imports THAT endpoint's signing secret, so nobody ever
copies it by hand. Exactly one endpoint may point at us; extras are a hard fail.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Mapping

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "kernel-2027") not in sys.path:
    sys.path.insert(0, str(ROOT / "kernel-2027"))
from kernel2027 import sha256_text  # noqa: E402 — stdlib-only kernel, no install

BASE = "https://ziaedqqhvsmcwcmlbcpi.supabase.co/functions/v1/ycloud-webhook"
AUDIENCE = "ai2027-supabase-import"
NAMES = (
    "YCLOUD_WEBHOOK_SECRET",
    "WHATSAPP_CLOUD_APP_SECRET",
    "WHATSAPP_CLOUD_VERIFY_TOKEN",
    "AI2027_WHATSAPP_WEBHOOK",
)
PROBE_BODY = b'{"object":"ai2027.secret-sync.probe"}'  # parses to zero events: nothing staged
PROBE_ATTEMPTS = 6
WEBHOOK_URL = f"{BASE}/webhook"
YCLOUD_API = "https://api.ycloud.com/v2"
YCLOUD_EVENTS = ("whatsapp.inbound_message.received", "whatsapp.message.updated")
YCLOUD_DESCRIPTION = "ai-2027 · Supabase Edge (free 24/7) · managed by supabase-secret-sync.yml"
PROBE_WAIT_S = 15  # edge isolates cache Vault for <=60 s

Http = Callable[[urllib.request.Request], tuple[int, bytes]]


def default_http(req: urllib.request.Request) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 (https constant)
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b""


def collect(env: Mapping[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """(values to import, per-name status). Status never contains a value."""
    values: dict[str, str] = {}
    status: dict[str, str] = {}
    for name in NAMES:
        v = (env.get(name) or "").strip()
        if not v:
            status[name] = "not set in GitHub secrets"
        elif name == "AI2027_WHATSAPP_WEBHOOK" and v.lower().startswith(("http://", "https://")):
            status[name] = "is a URL, not a signing secret — skipped"
        elif not 8 <= len(v) <= 4096:
            status[name] = f"length {len(v)} outside 8..4096 — skipped"
        else:
            values[name] = v
            status[name] = "present"
    return values, status


def oidc_token(env: Mapping[str, str], http: Http) -> str:
    url, bearer = env.get("ACTIONS_ID_TOKEN_REQUEST_URL"), env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not bearer:
        raise RuntimeError("no OIDC context: the workflow needs `permissions: id-token: write`")
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(f"{url}{sep}audience={urllib.parse.quote(AUDIENCE)}",
                                 headers={"Authorization": f"bearer {bearer}"})
    code, body = http(req)
    if code != 200:
        raise RuntimeError(f"OIDC token request failed: HTTP {code}")
    return json.loads(body)["value"]


def _post(url: str, body: bytes, headers: dict[str, str], http: Http) -> tuple[int, bytes]:
    return http(urllib.request.Request(url, data=body, method="POST",
                                       headers={"Content-Type": "application/json", **headers}))


class StepFailed(Exception):
    """A failure whose message is safe to print: it never carries a secret or key."""


def _ycloud(method: str, path: str, api_key: str, http: Http, body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"{YCLOUD_API}{path}", data=data, method=method,
                                 headers={"X-API-Key": api_key, "Accept": "application/json",
                                          **({"Content-Type": "application/json"} if data else {})})
    code, raw = http(req)
    try:
        parsed = json.loads(raw or b"{}")
    except ValueError:
        parsed = {}
    return code, parsed if isinstance(parsed, dict) else {}


def _ycloud_error(what: str, code: int, payload: dict) -> StepFailed:
    err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    hint = {401: " — YCLOUD_API_KEY is wrong or revoked", 403: " — the API key lacks webhook permission"}.get(code, "")
    return StepFailed(f"YCloud {what}: HTTP {code} {str(err.get('code') or '')[:40]}{hint}".rstrip())


def ycloud_endpoint_secret(env: Mapping[str, str], http: Http) -> tuple[str, list[str]]:
    """(signing secret, safe report lines). Finds or creates THE endpoint for WEBHOOK_URL."""
    api_key = (env.get("YCLOUD_API_KEY") or "").strip()
    if not api_key:
        raise StepFailed("--ycloud-api needs the YCLOUD_API_KEY Actions secret — not set")
    code, page = _ycloud("GET", "/webhookEndpoints?limit=100", api_key, http)
    if code != 200:
        raise _ycloud_error("list endpoints", code, page)
    items = [e for e in page.get("items") or [] if isinstance(e, dict)]
    ours = [e for e in items if str(e.get("url") or "").rstrip("/") == WEBHOOK_URL]
    if len(ours) > 1:
        ids = ", ".join(f"`{str(e.get('id'))[:40]}`" for e in ours)
        raise StepFailed(f"{len(ours)} YCloud endpoints point at our URL ({ids}); each has its own "
                         "secret — delete the extras in YCloud → Developers → Webhook, keep one")
    if ours:
        ep, action = ours[0], "existing endpoint reused"
    else:
        code, ep = _ycloud("POST", "/webhookEndpoints", api_key, http,
                           {"url": WEBHOOK_URL, "enabledEvents": list(YCLOUD_EVENTS),
                            "description": YCLOUD_DESCRIPTION})
        if code not in (200, 201):
            raise _ycloud_error("create endpoint", code, ep)
        action = "endpoint created"
    ep_id = str(ep.get("id") or "")
    secret = str(ep.get("secret") or "").strip()
    if not secret and ep_id:
        code, full = _ycloud("GET", f"/webhookEndpoints/{urllib.parse.quote(ep_id, safe='')}", api_key, http)
        secret = str(full.get("secret") or "").strip() if code == 200 else ""
        ep = full if code == 200 else ep
    events = [str(x) for x in ep.get("enabledEvents") or []]
    status = str(ep.get("status") or "unknown")
    report = [f"YCloud: {action} `{ep_id[:40]}` · status `{status[:20]}` · {len(events)} event(s)"]
    if not secret:
        raise StepFailed(f"YCloud endpoint `{ep_id[:40]}` returned no signing secret")
    if not 8 <= len(secret) <= 4096:
        raise StepFailed(f"YCloud signing secret length {len(secret)} outside 8..4096")
    if YCLOUD_EVENTS[0] not in events:
        raise StepFailed(f"YCloud endpoint `{ep_id[:40]}` is not subscribed to `{YCLOUD_EVENTS[0]}` — "
                         "enable it in YCloud → Developers → Webhook")
    if status != "active":
        report.append(f"**WARN** endpoint status is `{status[:20]}` — YCloud delivers only to `active`; "
                      "re-enable it in YCloud → Developers → Webhook")
    return secret, report


def ycloud_probe(secret: str, http: Http, now: Callable[[], float] = time.time) -> tuple[int, int]:
    ts = str(int(now()))
    good = hmac.new(secret.encode(), ts.encode() + b"." + PROBE_BODY, hashlib.sha256).hexdigest()
    ok, _ = _post(f"{BASE}/webhook", PROBE_BODY, {"YCloud-Signature": f"t={ts},s={good}"}, http)
    bad, _ = _post(f"{BASE}/webhook", PROBE_BODY, {"YCloud-Signature": f"t={ts},s={'0' * 64}"}, http)
    return ok, bad


def meta_probe(secret: str, http: Http) -> tuple[int, int]:
    good = hmac.new(secret.encode(), PROBE_BODY, hashlib.sha256).hexdigest()
    ok, _ = _post(f"{BASE}/webhook", PROBE_BODY, {"X-Hub-Signature-256": f"sha256={good}"}, http)
    bad, _ = _post(f"{BASE}/webhook", PROBE_BODY, {"X-Hub-Signature-256": "sha256=" + "0" * 64}, http)
    return ok, bad


def verify_token_probe(token: str, http: Http) -> int:
    q = urllib.parse.urlencode({"hub.mode": "subscribe", "hub.verify_token": token,
                                "hub.challenge": "ai2027probe"})
    code, body = http(urllib.request.Request(f"{BASE}/webhook?{q}"))
    return code if body == b"ai2027probe" else -code


def retry(fn: Callable[[], bool], sleep: Callable[[float], None]) -> bool:
    for i in range(PROBE_ATTEMPTS):
        if fn():
            return True
        if i + 1 < PROBE_ATTEMPTS:
            sleep(PROBE_WAIT_S)
    return False


def run(env: Mapping[str, str], http: Http = default_http, sleep: Callable[[float], None] = time.sleep,
        dry_run: bool = False, ycloud_api: bool = False) -> tuple[int, list[str]]:
    lines = ["| name | source | Vault digest | live probe |", "|---|---|---|---|"]
    values, status = collect(env)
    rows = {n: [status[n], "—", "—"] for n in NAMES}
    notes: list[str] = []

    def table() -> list[str]:
        return lines + [f"| `{n}` | {r[0]} | {r[1]} | {r[2]} |" for n, r in rows.items()] + (
            ["", *notes] if notes else [])

    if ycloud_api:
        if dry_run:
            has_key = bool((env.get("YCLOUD_API_KEY") or "").strip())
            rows["YCLOUD_WEBHOOK_SECRET"][0] = ("YCloud API (key present)" if has_key
                                                else "YCloud API — **YCLOUD_API_KEY not set**")
            return (0 if has_key else 1), table() + ["", "dry run: YCloud API not called, nothing imported."]
        try:
            secret, notes = ycloud_endpoint_secret(env, http)
        except StepFailed as exc:
            return 1, table() + ["", f"**FAIL** {exc}"]
        github_value = values.get("YCLOUD_WEBHOOK_SECRET")
        values["YCLOUD_WEBHOOK_SECRET"] = secret
        rows["YCLOUD_WEBHOOK_SECRET"][0] = "YCloud API" + (
            "" if github_value is None else
            " (GitHub copy identical)" if hmac.compare_digest(github_value, secret) else
            " (**GitHub copy is stale** — YCloud value used)")

    if not values:
        return 1, table() + ["", "**FAIL** none of the four names holds a usable value — nothing imported."]
    if dry_run:
        return 0, table() + ["", f"dry run: {len(values)} value(s) would be imported."]

    code, body = _post(f"{BASE}/import-secrets", json.dumps({"secrets": values}).encode(),
                       {"Authorization": f"Bearer {oidc_token(env, http)}"}, http)
    if code != 200:
        reason = ""
        try:
            reason = json.loads(body).get("reason", "")
        except (ValueError, AttributeError):
            pass
        return 1, table() + ["", f"**FAIL** import refused: HTTP {code} {reason}".rstrip()]
    stored = {d["name"]: d["sha256"] for d in json.loads(body).get("stored", [])}
    failed = False
    for name, v in values.items():
        match = stored.get(name) == sha256_text(v)
        rows[name][1] = f"match `{stored[name][:12]}…`" if match else "**MISMATCH**"
        failed |= not match

    if "YCLOUD_WEBHOOK_SECRET" in values:
        res: dict[str, tuple[int, int]] = {}
        ok = retry(lambda: (res.__setitem__("r", ycloud_probe(values["YCLOUD_WEBHOOK_SECRET"], http))
                            or res["r"] == (200, 401)), sleep)
        rows["YCLOUD_WEBHOOK_SECRET"][2] = ("signed 200 · forged 401" if ok
                                            else f"**FAIL** signed {res['r'][0]} · forged {res['r'][1]}")
        failed |= not ok
    if "WHATSAPP_CLOUD_APP_SECRET" in values:
        res2: dict[str, tuple[int, int]] = {}
        ok = retry(lambda: (res2.__setitem__("r", meta_probe(values["WHATSAPP_CLOUD_APP_SECRET"], http))
                            or res2["r"] == (200, 401)), sleep)
        rows["WHATSAPP_CLOUD_APP_SECRET"][2] = ("signed 200 · forged 401" if ok
                                                else f"**FAIL** signed {res2['r'][0]} · forged {res2['r'][1]}")
        failed |= not ok
    if "WHATSAPP_CLOUD_VERIFY_TOKEN" in values:
        res3: dict[str, int] = {}
        ok = retry(lambda: (res3.__setitem__("r", verify_token_probe(values["WHATSAPP_CLOUD_VERIFY_TOKEN"], http))
                            or res3["r"] == 200), sleep)
        rows["WHATSAPP_CLOUD_VERIFY_TOKEN"][2] = "challenge echoed 200" if ok else f"**FAIL** {res3['r']}"
        failed |= not ok

    verdict = "**FAIL** see table" if failed else f"**PASS** {len(values)} secret(s) in Vault and live"
    return (1 if failed else 0), table() + ["", verdict]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="presence report only; no network")
    ap.add_argument("--ycloud-api", action="store_true",
                    help="find/create the YCloud endpoint and import its signing secret (needs YCLOUD_API_KEY)")
    args = ap.parse_args(argv)
    try:
        rc, out = run(os.environ, dry_run=args.dry_run, ycloud_api=args.ycloud_api)
    except StepFailed as exc:  # composed by us from non-secret parts only
        rc, out = 1, [f"**FAIL** {exc}"]
    except Exception as exc:  # report the class only: messages may carry URLs/tokens
        rc, out = 1, [f"**FAIL** {type(exc).__name__}"]
    text = "\n".join(["## Webhook secrets → Supabase Vault", "", *out])
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())
