# Governance — what is enforced, by what, and what only the owner can do

Rule of this file: **if a control is not enforced by code or by the platform, it is
listed as not enforced.** Aspirations live in `docs/ROADMAP-2027.md`, not here.

## Enforced in code (works today, verified)

| Control | Mechanism | Evidence |
|---|---|---|
| Tests must pass | 11 CI jobs on every PR | `gh pr checks` |
| Runtime must work over real HTTP | 15 checks against booted servers | `scripts/runtime_smoke.py` |
| No third-party imports in the kernel | AST gate with a repo-local allowlist | `scripts/ci_guard.py imports` |
| Published numbers cannot drift | CI reads the claim from `memory/README.md` | `.github/workflows/ci.yml` |
| Actions must be SHA-pinned | SBOM verifier | `python3 scripts/sbom.py --assert-pinned` |
| Static analysis on every PR | CodeQL security-extended (Python + JS/TS) | `.github/workflows/codeql.yml` |
| Dependency updates are proposed | Dependabot (actions weekly, pip monthly) | `.github/dependabot.yml` |
| Degradation is announced | Ember beat opens/closes one labelled issue | issue #34 (opened + closed 2026-11-17) |
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

## Decision log

Architecture decisions are recorded as ADRs in `docs/adr/`. A change that
contradicts an accepted ADR must add a new ADR that supersedes it — it may not
quietly diverge.

## Review policy (current reality)

Single maintainer. That is a risk, stated openly: `CODEOWNERS` names one person,
so "review" today means "CI + self-review". The mitigation is mechanical
verification (gates above), not a second human.
