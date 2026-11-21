# Governance — what is enforced, by what, and what only the owner can do

Rule of this file: **if a control is not enforced by code or by the platform, it is
listed as not enforced.** Aspirations live in `docs/ROADMAP-2027.md`, not here.

## Enforced in code (works today, verified)

| Control | Mechanism | Evidence |
|---|---|---|
| Tests must pass | 15 CI jobs on every PR | `gh pr checks` |
| Runtime must work over real HTTP | 17 checks against booted servers | `scripts/runtime_smoke.py` |
| No third-party imports in the kernel | AST gate with a repo-local allowlist | `scripts/ci_guard.py imports` |
| Published numbers cannot drift | CI reads the claim from `memory/README.md` | `.github/workflows/ci.yml` |
| Actions must be SHA-pinned | SBOM verifier | `python3 scripts/sbom.py --assert-pinned` |
| Static analysis on every PR | CodeQL security-extended (Python + JS/TS) | `.github/workflows/codeql.yml` |
| Dependency updates are proposed | Dependabot (actions weekly, pip monthly) | `.github/dependabot.yml` |
| Degradation is announced | Ember beat opens/closes one labelled issue | issues #34, #39 (opened + closed 2026-11-17) |
| Escalation is real, not decorative | stage 3 adds `escalation/s3` + code-owner mention | issue #39 (3 degraded runs, then closed by a healthy beat) |
| **Silent degradation is refused** | `scripts/health_gate.py` — 11 checks, 3 escalation stages | `python3 scripts/test_health_gate.py` (24 tests) |
| The retry cannot inflate the record | history row written once by the gate (`--commit-beat`) | `test_19b` (idempotent on `beat_ts`) |
| Recovery is one command and timed | `scripts/ember_up.sh` | CI boots the isolated local stack and rechecks all 17 HTTP contracts |
| The 60 s task promise is falsifiable | `scripts/run_task.py --budget 60` + CI `recovery` job | `python3 scripts/test_run_task.py` (7 tests) |
| Ownership is declared | `CODEOWNERS` | `.github/CODEOWNERS` |
| Releases carry proof | tag → tests → wheel + SBOM + SHA256SUMS | `.github/workflows/release.yml` |

## NOT enforced — needs repository admin (the automation token gets HTTP 403)

Attempted on 2026-11-17 and refused:

```
$ gh api -X PUT repos/…/branches/main/protection --input - <<< '{…}'
{"message":"Resource not accessible by integration","status":"403"}
```

Run these yourself (30 seconds), or enable the same thing in
**Settings → Branches → Add branch protection rule**:

```bash
gh api -X PUT repos/sayedelazameydesign-crypto/ai-2027/branches/main/protection \
  -H "Accept: application/vnd.github+json" --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "Tests · Python 3.11",
      "Runtime smoke · real servers over HTTP",
      "L1 memory · admission gate + measured retrieval honesty"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": { "required_approving_review_count": 0 },
  "required_conversation_resolution": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "restrictions": null
}
JSON
```

| Not enforced | Why it matters | Where to fix |
|---|---|---|
| Branch protection on `main` | nothing currently stops a direct red push | command above |
| Secret scanning + push protection | a leaked key reaches history before anyone sees it | Settings → Code security → Secret scanning → Push protection |
| Private vulnerability reporting | `SECURITY.md` gives an address, not an encrypted channel | Settings → Code security → Private reporting |
| Environments for releases | anyone with write access can cut a release | Settings → Environments → `release` with required reviewers |
| Release immutability | a published release can be edited silently | Settings → Releases → Immutable releases |
| Hosted second mirror (Vercel) | one provider is still a single point of failure | measured 2026-11-17: `create_project` → *"Access denied. Your token may not have the required permissions."* — re-authorise the Vercel app, then `vercel link` + `vercel --prod` in `website/` |
| Supabase `beats` table | external readers cannot see the beat series without GitHub | 4 projects on the account, **all `INACTIVE`** (paused): restoring or creating one is a cost decision, not a code change |

## Decision log

Architecture decisions are recorded as ADRs in `docs/adr/`. A change that
contradicts an accepted ADR must add a new ADR that supersedes it — it may not
quietly diverge.

## Review policy (current reality)

Single maintainer. That is a risk, stated openly: `CODEOWNERS` names one person,
so "review" today means "CI + self-review". The mitigation is mechanical
verification (gates above), not a second human.
