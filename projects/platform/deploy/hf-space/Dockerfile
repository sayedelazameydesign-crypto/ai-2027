# ============================================================
# Nawah Agent Platform — Hugging Face Space (Docker, FREE tier)
# Zero-card deployment target. Enforces pinned-SHA policy in-image.
# Build args are set by Space variables or edited inline below.
# ============================================================
FROM node:20-slim

# ── Pinned deployment source (AUDIT-002 policy) ──
ARG DEPLOY_SHA=a8a87e8f4e45cfd1f01a485147ea8c892c6bcfaf
ARG REPO_URL=https://github.com/sayedelazameydesign-crypto/platform.git

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN git clone "$REPO_URL" . \
    && git checkout --detach "$DEPLOY_SHA" \
    # FAIL-CLOSED: HEAD must equal requested SHA, detached
    && test "$(git rev-parse HEAD)" = "$(git rev-parse --verify ${DEPLOY_SHA}^{commit})" \
    && ! git symbolic-ref -q HEAD \
    && git rev-parse HEAD > .deploy-lock \
    && echo "deployment source locked: $(cat .deploy-lock)"

RUN npm ci --no-audit --no-fund
RUN npx playwright install --with-deps chromium
RUN npm run build

# HF Spaces serves the app on port 7860; writable dirs for runtime user
ENV PORT=7860 NODE_ENV=production
RUN mkdir -p /tmp/agent-runs && chmod 777 /tmp/agent-runs /app
EXPOSE 7860

# Secrets (set in Space Settings → Variables and secrets — NEVER here):
#   OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL,
#   TURSO_DATABASE_URL, TURSO_AUTH_TOKEN
CMD ["npx", "next", "start", "-H", "0.0.0.0", "-p", "7860"]
