# Manual GitHub Settings — re-verified 2026-11-17

**The situation changed since the first checklist.** Two claims in the earlier
version were wrong, so this file corrects them with evidence:

| Earlier claim | Verified reality |
|---|---|
| "GitHub Pages is not enabled — you must enable it" | ❌ **Already enabled.** `GET /repos/.../pages` returns `build_type: "workflow"`, `html_url: https://sayedelazameydesign-crypto.github.io/ai-2027/`, `https_enforced: true`. No manual Settings → Pages step is needed. |
| "The site is live, just not linked" | ❌ **It never deployed.** The generated `jekyll-gh-pages.yml` built Jekyll from the repo root `./`, not from `website/`. All 4 pushes to `main` failed at *Build with Jekyll*. Fixed in code by `.github/workflows/pages.yml`, which uploads `website/` as a static artifact. |
| "Integration token is read-only for admin settings" | ✅ **Confirmed.** `permissions.admin: true` in the repo payload is the *user's* permission, but the GitHub App installation token has `X-OAuth-Scopes: (empty)` and `X-Accepted-Github-Permissions: metadata=read`. `PATCH /repos/...` and `PUT /repos/.../topics` both return **403 "Resource not accessible by integration"** (re-tested today). |

## ✅ Now fully automatic (no clicks needed)

- **Pages source / build** — workflow-based deploy from `website/` on every push to `main`.
- **Live demo** — `website/demo.html` + `website/kernel-demo.js` (zero network calls, 12 contract tests in CI).
- **CI / benchmarks / wheel** — `.github/workflows/ci.yml`.
- **Community seeding** — issues #5, #6, #7 (`good first issue`) already open.

## ⚠️ Still genuinely manual — 4 clicks, ~60 seconds (403 documented)

### 1. Settings → General → About → ⚙️ — Description
```
Open ecosystem of 48 AI agent systems, platforms & tools — Evidence-First agents that prove their work. منظومة مفتوحة لوكلاء ذكاء اصطناعي يثبتون عملهم بالأدلة.
```

### 2. Same dialog — Topics
```
ai-agents  agent-framework  agentic-ai  autonomous-agents  llm  open-source  python  typescript  verification  deterministic  arabic
```

### 3. Same dialog — Website
```
https://sayedelazameydesign-crypto.github.io/ai-2027/
```

### 4. Settings → General → Features — ✅ **Discussions**
Unlocks the community channel for contributors from the global launch.

### 5. Optional — Social preview (1280×640)
Upload in Settings → General. Ask and I'll generate one.

---

## Proof commands (read-only, safe to re-run)

```bash
gh api repos/sayedelazameydesign-crypto/ai-2027 --jq '{permissions,description,topics,homepage,has_discussions}'
gh api repos/sayedelazameydesign-crypto/ai-2027/pages --jq '{build_type,html_url,https_enforced}'
gh run list --workflow=pages.yml --limit 5
```

> Done with the 4 clicks? Tell me and I'll set the readiness gate in
> `launch/README.md` to **metadata complete**. Until then the gate stays honest.
