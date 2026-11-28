"""Tests for scripts/supabase_secret_sync.py — no network, no real secret."""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("sss", ROOT / "scripts" / "supabase_secret_sync.py")
sss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sss)

OIDC_ENV = {"ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc.example/token?x=1",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "runner-bearer"}


class FakeEdge:
    """Mimics the deployed function: OIDC gate, Vault, signature checks."""

    def __init__(self, stale_probes: int = 0, lie_digest: bool = False, refuse: int | None = None,
                 telegram_tokens: tuple[str, ...] = ("123456:celia-fixture-token",), celia_stale: int = 0):
        self.vault: dict[str, str] = {}
        self.telegram_tokens = telegram_tokens  # tokens Telegram accepts
        self.celia_stale = celia_stale
        self.stale = stale_probes
        self.lie = lie_digest
        self.refuse = refuse
        self.calls: list[str] = []
        self.audience = None

    def __call__(self, req):
        url = req.full_url
        self.calls.append(url)
        if url.startswith("https://oidc.example/"):
            assert req.get_header("Authorization") == "bearer runner-bearer"
            self.audience = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["audience"][0]
            return 200, json.dumps({"value": "gh.oidc.jwt"}).encode()
        if url == sss.CELIA_HEALTHZ:  # labels only; carries no secret
            assert req.get_method() == "GET" and req.data is None
            if self.celia_stale > 0:
                self.celia_stale -= 1
                return 200, json.dumps({"token_source": "env", "webhook_actual": "none", "providers": 0}).encode()
            tok = self.vault.get("CELIA_TELEGRAM_TOKEN")
            models = sum(1 for n in sss.CELIA_NAMES[1:] if n in self.vault)
            return 200, json.dumps({
                "telegram": bool(tok), "token_source": "vault" if tok else "none",
                "webhook_actual": ("none" if tok in self.telegram_tokens else "unknown") if tok else "unknown",
                "providers": models, "chat": "shadow", "webhook": "unset"}).encode()
        assert url.startswith(sss.BASE), f"secrets must only go to the constant BASE, got {url}"
        if url.endswith("/import-secrets"):
            assert req.get_header("Authorization") == "Bearer gh.oidc.jwt"
            if self.refuse:
                return self.refuse, b'{"ok":false,"reason":"claim_mismatch:ref"}'
            items = json.loads(req.data)["secrets"]
            self.vault.update(items)
            stored = [{"name": k, "sha256": ("0" * 64 if self.lie else hashlib.sha256(v.encode()).hexdigest())}
                      for k, v in items.items()]
            return 200, json.dumps({"ok": True, "stored": stored}).encode()
        if "/webhook" in url:
            if self.stale > 0:
                self.stale -= 1
                return 401, b""
            if req.get_method() == "GET":
                q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                ok = q["hub.verify_token"][0] == self.vault.get("WHATSAPP_CLOUD_VERIFY_TOKEN")
                return (200, q["hub.challenge"][0].encode()) if ok else (403, b"")
            yc = req.get_header("Ycloud-signature")
            if yc:
                t, s = (p.split("=", 1)[1] for p in yc.split(","))
                exp = hmac.new(self.vault["YCLOUD_WEBHOOK_SECRET"].encode(), f"{t}.".encode() + req.data,
                               hashlib.sha256).hexdigest()
                return (200, b'{"ok":true,"ingested":0}') if s == exp else (401, b"")
            hub = req.get_header("X-hub-signature-256")
            exp = hmac.new(self.vault["WHATSAPP_CLOUD_APP_SECRET"].encode(), req.data, hashlib.sha256).hexdigest()
            return (200, b"{}") if hub == f"sha256={exp}" else (401, b"")
        return 404, b""


def no_sleep(_s):
    return None


def test_collect_skips_unset_urls_and_bad_lengths_without_echoing_values():
    values, status = sss.collect({"YCLOUD_WEBHOOK_SECRET": "  whsec_fixture_not_real_1  ",
                                  "AI2027_WHATSAPP_WEBHOOK": "https://old.example/hook",
                                  "WHATSAPP_CLOUD_APP_SECRET": "short"})
    assert values == {"YCLOUD_WEBHOOK_SECRET": "whsec_fixture_not_real_1"}
    assert status["AI2027_WHATSAPP_WEBHOOK"].startswith("is a URL")
    assert status["WHATSAPP_CLOUD_VERIFY_TOKEN"] == "not set in GitHub secrets"
    assert "short" not in json.dumps(status) and "old.example" not in json.dumps(status)


def test_full_import_digests_match_and_live_probes_pass():
    edge = FakeEdge()
    env = {**OIDC_ENV, "YCLOUD_WEBHOOK_SECRET": "whsec_fixture_not_real_1",
           "WHATSAPP_CLOUD_APP_SECRET": "meta_app_secret_1", "WHATSAPP_CLOUD_VERIFY_TOKEN": "verify_tok_1"}
    rc, out = sss.run(env, http=edge, sleep=no_sleep)
    text = "\n".join(out)
    assert rc == 0, text
    assert edge.audience == "ai2027-supabase-import"
    assert edge.vault["YCLOUD_WEBHOOK_SECRET"] == "whsec_fixture_not_real_1"
    assert "**PASS** 3 secret(s)" in text and text.count("match `") == 3
    for v in ("whsec_fixture_not_real_1", "meta_app_secret_1", "verify_tok_1"):
        assert v not in text, "a secret value leaked into the report"


def test_stale_isolate_is_retried_then_passes():
    edge = FakeEdge(stale_probes=2)
    rc, out = sss.run({**OIDC_ENV, "YCLOUD_WEBHOOK_SECRET": "whsec_fixture_not_real_1"}, http=edge, sleep=no_sleep)
    assert rc == 0, out


def test_digest_mismatch_fails():
    rc, out = sss.run({**OIDC_ENV, "YCLOUD_WEBHOOK_SECRET": "whsec_fixture_not_real_1"},
                      http=FakeEdge(lie_digest=True), sleep=no_sleep)
    assert rc == 1 and "**MISMATCH**" in "\n".join(out)


def test_refused_import_reports_reason_and_fails():
    rc, out = sss.run({**OIDC_ENV, "YCLOUD_WEBHOOK_SECRET": "whsec_fixture_not_real_1"},
                      http=FakeEdge(refuse=401), sleep=no_sleep)
    assert rc == 1 and "HTTP 401 claim_mismatch:ref" in "\n".join(out)


def test_nothing_set_fails_loudly_and_never_calls_network():
    edge = FakeEdge()
    rc, out = sss.run(dict(OIDC_ENV), http=edge, sleep=no_sleep)
    assert rc == 1 and edge.calls == [] and "nothing imported" in "\n".join(out)


def test_dry_run_never_calls_network():
    edge = FakeEdge()
    rc, out = sss.run({**OIDC_ENV, "YCLOUD_WEBHOOK_SECRET": "whsec_fixture_not_real_1"}, http=edge, dry_run=True)
    assert rc == 0 and edge.calls == []


def test_missing_oidc_permission_is_a_clear_error():
    try:
        sss.run({"YCLOUD_WEBHOOK_SECRET": "whsec_fixture_not_real_1"}, http=FakeEdge(), sleep=no_sleep)
    except RuntimeError as e:
        assert "id-token: write" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


def test_workflow_is_manual_main_only_oidc_and_destination_is_constant():
    wf = (ROOT / ".github" / "workflows" / "supabase-secret-sync.yml").read_text()
    assert "workflow_dispatch:" in wf and "pull_request" not in wf and "push:" not in wf
    assert "id-token: write" in wf and "github.ref == 'refs/heads/main'" in wf
    assert "vars." not in wf, "destination must not be overridable by a repo variable"
    edge_src = (ROOT / "supabase" / "functions" / "ycloud-webhook" / "index.ts").read_text()
    assert ".github/workflows/supabase-secret-sync.yml@refs/heads/main" in edge_src
    assert sss.AUDIENCE in edge_src
    assert all(n in edge_src for n in sss.NAMES)


# ------------------------------------------------------------ --ycloud-api path
FIXTURE_KEY = "ycloud_api_key_fixture_not_real"
FIXTURE_WHSEC = "whsec_fixture_from_ycloud_api_9"


class FakeYCloud:
    """Mimics api.ycloud.com/v2/webhookEndpoints (list returns `secret`, per the OpenAPI schema)."""

    def __init__(self, endpoints=None, list_code=200, list_has_secret=True):
        self.endpoints = list(endpoints or [])
        self.list_code = list_code
        self.list_has_secret = list_has_secret
        self.calls: list[tuple[str, str]] = []
        self.created: list[dict] = []

    def __call__(self, req):
        assert req.get_header("X-api-key") == FIXTURE_KEY
        path = req.full_url[len(sss.YCLOUD_API):]
        self.calls.append((req.get_method(), path))
        if req.get_method() == "GET" and path.startswith("/webhookEndpoints?"):
            if self.list_code != 200:
                return self.list_code, b'{"error":{"status":401,"code":"UNAUTHORIZED"}}'
            items = [{k: v for k, v in e.items() if k != "secret" or self.list_has_secret} for e in self.endpoints]
            return 200, json.dumps({"offset": 0, "limit": 100, "length": len(items), "items": items}).encode()
        if req.get_method() == "GET" and path.startswith("/webhookEndpoints/"):
            ep = next(e for e in self.endpoints if e["id"] == path.rsplit("/", 1)[1])
            return 200, json.dumps(ep).encode()
        if req.get_method() == "POST" and path == "/webhookEndpoints":
            body = json.loads(req.data)
            self.created.append(body)
            ep = {"id": "wh_created_1", "status": "active", "secret": FIXTURE_WHSEC, **body}
            self.endpoints.append(ep)
            return 200, json.dumps(ep).encode()
        return 404, b"{}"


def ep(i, url=None, secret=FIXTURE_WHSEC, status="active", events=sss.YCLOUD_EVENTS):
    return {"id": i, "url": url or sss.WEBHOOK_URL, "status": status, "secret": secret, "enabledEvents": list(events)}


def combo(yc, edge):
    return lambda req: yc(req) if req.full_url.startswith(sss.YCLOUD_API) else edge(req)


def yrun(yc, edge=None, extra=None, **kw):
    edge = edge or FakeEdge()
    env = {**OIDC_ENV, "YCLOUD_API_KEY": FIXTURE_KEY, **(extra or {})}
    rc, out = sss.run(env, http=combo(yc, edge), sleep=no_sleep, ycloud_api=True, **kw)
    text = "\n".join(out)
    assert FIXTURE_KEY not in text and FIXTURE_WHSEC not in text, "key or secret leaked into the report"
    return rc, text, edge


def test_ycloud_creates_endpoint_when_none_points_at_us_and_imports_its_secret():
    yc = FakeYCloud(endpoints=[ep("wh_other", url="https://elsewhere.example/hook", secret="whsec_other_x")])
    rc, text, edge = yrun(yc)
    assert rc == 0, text
    assert yc.created == [{"url": sss.WEBHOOK_URL, "enabledEvents": list(sss.YCLOUD_EVENTS),
                           "description": sss.YCLOUD_DESCRIPTION}]
    assert edge.vault == {"YCLOUD_WEBHOOK_SECRET": FIXTURE_WHSEC}
    assert "endpoint created `wh_created_1`" in text and "signed 200 · forged 401" in text
    assert "**PASS** 1 secret(s)" in text


def test_ycloud_reuses_the_console_endpoint_without_creating_or_rotating():
    yc = FakeYCloud(endpoints=[ep("wh_console", url=sss.WEBHOOK_URL + "/")])
    rc, text, edge = yrun(yc)
    assert rc == 0, text
    assert yc.created == [] and all(m == "GET" for m, _ in yc.calls)
    assert "existing endpoint reused `wh_console`" in text
    assert edge.vault["YCLOUD_WEBHOOK_SECRET"] == FIXTURE_WHSEC


def test_ycloud_fetches_endpoint_by_id_when_list_omits_secret():
    yc = FakeYCloud(endpoints=[ep("wh_console")], list_has_secret=False)
    rc, text, _ = yrun(yc)
    assert rc == 0, text
    assert ("GET", "/webhookEndpoints/wh_console") in yc.calls


def test_ycloud_two_endpoints_for_our_url_is_a_hard_fail_and_imports_nothing():
    yc = FakeYCloud(endpoints=[ep("wh_a"), ep("wh_b", secret="whsec_fixture_second_1")])
    rc, text, edge = yrun(yc)
    assert rc == 1 and "2 YCloud endpoints point at our URL (`wh_a`, `wh_b`)" in text
    assert edge.calls == [] and edge.vault == {}


def test_ycloud_bad_key_fails_with_hint_and_imports_nothing():
    rc, text, edge = yrun(FakeYCloud(list_code=401))
    assert rc == 1 and "HTTP 401 UNAUTHORIZED — YCLOUD_API_KEY is wrong or revoked" in text
    assert edge.calls == []


def test_ycloud_endpoint_without_inbound_event_fails():
    rc, text, edge = yrun(FakeYCloud(endpoints=[ep("wh_c", events=["whatsapp.message.updated"])]))
    assert rc == 1 and "not subscribed to `whatsapp.inbound_message.received`" in text
    assert edge.vault == {}


def test_ycloud_pending_endpoint_imports_but_warns():
    rc, text, _ = yrun(FakeYCloud(endpoints=[ep("wh_p", status="pending")]))
    assert rc == 0 and "**WARN** endpoint status is `pending`" in text


def test_ycloud_value_wins_over_a_stale_github_copy():
    rc, text, edge = yrun(FakeYCloud(endpoints=[ep("wh_s")]),
                          extra={"YCLOUD_WEBHOOK_SECRET": "whsec_old_hand_copied_1"})
    assert rc == 0 and "**GitHub copy is stale**" in text
    assert edge.vault["YCLOUD_WEBHOOK_SECRET"] == FIXTURE_WHSEC
    assert "whsec_old_hand_copied_1" not in text


def test_ycloud_missing_key_fails_before_any_network():
    yc, edge = FakeYCloud(), FakeEdge()
    rc, out = sss.run(dict(OIDC_ENV), http=combo(yc, edge), sleep=no_sleep, ycloud_api=True)
    assert rc == 1 and "YCLOUD_API_KEY Actions secret — not set" in "\n".join(out)
    assert yc.calls == [] and edge.calls == []


def test_ycloud_dry_run_never_calls_network():
    yc, edge = FakeYCloud(), FakeEdge()
    rc, out = sss.run({**OIDC_ENV, "YCLOUD_API_KEY": FIXTURE_KEY}, http=combo(yc, edge),
                      ycloud_api=True, dry_run=True)
    assert rc == 0 and yc.calls == [] and edge.calls == []
    assert FIXTURE_KEY not in "\n".join(out)


def test_ycloud_secret_and_key_never_reach_stdout_even_in_actions(capsys):
    """Nothing is ever printed with a value in it — not even an ::add-mask:: command."""
    for scenario in ([ep("wh_m")], [ep("wh_a"), ep("wh_b")], [ep("wh_c", events=["x.y"])]):
        rc, text, _ = yrun(FakeYCloud(endpoints=scenario), extra={"GITHUB_ACTIONS": "true"})
        out = capsys.readouterr().out
        assert FIXTURE_WHSEC not in out and FIXTURE_KEY not in out and "::add-mask::" not in out
    src = (ROOT / "scripts" / "supabase_secret_sync.py").read_text()
    assert "add-mask" not in src, "values must never be written to the log stream, even for masking"


def test_workflow_passes_ycloud_key_via_env_and_flag():
    wf = (ROOT / ".github" / "workflows" / "supabase-secret-sync.yml").read_text()
    assert "YCLOUD_API_KEY: ${{ secrets.YCLOUD_API_KEY }}" in wf and "--ycloud-api" in wf
    assert "ycloud_api:" in wf and "secrets.YCLOUD_API_KEY }}\" " not in wf
    assert sss.YCLOUD_API == "https://api.ycloud.com/v2" and sss.WEBHOOK_URL.startswith(sss.BASE)


# ------------------------------------------------- CELIA through the same door
CELIA_TOKEN = "123456:celia-fixture-token"


def test_celia_secrets_ride_the_same_oidc_import_and_are_proven_live():
    edge = FakeEdge(celia_stale=2)  # isolates cache Vault: first reads are stale
    env = {**OIDC_ENV, "CELIA_TELEGRAM_TOKEN": CELIA_TOKEN, "GEMINI_API_KEY": "gemini_fixture_key_1"}
    rc, out = sss.run(env, http=edge, sleep=no_sleep)
    text = "\n".join(out)
    assert rc == 0, text
    assert edge.vault["CELIA_TELEGRAM_TOKEN"] == CELIA_TOKEN
    assert "celia-telegram uses Vault · Telegram accepted it" in text
    assert "1 free provider(s) configured" in text
    assert "**PASS** 2 secret(s)" in text
    for v in (CELIA_TOKEN, "gemini_fixture_key_1"):
        assert v not in text, "a secret value leaked into the report"


def test_a_token_telegram_rejects_fails_the_run():
    edge = FakeEdge(telegram_tokens=())
    rc, out = sss.run({**OIDC_ENV, "CELIA_TELEGRAM_TOKEN": CELIA_TOKEN}, http=edge, sleep=no_sleep)
    text = "\n".join(out)
    assert rc == 1 and "**did not accept it**" in text, text


def test_workflow_passes_the_runner_token_as_celia_token_and_never_the_gates():
    wf = (ROOT / ".github" / "workflows" / "supabase-secret-sync.yml").read_text()
    assert "CELIA_TELEGRAM_TOKEN: ${{ secrets.PA_APPROVAL_BOT_TOKEN || secrets.TELEGRAM_BOT_TOKEN }}" in wf
    for name in sss.CELIA_NAMES[1:]:
        assert "%s: ${{ secrets.%s }}" % (name, name) in wf
    code = "\n".join(line for line in wf.splitlines() if not line.lstrip().startswith("#"))
    assert "CELIA_WEBHOOK" not in code and "CELIA_CHAT" not in code
    assert "CELIA_WEBHOOK" not in sss.NAMES and "CELIA_CHAT" not in sss.NAMES
    sql = (ROOT / "supabase" / "migrations" / "20261128000500_celia_vault_secrets.sql").read_text()
    assert all("'%s'" % n in sql for n in sss.NAMES), "SQL allowlist == script NAMES"
    celia_src = (ROOT / "supabase" / "functions" / "celia-telegram" / "index.ts").read_text()
    assert all('"%s"' % n in celia_src for n in sss.CELIA_NAMES)
