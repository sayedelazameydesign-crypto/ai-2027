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
        dry_run: bool = False) -> tuple[int, list[str]]:
    lines = ["| name | GitHub | Vault digest | live probe |", "|---|---|---|---|"]
    values, status = collect(env)
    rows = {n: [status[n], "—", "—"] for n in NAMES}

    def table() -> list[str]:
        return lines + [f"| `{n}` | {r[0]} | {r[1]} | {r[2]} |" for n, r in rows.items()]

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
    args = ap.parse_args(argv)
    try:
        rc, out = run(os.environ, dry_run=args.dry_run)
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
