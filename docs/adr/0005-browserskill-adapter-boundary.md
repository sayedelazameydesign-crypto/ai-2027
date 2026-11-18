# ADR 0005 — Integrate Tencent/BrowserSkill as a policy-gated adapter, never a vendored clone

- **Status**: Accepted
- **Date**: 2026-11-18
- **Context**: `Tencent/BrowserSkill` (MIT, inspected at `aeebd100`, release
  `cli-v0.5.0` / `ext-v0.5.0`, Protocol `2.0`) connects an agent to the owner's
  real, logged-in Chrome or Microsoft Edge through a single `bsk` binary
  (CLI + stdio MCP + loopback/WSS daemon) and an MV3 extension. Cloning its
  ~40 MB Rust/TypeScript tree into this repository would break four enforced
  invariants: `projects/` is a byte-mirror of 48 owner repos checked by
  `scripts/verify_archive_identity.py`; `projects/agi-system/core` and
  `live-agent/` are deterministic and AST-gated to the Python stdlib (ADR 0002);
  CodeQL scans every tracked JS/TS file; and a logged-in browser is the opposite
  of a deterministic filesystem sandbox.

## Decision

- `Tencent/BrowserSkill` is **not** cloned or vendored into the repository. The
  `bsk` CLI is installed outside Git (`~/.local/bin/bsk`) at a pinned version
  (`0.5.0`) via `scripts/install_browserskill.sh` / `.ps1`, with **fail-closed**
  SHA-256 verification (refusing missing or mismatched checksums).
- All repository integration lives in `browser_skill_adapter/` (Python 3.10+,
  stdlib only, AST-gated in CI), alongside `render_control_adapter/`:
  1. **Reads are automatic inside the Agent Window; mutations require a
     single-use approval grant**; boundary-crossing operations (`borrow_tab`,
     `upload`, `download`, `debug_rule`, `evaluate`) require exact target
     confirmation, and `evaluate` is disabled by default.
  2. **Domain, path, and environment walls execute before `bsk` runs**:
     `file://`, browser-internal schemes, URL credentials, metadata IPs and
     private networks are denied by default; repository credentials
     (`GEMINI_API_KEY`, `RENDER_API_KEY`, `GITHUB_TOKEN`, …) are stripped from
     the `bsk` subprocess environment; `BSK_AUTO_START=0` is always set.
  3. **An executed browser action is not `VERIFIED`**: `effect_state == "unknown"`,
     `timeout`, and `dialog_pending` become `INCONCLUSIVE` and block blind
     replay until `sync` or dialog resolution. `VERIFIED` requires independent
     post-checks, mandatory `session stop`, and a redacted on-disk evidence
     artifact whose recomputed SHA-256 matches — the exact contract enforced by
     `memory/store.py` (L1).
  4. **Pairing material is treated as a credential**: `scripts/secret_scan.py`
     refuses `bsk-local.v1.*`, `bsk-auth.*`, `ws(s)://…#<fragment>` pairing URLs,
     and `local-pairings.json` before commit and in CI.
- All three BrowserSkill operating modes roll out across **three gated stages**
  (`docs/BROWSERSKILL-PLAN.md`):
  - **Stage 1 (`local`)** — browser and `bsk` daemon on the owner's machine.
  - **Stage 2 (`skill_mcp`)** — harness `SKILL.md` + `bsk mcp` + optional
    policy-gated `GEMINI_API_KEY` planner bridge (gated on Stage 1 live proof).
  - **Stage 3 (`remote_wss`)** — `bsk daemon --mode server` over authenticated
    WSS + gated Render control via `RENDER_API_KEY` + HUD task preview (gated on
    a non-sleeping TLS endpoint and Stage 2 proof).
  Unenabled modes raise `ModeNotEnabledError` rather than simulating support.

## Consequences

- `projects/`, `projects/agi-system/core`, `live-agent/`, `requirements.txt`,
  `Dockerfile`, and the 15-check `runtime_smoke.py` contract stay untouched.
- Headless CI runners and network-restricted sandboxes cannot attach a real
  Chrome profile: `browser_skill_adapter/probe.py` exits `2` (`SKIPPED`), never
  `0`, and offline tests verify the policy, redaction, no-replay, cleanup and L1
  memory contracts against Protocol `2.0` fixtures.
- Live proof of Stage 1 requires one owner run on the machine holding the
  browser (`probe.py` + `run_local_task.py`). Until that report exists, Stage 1
  is declared *adapter-proven offline, live browser verification pending*.
