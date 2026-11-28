# CELIA.SH 2027 — Architecture (Phase 1)

> Product shell: **CELIA.SH 2027** (`celia.sh2027`) with **Celia** (`Celia Agent`) as the base
> assistant. Arabic **RTL-first** experience with a full English (LTR) toggle, Manus-inspired
> fluid responsive design, connected **only** to the real, already-existing interfaces.

This document is the honest architecture record for the `workspace-ui` rebrand that ships in
this branch. It deliberately **reuses** existing systems instead of replacing them with parallel
builds, and it never invents endpoints or claims capabilities that are not wired to real services.

---

## 1. What was reused (no parallel reimplementation)

| Source | Reuse in CELIA.SH 2027 | Notes |
| --- | --- | --- |
| `workspace-ui/src/StudioWorkspace.tsx` | "Tasks → Agent Studio" panel | Real session creation, task dispatch, **SSE** event streaming via `readStudioEventStream`, **stop**, resume-ready session model, **human approvals** (`StudioApproval`). Logic unchanged; only copy lightly mentions Celia. |
| `python-2027/src/ai2027_protocol/studio.py` | `service = "ai2027-agent-studio"` | **Untouched.** The internal service id and the `BudgetGuard` (`$0` default) contract are preserved exactly. |
| `workspace-ui/src/ConnectorsWorkspace.tsx` | "Connectors" panel | Live `Connectors Hub` catalog over `/studio-api/v1/studio/connectors/catalog`. Shows configuration state only; never claims a verified live read. **Untouched.** |
| `workspace-ui/vite.config.ts` | Local credential gateway | **Untouched.** Binds to `127.0.0.1` only, `trustedLocalGateway` injects reader/operator tokens server-side, `allowedHosts: ['127.0.0.1','localhost']`. The gateway test `scripts/test_workspace_local_gateway.py` still passes against this file. |
| `kernel-2027` + Live Agent (`/api/run`, `/api/health`, `/api/capabilities`, `/api/runs`, `/api/file`) | "Tasks → Local execution · Live Agent" panel (`KernelWorkspace.tsx`) | Faithful port of the original kernel view: sandbox file ops, policy wall, `cleanup_status`, safe preview. Real `/api/*` routes only. |
| `connectors-hub` | Catalog source behind Studio API | No change; read-only display. |
| `.github/workflows/ci.yml` | `Workspace UI local gateway + cleanup contract` | **Untouched.** Still runs `npm ci → typecheck → build → test_workspace_local_gateway.py → cleanup-status.test.cjs`. |

## 2. What is new in this branch

| File | Purpose |
| --- | --- |
| `workspace-ui/src/App.tsx` | Unified CELIA.SH shell: native inline SVG `CeliaMark`, 6-section sidebar, language toggle, connection pill, ⌘K new task, `/ops/` console link. |
| `workspace-ui/src/KernelWorkspace.tsx` | Local kernel sandbox execution (ported from the old `App.tsx` kernel view). |
| `workspace-ui/src/LibraryWorkspace.tsx` | `celia_memory` stats from `StudioStatus.memory`, session log, evidence/SHA-256 per artifact. |
| `workspace-ui/src/ProjectsWorkspace.tsx` | Honest capability map (implemented / partial / planned / unavailable / blocked). No "complete" buttons for unimplemented items. |
| `workspace-ui/src/SkillsWorkspace.tsx` | Skill framework status (honest badges). |
| `workspace-ui/src/SettingsWorkspace.tsx` | `BudgetGuard` ($0 default), kernel status, loopback-only gateway info, engineering-honesty note. |
| `workspace-ui/src/capabilities.json` + `capabilities.ts` | Single source of truth for the capability/skill/project matrix and the status labels. |
| `workspace-ui/src/StatusBadge.tsx` | Status badge component. |
| `workspace-ui/src/styles.css` | CELIA.SH visual identity on top of the existing tokens (new classes appended; existing classes preserved). |
| `workspace-ui/index.html` | `CELIA.SH 2027` title/meta, RTL. |
| `workspace-ui/test/celia-contract.test.mjs` | Contract test: security contract intact, honesty matrix valid, six sections present. |
| `docs/CELIA-SH-2027-ARCHITECTURE.md` | This document. |

## 3. Capability gap vs `projects/cela.nimna` (frozen archive)

`projects/cela.nimna` and `projects/celia.sh` are **frozen archives** and are **not** modified by this
branch. They are used only as a *capability-gap reference*: several ideas there (persistent projects,
cloud browser, presentations/video/design, scheduled automation) are **not** live runtime features yet.
The UI surfaces them honestly as `planned`/`partial` — never as completed actions.

| Capability | Status | Reality |
| --- | --- | --- |
| Agent Studio sessions + SSE | implemented | Real `/studio-api` streaming |
| Local kernel sandbox ops | implemented | Real `/api/run` + `cleanup_status` |
| Evidence verification (SHA-256) | implemented | Decision record + hash |
| Human-in-the-loop approvals | implemented | `StudioApproval` |
| Connectors Hub catalog | partial | Config state only, no verified live read |
| Celia evidence memory | partial | `StudioStatus.memory` stats; admission gated |
| Loopback-only local gateway | implemented | `127.0.0.1` + `trustedLocalGateway` |
| Multilingual RTL/LTR | implemented | ar-first + en toggle |
| Budget guard ($0) | implemented | `BudgetGuard` default |
| Persistent projects | planned | Frozen `projects/` archive; no live layer |
| Cloud browser | partial | Stage-1 read-only BrowserSkill adapter only |
| Presentations / video / design | planned | Roadmap only |
| Scheduled automation | planned | Scheduling service not built |
| Public deploy | blocked | No deploy/auto-merge in this phase |

## 4. Six-phase roadmap

1. **Phase 1 — Full product shell connected to Agent Studio** (this branch): unified UI, six sections,
   real Agent Studio + kernel execution, honest capability map, architecture doc.
2. **Phase 2 — Persistent projects & session continuity** wired to a live project layer (currently `planned`).
3. **Phase 3 — Skill marketplace** from `celia-skill-factory` with real execution (currently `partial`).
4. **Phase 4 — Verified connectors read/write** behind local approval (Connectors Hub currently `partial`).
5. **Phase 5 — Scheduled automation** (currently `planned`).
6. **Phase 6 — Cloud browser / rich media generation** (currently `partial`/`planned`).

## 5. Engineering-honesty invariants (enforced by `celia-contract.test.mjs`)

- The `vite.config.ts` security contract (`host: '127.0.0.1'`, `trustedLocalGateway`, `allowedHosts`)
  must remain intact — the test fails if it is weakened.
- Every capability/skill/project entry in `capabilities.json` has a status in
  `{implemented, partial, planned, unavailable, blocked}`.
- Entries with status `planned` / `unavailable` / `blocked` **must not** carry an `action` that implies
  a completed, connected run — no deceptive "complete" buttons.
- The six nav sections exist in `App.tsx`: المهام · المشاريع · المكتبة · المهارات · الموصلات · الإعدادات والحوكمة.

## 6. Verification

Run inside `workspace-ui`:

```bash
npm ci --ignore-scripts
npm run typecheck
npm run build
npm test                 # node --test test/celia-contract.test.mjs
```

Repo-level gates that must stay green:

```bash
python scripts/secret_scan.py --all --scope code
python scripts/ci_guard.py imports
python scripts/test_workspace_local_gateway.py -v   # depends on vite.config.ts + ember_up.sh (untouched)
```

> Preview must use the loopback-bound Vite dev server (`127.0.0.1:4173`) only. The credential gateway
> is never opened on `0.0.0.0`, and the local API credentials file (`.ember-up/api-credentials.json`)
> is never exposed to the browser bundle.
