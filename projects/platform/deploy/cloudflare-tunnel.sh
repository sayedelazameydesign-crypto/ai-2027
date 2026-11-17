#!/usr/bin/env bash
# ============================================================
# Cloudflare Tunnel — zero-port, zero-cost HTTPS for the platform
#
# Mode A (no account, no domain — instant public demo URL):
#   bash deploy/cloudflare-tunnel.sh quick
#
# Mode B (production: your domain on Cloudflare, permanent tunnel):
#   bash deploy/cloudflare-tunnel.sh setup app.yourdomain.com
#
# Benefits over open ports + Caddy:
#   - No VCN ingress rules, no iptables — outbound-only connection
#   - Origin IP hidden behind Cloudflare (DDoS shield included)
#   - Free auto-renewing HTTPS at the edge
# ============================================================
set -euo pipefail

MODE="${1:-quick}"
HOSTNAME_ARG="${2:-}"

install_cloudflared() {
  if command -v cloudflared >/dev/null; then return; fi
  echo "═══ Installing cloudflared ═══"
  ARCH=$(dpkg --print-architecture 2>/dev/null || echo amd64)
  curl -fsSL -o /tmp/cloudflared.deb \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}.deb"
  sudo dpkg -i /tmp/cloudflared.deb
}

install_cloudflared

if [ "$MODE" = "quick" ]; then
  echo "═══ Quick Tunnel (ephemeral demo URL, no account needed) ═══"
  echo "⚠️  Anyone with the URL can use the platform — share carefully, Ctrl+C to stop."
  exec cloudflared tunnel --url http://localhost:3000
fi

if [ "$MODE" = "setup" ]; then
  [ -n "$HOSTNAME_ARG" ] || { echo "Usage: $0 setup app.yourdomain.com"; exit 1; }
  echo "═══ 1/3 Login (opens a Cloudflare auth URL — approve your domain) ═══"
  cloudflared tunnel login
  echo "═══ 2/3 Create tunnel + DNS route ═══"
  cloudflared tunnel create platform || true
  cloudflared tunnel route dns platform "$HOSTNAME_ARG"
  mkdir -p ~/.cloudflared
  TUNNEL_ID=$(cloudflared tunnel list | awk '/platform/{print $1; exit}')
  tee ~/.cloudflared/config.yml >/dev/null <<CFG
tunnel: $TUNNEL_ID
credentials-file: $HOME/.cloudflared/$TUNNEL_ID.json
ingress:
  - hostname: $HOSTNAME_ARG
    service: http://localhost:3000
  - service: http_status:404
CFG
  echo "═══ 3/3 Run as a service (survives reboot) ═══"
  sudo cloudflared service install
  sudo systemctl enable --now cloudflared
  echo "✓ LIVE at https://$HOSTNAME_ARG — no open ports, origin IP hidden."
  exit 0
fi

echo "Unknown mode: $MODE (use 'quick' or 'setup')" && exit 1
