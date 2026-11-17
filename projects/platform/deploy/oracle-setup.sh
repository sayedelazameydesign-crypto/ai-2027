#!/usr/bin/env bash
# ============================================================
# Oracle Cloud Always Free — one-shot production setup
# Target: Ubuntu 24.04 on VM.Standard.A1.Flex (ARM, 4 OCPU / 24GB)
# Usage:  DEPLOY_SHA=<sha> bash deploy/oracle-setup.sh [yourdomain.com]
# Policy: deployment source is a PINNED SHA (detached checkout).
#         NO `git pull` on this host — redeploys pass a new DEPLOY_SHA.
# Cost:   $0 — Always Free tier + Let's Encrypt SSL via Caddy
# ============================================================
set -euo pipefail

DOMAIN="${1:-}"
DEPLOY_SHA="${DEPLOY_SHA:-}"
APP_DIR="/opt/platform"
REPO_URL="https://github.com/sayedelazameydesign-crypto/platform.git"

if [ -z "$DEPLOY_SHA" ]; then
  echo "✗ DEPLOY_SHA is required (deployment-source lock policy — AUDIT-001 aftermath)."
  echo "  Usage: DEPLOY_SHA=8b65e93... bash deploy/oracle-setup.sh [domain]"
  exit 1
fi

echo "═══ 1/6 System packages ═══"
sudo apt-get update -y
sudo apt-get install -y git curl ca-certificates

echo "═══ 2/6 Node.js 20.x ═══"
if ! command -v node >/dev/null || [[ "$(node -v)" != v20* ]]; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi

echo "═══ 3/6 App checkout — PINNED SHA (no moving branch) ═══"
sudo mkdir -p "$APP_DIR" && sudo chown "$USER" "$APP_DIR"
if [ ! -d "$APP_DIR/.git" ]; then git clone "$REPO_URL" "$APP_DIR"; fi
cd "$APP_DIR"
git fetch origin
git checkout --detach "$DEPLOY_SHA"

# ── FAIL-CLOSED: HEAD must equal the requested SHA exactly ──
WANT=$(git rev-parse --verify "${DEPLOY_SHA}^{commit}")
ACTUAL=$(git rev-parse HEAD)
if [ "$ACTUAL" != "$WANT" ]; then
  echo "✗ BLOCK: HEAD ($ACTUAL) != DEPLOY_SHA ($WANT)"; exit 1
fi
# Detached HEAD makes any future `git pull` fail by itself
# ("You are not currently on a branch") — inherent mutable-update rejection.
if git symbolic-ref -q HEAD >/dev/null; then
  echo "✗ BLOCK: checkout is on a branch (moving ref) — must be detached"; exit 1
fi
# Deployment lock: smoke-test.sh re-verifies HEAD against this before gating.
echo "$ACTUAL" > "$APP_DIR/.deploy-lock"
{
  echo "# Deployment Evidence (setup phase)"
  echo "deployed_sha: $ACTUAL"
  echo "requested:    $DEPLOY_SHA"
  echo "detached:     yes"
  echo "timestamp:    $(date -u +%FT%TZ)"
} > "/tmp/DEPLOYMENT-EVIDENCE-$(date +%Y%m%d-%H%M%S).txt"
echo "✓ deployment source locked: $ACTUAL (detached, .deploy-lock written)"
npm ci --no-audit --no-fund
npx playwright install chromium
sudo npx playwright install-deps chromium
if [ ! -f .env ]; then
  cp .env.example .env
  echo "⚠️  Edit $APP_DIR/.env with your Groq key (OPENAI_API_KEY / OPENAI_BASE_URL) then re-run."
fi
npm run build

echo "═══ 4/6 systemd service (auto-restart, survives reboot) ═══"
sudo tee /etc/systemd/system/platform.service >/dev/null <<UNIT
[Unit]
Description=Agent Platform (Next.js)
After=network.target

[Service]
WorkingDirectory=$APP_DIR
ExecStart=$(command -v npm) start
Restart=always
RestartSec=5
User=$USER
Environment=NODE_ENV=production
EnvironmentFile=$APP_DIR/.env

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now platform

echo "═══ 5/6 Caddy reverse proxy + free auto-SSL ═══"
# Oracle Ubuntu images ship with restrictive iptables (only SSH open) —
# the #1 cause of "site unreachable". Open web ports at the OS level too:
sudo iptables -I INPUT 5 -p tcp --dport 80 -j ACCEPT 2>/dev/null || true
sudo iptables -I INPUT 5 -p tcp --dport 443 -j ACCEPT 2>/dev/null || true
sudo iptables -I INPUT 5 -p tcp --dport 3000 -j ACCEPT 2>/dev/null || true
sudo apt-get install -y iptables-persistent >/dev/null 2>&1 || true
sudo netfilter-persistent save 2>/dev/null || true

if [ -n "$DOMAIN" ]; then
  sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
  sudo apt-get update -y && sudo apt-get install -y caddy
  sudo tee /etc/caddy/Caddyfile >/dev/null <<CADDY
$DOMAIN {
    reverse_proxy localhost:3000
}
CADDY
  sudo systemctl restart caddy
  echo "✓ SSL live at https://$DOMAIN (Let's Encrypt, auto-renews)"
else
  echo "↷ No domain passed — app on :3000. Open Oracle VCN ingress for 80/443/3000."
fi

echo "═══ 6/6 Health check ═══"
sleep 3
curl -fsS http://localhost:3000/api/health && echo " ✓ platform is LIVE"
echo
echo "Done. Logs: journalctl -u platform -f"
