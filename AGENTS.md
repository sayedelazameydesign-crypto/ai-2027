# AGENTS.md — AI-2027 Agent Working Instructions

This file is the canonical instruction set for **coding agents working inside
this repository**. `CLAUDE.md` is a compatibility shim that points here; do not
duplicate these rules into it.

Operating-mind instructions for the *runtime agent* (the system that executes
goals inside the platform) live separately under [`prompts/agent/`](prompts/agent/).
**Do not mix the two layers.** These instructions govern how software is built
here; the prompts govern how a goal is understood, planned and verified at run
time.

## 1. Read this before touching anything

AI-2027 is a multi-layer platform, not one script. The layers that matter:

| Layer | Location | What it is |
| --- | --- | --- |
| Kernel | `kernel-2027/` | Stdlib-only policy, approvals, audit, sandbox. The authority on writes. |
| Protocol / runtime | `python-2027/src/ai2027_protocol/` | The Free-First router, planner, agent, tools, verification. **Primary development path.** |
| Memory | `memory/` | Evidence-gated L1 store (stdlib `sqlite3` + deterministic lexical embedding). |
| Live agent | `live-agent/` | Deployment wrapper around the kernel. |
| Tools & adapters | `tools/`, `browser_skill_adapter/`, `connectors-hub/`, `command-hud/` | Capability surfaces. |
| Projects | `projects/` | Standalone sub-projects and archives. Treat archives as read-only evidence, not live services. |

`python-2027` is the primary path for development and integration. Reuse its
router, planner, agent and verification code before writing a new one. Do not
create a second router beside `FreeFirstRouter`, and do not duplicate the
kernel's memory or policy logic.

## 2. Evidence before claims — the project's central rule

A claim is only as good as the check that produced it.

- **Never claim a result you did not observe.** If you did not run it, it is not
  a result.
- **Never describe a simulated, deterministic or offline path as a live model
  result.** Name the capability that actually served the step.
- **A file being written is not a result.** Verification re-reads it
  independently. The writer and the verifier must not share code paths.
- **A passing simulation is not proof of a live run.** Live provider behaviour
  is proven by a live probe with its captured evidence, not by a mock.
- **Do not add a dependency to "make deployment work"** without a decision
  record. The root `requirements.txt` is deliberately empty and that emptiness
  is a measured claim, guarded by `scripts/ci_guard.py imports`.

When you are unsure whether something is real, say what you checked and what you
did not. Honest partial results are accepted; polished guesses are not.

## 3. Zero measured cost

`MAX_SPEND_USD = 0` is a policy enforced in code by `BudgetGuard`, not a
promise in a document.

- An API key is **never** evidence that a provider is free.
- Providers whose cost cannot be proven are classified `UNKNOWN_COST` and
  blocked. `NON_RECLASSIFIABLE_PROVIDERS` may not be labelled free by a caller.
- Free-tier model IDs are allowlisted explicitly and pruned when a provider
  retires them — a stale allowlist entry is how a "free" call starts failing
  with 404/410.
- New providers are admitted only through the existing classification and
  budget tests.

## 4. Engineering standards

Full standards live in [`docs/agent/ENGINEERING-STANDARDS.md`](docs/agent/ENGINEERING-STANDARDS.md).
The non-negotiables:

- **Stdlib first.** Kernel, memory, tools and deployment layers are stdlib-only.
  Their imports are checked by AST gates in CI. `python-2027` is the only place
  with declared third-party dependencies (FastAPI, httpx, Pydantic, structlog).
- **Typed and linted.** `python-2027` runs `ruff` and `mypy --strict` in CI.
  New code in that package must pass both.
- **Tests travel with the change.** A change without a test is incomplete.
  Tests must be hermetic: no network, no credentials, no live provider, unless
  the test is explicitly a live probe with its own gate.
- **Fail-closed on security.** Anything sensitive defaults to denied. A
  denial is a result, not an error to be worked around.
- **Docs are claims too.** A documented capability must be implemented, or the
  doc must say it is declared/planned. Update docs in the same change.

## 5. Working in this repository

- **One branch per session.** Do all work on the current branch. Push only to
  it. Never switch or create branches unless explicitly asked, and never push to
  `main` directly.
- **Commits are reviewable units.** A commit message states what changed and
  why. A PR is one coherent change with tests, not a pile of unrelated edits.
- **Never modify `main` from a feature branch's diff by accident.** Check
  `git status` and `git diff --stat` before pushing.
- **Archival code is not live.** Anything under `projects/agi-system` and other
  archive paths is frozen. Read it for contracts; do not depend on it as a
  running service.
- **Do not silently rewrite history** or force-push to shared branches.

## 6. Verification before declaring done

Before you report completion, run the relevant gates and report their real
output:

- the module's own unit tests;
- the stdlib-only AST gate for any layer that requires it;
- a compile check (`python -m compileall`);
- the routing/budget/verification tests that cover the changed behaviour.

If a gate cannot run in this environment (a missing optional dependency, no
network), say so explicitly and say what you ran instead. Do not report a gate
as passed when it was not run.

## 7. Prompt and instruction files

`prompts/agent/*.md` are the operating mind. They are loaded and integrity
checked by `python-2027/src/ai2027_protocol/prompts.py`; the loading contract and
the required-section rules are tested in `tests/test_prompt_loader.py` and
`python-2027/tests/test_prompt_loader.py`.

- Keep each prompt file's front matter (`name`, `version`, `role`, `required`)
  accurate. Bump `version` when behaviour changes.
- Do not store reasoning traces, private deliberation or unverified claims as
  memory or as prompt content.
- Do not put coding-agent instructions inside `prompts/agent/`, and do not put
  run-time operating instructions into `AGENTS.md`.

## 8. When you are blocked

Say so plainly, with the specific obstacle:

- the capability is declared but not implemented;
- the check cannot run here and no offline equivalent exists;
- the goal requires an approval you cannot grant;
- the goal conflicts with a hard boundary (budget, sandbox, kernel policy).

Offer the smallest change that unblocks the goal, and the evidence needed to
justify it. Do not improvise around a boundary to appear productive.
