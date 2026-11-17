# Manual GitHub Settings — re-verified 2026-11-17

**The situation changed since the first checklist.** Two claims in the earlier
version were wrong, so this file corrects them with evidence:

| Earlier claim | Verified reality |
|---|---|
| "GitHub Pages is not enabled — you must enable it" | ❌ **Already enabled.** `GET /repos/.../pages` returns `html_url: https://sayedelazameydesign-crypto.github.io/ai-2027/`, `https_enforced: true`. No *enablement* step is needed. ⚠️ The `build_type` field has since flipped to `legacy` (source `main /`) — see §6 below. |
| "The site is live, just not linked" | ❌ **It never deployed.** The generated `jekyll-gh-pages.yml` built Jekyll from the repo root `./`, not from `website/`. All 4 pushes to `main` failed at *Build with Jekyll*. Fixed in code by `.github/workflows/pages.yml`, which uploads `website/` as a static artifact. |
| "One manual step remains: Pages source `main` + `/website`" | ⚠️ **Not required for the launch.** Our workflow deploy succeeded twice (`44073888821` on push, `44075625763` on dispatch) and the URL was fetched over HTTPS. The branch-source setting only produces a *redundant* failing build — see §6. |
| "Integration token is read-only for admin settings" | ✅ **Confirmed.** `permissions.admin: true` in the repo payload is the *user's* permission, but the GitHub App installation token has `X-OAuth-Scopes: (empty)` and `X-Accepted-Github-Permissions: metadata=read`. `PATCH /repos/...` and `PUT /repos/.../topics` both return **403 "Resource not accessible by integration"** (re-tested today). |

## ✅ Now fully automatic (no clicks needed)

- **Pages source / build** — workflow-based deploy from `website/` on every push to `main`.
  **Verified live:** run `44073888821` → success; `GET /pages/builds` → `status: "built"`,
  `error: null` at 2026-11-17 11:25:47 UTC; `/ai-2027/` and `/ai-2027/demo.html` fetched
  successfully over HTTPS. The 4 earlier Jekyll failures are gone.
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

### 6. Optional cleanup — stop the redundant failing "pages build and deployment" runs
Discovered after merging #10/#11, documented so the Actions tab is not
misread. With every push to `main`, GitHub ALSO runs its internal
`pages-build-deployment` workflow in **legacy** Jekyll mode against the repo
root (`build_type: legacy`, `source: {branch: main, path: "/"}`). Both runs
(`44073889835`, `44075191517`) failed at *Build with Jekyll*.

**This does NOT break the site.** Our `Deploy static site to GitHub Pages`
workflow deploys the static `website/` artifact independently and succeeded;
`/ai-2027/` and `/ai-2027/demo.html` were re-fetched successfully after both
merges. A failed legacy build does not overwrite a successful deployment.

Switching the source back to **GitHub Actions** would remove that red X:
`PUT /repos/.../pages {"build_type":"workflow"}` → **403 Resource not
accessible by integration** (re-verified). So it is one more owner click:

> **Settings → Pages → Build and deployment → Source → `GitHub Actions`**
> (or, alternatively, `Deploy from a branch` → `main` + `/website`; we added
> `website/.nojekyll` so that mode copies files verbatim without Jekyll).

Either choice is safe. Do **not** point the branch source at `/` again — that
is exactly the root-level Jekyll build that failed 4 times before.

---

## Proof commands (read-only, safe to re-run)

```bash
gh api repos/sayedelazameydesign-crypto/ai-2027 --jq '{permissions,description,topics,homepage,has_discussions}'
gh api repos/sayedelazameydesign-crypto/ai-2027/pages --jq '{build_type,html_url,https_enforced}'
gh run list --workflow=pages.yml --limit 5
```

> Done with the 4 clicks? Tell me and I'll set the readiness gate in
> `launch/README.md` to **metadata complete**. Until then the gate stays honest.
