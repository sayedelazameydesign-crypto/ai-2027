# BrowserSkill Adapter — Stage 1 (`local`: Browser + Machine)

Stdlib-only, policy-gated Python 3.10+ adapter for
[`Tencent/BrowserSkill`](https://github.com/Tencent/BrowserSkill) (MIT, pinned to
`cli-v0.5.0` / `ext-v0.5.0`, Protocol `2.0`).

**Honest status:** all policy, domain, redaction, no-blind-replay, session-cleanup,
hash-chained audit, and L1 memory gates are **proven by offline execution**
(`test_adapter.py`). Live execution against a real Chrome/Edge profile requires
the owner's local machine (`probe.py` + `run_local_task.py`) — neither headless CI
runners nor network-isolated sandboxes attach a logged-in browser, and `probe.py`
reports `SKIPPED` (exit `2`), never a fake `READY`.

## Three-mode / three-stage roadmap

| Stage | Mode ID | Status | Scope |
|---|---|---|---|
| **1 / 3** | `local` | **Implemented now** | Local browser + local `bsk daemon` (`127.0.0.1:52800` + UDS/Named Pipe IPC), policy wall, SHA-256 evidence, L1 memory gate, HUD read-only status |
| **2 / 3** | `skill_mcp` | Declared & gated (`ModeNotEnabledError`) | Harness `SKILL.md` + `bsk mcp` (11 tools) + optional policy-gated Gemini planner bridge (`GEMINI_API_KEY` via `env:` + `x-goog-api-key`) |
| **3 / 3** | `remote_wss` | Declared & gated (`ModeNotEnabledError`) | `bsk daemon --mode server` over authenticated WSS + gated Render control (`RENDER_API_KEY`, non-sleeping TLS host) + HUD `ui.task_preview` |

Full plan and architecture rationale: [`docs/BROWSERSKILL-PLAN.md`](../docs/BROWSERSKILL-PLAN.md)
and [`docs/adr/0005-browserskill-adapter-boundary.md`](../docs/adr/0005-browserskill-adapter-boundary.md).

## Policy table (as enforced by `adapter.py`)

| Operation | Risk | Grant | Extra code-enforced rule |
|---|---|---|---|
| `status`, `browsers`, `sync`, `observe`, `read`, `extract`, `screenshot`, `console`, `network`, `get_html`, `tab_list` | `read` | automatic | Wrapped with `untrusted_page_data: true` + `Redactor` |
| `session_start` | `session` | automatic | Requires Protocol `2.0`; refuses silent pick when >1 browser online; defaults to `--no-focus` |
| `session_stop`, `tab_return` | `cleanup` | automatic | **Never** blocked by the HUD freeze switch; runs in `finally` |
| `navigate` | `write` | single-use | `DomainPolicy` denies `file://`, `chrome://`, `javascript:`, `data:`, metadata IPs, RFC1918, URL creds, and plaintext `http://` by default |
| `click`, `fill`, `select`, `press`, `scroll_to` | `write` | single-use | Requires live `context_id` + validated `expect` facts; `fill(..., sensitive=True)` is denied (never put passwords in `argv`) |
| `resolve_dialog` | `write` | single-use | Explicit `accept` or `dismiss`; clears `dialog_pending` lock |
| `borrow_tab` | `explicit` | single-use + exact `tab_id` | Denied in `--incognito`; browser confirmation is never bypassed |
| `upload`, `download` | `explicit` | single-use + exact filename | Cross-platform sandbox confinement (POSIX + Windows + `%2e`/`%2f` + symlink refusal) |
| `evaluate` | `explicit` | single-use + exact origin | **Disabled by default** (`allow_evaluate=False`); requires `expect` or `observation_id` even when enabled |

## Verified Browser Integration: one owner-approved task

Use the [local handoff guide](../docs/VERIFIED-BROWSER-INTEGRATION.md) when Arena
requests a test. `verified_handoff.py` accepts only one fixed loopback lab task.
It needs a dedicated, paired Chrome profile and interactive local approval of a
single expiring request. No account pages, user tabs, remote control, or automatic
uploads. Only a reviewed receipt comes back to Arena; the verifier distinguishes
receipt consistency, optional owner attestation, and actual live execution.

## Quick start on your machine (Stage 1 · Mode 1)

These are the general adapter smoke commands, not the Arena handoff. For the
handoff, follow the dedicated profile and **required** pairing instructions above.

```bash
# 1. Install pinned bsk v0.5.0 (fail-closed SHA-256 verification, no sudo, outside Git)
./scripts/install_browserskill.sh
export PATH="$HOME/.local/bin:$PATH"
./scripts/install_browserskill.sh --check
```

On Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_browserskill.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\install_browserskill.ps1 -Check
```

Install the extension in Chrome (`hhcmgoofomhgciiibhipgmgkgnoenaoi`) or Microsoft
Edge (`emacgiaaaiojkkpkddmmdfhmokgmnikg`), then start and pair locally:

```bash
# 2. Start daemon + optional verified local pairing (NEVER commit or share the link)
bsk daemon start
bsk daemon pair     # paste into Extension -> Connection settings -> Pairing link

# 3. Read-only Phase 0 probe (0 mutations asserted; writes mode-0600 gitignored report)
python3 browser_skill_adapter/probe.py --report browser_skill_adapter/phase0-report.json

# 4. Run one verified local browser task (+ optional L1 memory admission)
python3 browser_skill_adapter/run_local_task.py \
    --url https://example.com \
    --incognito \
    --memory-db browser_skill_adapter/runtime/memory.db

# 5. Inspect your own running AI-2027 stack locally
./scripts/ember_up.sh --with-browser --no-browser
python3 browser_skill_adapter/run_local_task.py \
    --url http://127.0.0.1:8090/ \
    --allow-localhost
```

## Run the offline tests (no browser, internet, or API key)

```bash
python3 browser_skill_adapter/test_adapter.py -v
python3 browser_skill_adapter/test_verified_handoff.py -v  # simulated bsk + loopback HTTP
python3 scripts/test_install_browserskill.py -v
```
