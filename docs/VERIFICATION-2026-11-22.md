# ✅ تحقق النشر — AI-2027 · 2026-11-22T16:39:14Z

> مولّد بـ `python3 scripts/verify_published.py` — كل سطر أدناه أُعيد تشغيله، لا منسوخ من README.
> `BLOCKED` تعني أن بيئة القياس لا ترى الشبكة، وليست حكماً بالموت.

**الخلاصة:** {'PROVEN': 24, 'DECLARED': 2, 'BLOCKED': 10}

| الفحص | الحكم | الدليل (مختصر) |
|---|---|---|
| `inventory.projects_on_disk` | **PROVEN** | 48 project dirs on disk; catalog declares total_projects=48 total_files=3982; files on disk (excl __pycache__)=3982 |
| `inventory.operational_status` | **DECLARED** | {'SIMULATED': 6, 'PROVEN': 1, 'DECLARED': 40, 'FAILED': 1} |
| `parse.render.yaml` | **PROVEN** | exists=True bytes=4566 |
| `parse..github/workflows/ci.yml` | **PROVEN** | exists=True bytes=58646 |
| `parse..github/workflows/pages.yml` | **PROVEN** | exists=True bytes=3067 |
| `parse..github/workflows/deploy.yml` | **PROVEN** | exists=True bytes=8447 |
| `parse.catalog.json` | **PROVEN** | valid JSON |
| `parse.docs/public-urls.json` | **PROVEN** | valid JSON |
| `parse.docs/deviations.json` | **PROVEN** | valid JSON |
| `parse.agent-card.json` | **PROVEN** | valid JSON |
| `github.repo` | **PROVEN** | {"defaultBranchRef":{"name":"main"},"forkCount":0,"isPrivate":false,"latestRelease":{"name":"v1.0.1","tagName":"v1.0.1","url":"https://github.com/sayedelazameydesign-crypto/ai-2027/releases/tag/v1.0.1","publishedAt":"2026-11-17T19:07:45Z"},"nameWithOwner":"sayedelazameydesign-crypto/ai-2027","pushed |
| `github.pages` | **PROVEN** | {"url":"https://api.github.com/repos/sayedelazameydesign-crypto/ai-2027/pages","status":"built","cname":null,"custom_404":false,"html_url":"https://sayedelazameydesign-crypto.github.io/ai-2027/","build_type":"workflow","source":{"branch":"main","path":"/"},"public":true,"protected_domain_state":null |
| `github.releases` | **PROVEN** | v1.0.1	Latest	v1.0.1	2026-11-17T19:07:45Z v1.0.0		v1.0.0	2026-11-17T17:54:22Z |
| `github.actions_recent` | **PROVEN** | 23s	5h completed	success	docs: AI-2027 inventory, promotion series, and execution guardrails	Evidence-First CI	arena/1e6efcb1-ai-2027	pull_request	45017361946	1m20s	5h completed	success	docs: AI-2027 inventory, promotion series, and execution guardrails	CodeQL	arena/1e6efcb1-ai-2027	pull_request	450 |
| `tests.agi-system` | **PROVEN** | : test ........................run run_e606ca79099a crashed Traceback (most recent call last):   File "/home/user/ai-2027/projects/agi-system/core/kernel.py", line 173, in run     return self._cycle(record, deadline)            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^   File "/home/user/ai-2027/projects/agi-sy |
| `tests.live-agent` | **PROVEN** | vel":"INFO","event":"run started"} {"source":"agi.kernel","level":"INFO","event":"kernel event"} ok test_traversal_is_denied_by_kernel_policy (__main__.TestRunGoal.test_traversal_is_denied_by_kernel_policy) ... {"source":"agi.kernel","level":"INFO","event":"run started"} {"source":"agi.kernel","leve |
| `tests.memory` | **PROVEN** | "INFO","event":"kernel event"} {"source":"agi.kernel","level":"INFO","event":"run started"} {"source":"agi.kernel","level":"INFO","event":"kernel event"} {"source":"agi.kernel","level":"INFO","event":"run started"} {"source":"agi.kernel","level":"INFO","event":"kernel event"} {"source":"agi.kernel", |
| `tests.hud` | **PROVEN** | 200 - ok test_17_http_evidence_and_history_survive_runtime_reload (__main__.HudTests.test_17_http_evidence_and_history_survive_runtime_reload) A real HUD HTTP run remains readable after reloading the runtime bridge. ... {"source":"agi.kernel","level":"INFO","event":"run started"} {"source":"agi.kern |
| `tests.demo-node` | **PROVEN** | ok   SHA-256 known-answer vectors (FIPS 180-4) ok   create_file reaches VERIFIED with independent sha256 evidence ok   path traversal is DENIED before the executor runs ok   absolute path and writing the sandbox root are DENIED ok   unknown capability is denied by the allowlist (fail closed) ok   pa |
| `gate.zero-dep-ast` | **PROVEN** | AST-scanned 18 Python files for third-party imports (Python 3.11.2). Conditional stdlib allowed across the 3.10+ matrix: tomllib PASS: stdlib only (zero third-party imports). |
| `audit.command-hud` | **PROVEN** | oject": "new-new",       "severity": "LOW",       "issue": "no LICENSE file"     },     {       "project": "new-new",       "severity": "LOW",       "issue": "no README"     },     {       "project": "new-project",       "severity": "LOW",       "issue": "no LICENSE file"     },     {       "project |
| `redteam.100` | **PROVEN** | ":"INFO","event":"run started"} {"source":"agi.kernel","level":"INFO","event":"kernel event"} {"source":"agi.kernel","level":"INFO","event":"run started"} {"source":"agi.kernel","level":"INFO","event":"kernel event"} {"source":"agi.kernel","level":"INFO","event":"run started"} {"source":"agi.kernel" |
| `bench.live-agent` | **PROVEN** | :"agi.kernel","level":"INFO","event":"run started"} {"source":"agi.kernel","level":"INFO","event":"kernel event"} {"request_id":"94a81635e4774049812a78df8b6262fc","method":"POST","route":"/api/run","status":200,"duration_ms":17.827} {"source":"agi.kernel","level":"INFO","event":"run started"} {"sour |
| `doctor.offline` | **DECLARED** | is visible",       "name": "write posture",       "remedy": "Set AI2027_READ_ONLY=1.",       "status": "warn"     },     {       "detail": "no provider credentials are set; the $0 offline fallback still works",       "name": "provider credentials",       "status": "pass"     },     {       "detail": |
| `url.https://sayedelazameydesign-crypto.github.io/ai-2027/demo.html` | **BLOCKED** | expected=live last_status=200 probe={'url': 'https://sayedelazameydesign-crypto.github.io/ai-2027/demo.html', 'verdict': 'BLOCKED', 'status': None, 'error': 'URLError: <urlopen error TLS/SSL connection has been closed (EOF) (_ssl.c:992)>'} |
| `url.https://sayedelazameydesign-crypto.github.io/ai-2027/` | **BLOCKED** | expected=live last_status=200 probe={'url': 'https://sayedelazameydesign-crypto.github.io/ai-2027/', 'verdict': 'BLOCKED', 'status': None, 'error': 'URLError: <urlopen error TLS/SSL connection has been closed (EOF) (_ssl.c:992)>'} |
| `url.https://cela-umber.vercel.app/` | **BLOCKED** | expected=live last_status=200 probe={'url': 'https://cela-umber.vercel.app/', 'verdict': 'BLOCKED', 'status': None, 'error': 'URLError: <urlopen error TLS/SSL connection has been closed (EOF) (_ssl.c:992)>'} |
| `url.https://new-pro-kohl.vercel.app/` | **BLOCKED** | expected=live last_status=200 probe={'url': 'https://new-pro-kohl.vercel.app/', 'verdict': 'BLOCKED', 'status': None, 'error': 'URLError: <urlopen error TLS/SSL connection has been closed (EOF) (_ssl.c:992)>'} |
| `url.https://cerulean-boba-48f59a.netlify.app/` | **BLOCKED** | expected=live last_status=200 probe={'url': 'https://cerulean-boba-48f59a.netlify.app/', 'verdict': 'BLOCKED', 'status': None, 'error': 'URLError: <urlopen error TLS/SSL connection has been closed (EOF) (_ssl.c:992)>'} |
| `url.https://elazamet1.vercel.app/` | **BLOCKED** | expected=dead last_status=404 probe={'url': 'https://elazamet1.vercel.app/', 'verdict': 'BLOCKED', 'status': None, 'error': 'URLError: <urlopen error TLS/SSL connection has been closed (EOF) (_ssl.c:992)>'} |
| `url.https://celia-pro.onrender.com/` | **BLOCKED** | expected=dead last_status=404 probe={'url': 'https://celia-pro.onrender.com/', 'verdict': 'BLOCKED', 'status': None, 'error': 'URLError: <urlopen error TLS/SSL connection has been closed (EOF) (_ssl.c:992)>'} |
| `url.https://nawah-agent.onrender.com/` | **BLOCKED** | expected=dead last_status=404 probe={'url': 'https://nawah-agent.onrender.com/', 'verdict': 'BLOCKED', 'status': None, 'error': 'URLError: <urlopen error TLS/SSL connection has been closed (EOF) (_ssl.c:992)>'} |
| `url.https://ai-2027-live-agent.onrender.com/api/health` | **BLOCKED** | declared in render.yaml; sandbox probe only (see report notes) |
| `url.https://ai-2027-studio.onrender.com/v1/studio/status` | **BLOCKED** | declared in render.yaml; sandbox probe only (see report notes) |
| `secret_scan.official` | **PROVEN** | SECRET SCAN — tracked (code) · 0 blocking · 0 warning(s)   … 11 more inside projects/ (byte-mirror; fix upstream, not here)     · projects/ai-store2/env.local:0  forbidden_file  env.local (never belongs in a commit)     · projects/cela.nimna/tests/conftest.py:15  assigned_secret  test-k…(41 chars)   |
| `secret_scan.ai-store2_redacted_here` | **PROVEN** | env.local REDACTED in this repo (D-2); upstream revocation is an owner action tracked in AUDIT.md |

## إعادة التشغيل
```bash
python3 scripts/verify_published.py
```

تفاصيل كاملة: `docs/verification-2026-11-22.json`
