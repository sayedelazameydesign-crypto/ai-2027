#!/usr/bin/env bash
# AI-2027 · one-command 24/7 install / update for a single Linux VM ($0 target).
#
#   sudo ./bootstrap.sh                         # first install (or update) from origin/main
#   sudo ./bootstrap.sh --ref <tag|sha|branch>  # deploy an exact revision
#   sudo ./bootstrap.sh --public-host hook.example.duckdns.org --acme-email you@example.com
#   sudo ./bootstrap.sh --open-firewall         # also open 80/443 in the host firewall
#   sudo ./bootstrap.sh --check                 # print health of an existing install, change nothing
#
# What it does (idempotent — safe to re-run; re-running IS the update path):
#   1. installs OS packages (python3, venv, git, sqlite3, curl, caddy)
#   2. creates the unprivileged `ai2027` system user
#   3. checks out the requested revision into /opt/ai2027 (root-owned, read-only to services)
#   4. builds the Agent Studio venv from the locked dependency set (no dev tools)
#   5. GATE: runs the offline self-checks of the new revision BEFORE touching services;
#      a failing gate restores the previous revision and exits non-zero
#   6. creates per-service env files in /etc/ai2027 ONCE (never overwrites your secrets);
#      the Live Agent operator/reader credentials are generated locally, never printed
#   7. installs the hardened systemd units + timers and (if --public-host) the Caddyfile
#   8. restarts the services and verifies every local health endpoint
#
# Exposure model: every service binds 127.0.0.1. Only Caddy listens publicly, and it
# forwards exactly two paths: /webhook and /healthz. Studio, Live Agent and Command HUD
# are reached through `ssh -L` only (see docs/DEPLOY-24-7-PLAN.md).
set -euo pipefail

# bash reads a script lazily; this one checks out a new revision of the tree it may be
# running from (/opt/ai2027/deployment/vm/bootstrap.sh). Re-exec from a private copy
# so a mid-run checkout can never change the lines still to be executed.
if [ -z "${AI2027_BOOTSTRAP_COPY:-}" ]; then
  _self="$(mktemp /tmp/ai2027-bootstrap.XXXXXX)"
  cp -- "$0" "$_self"
  chmod 0700 "$_self"
  AI2027_BOOTSTRAP_COPY="$_self" exec bash "$_self" "$@"
fi
trap 'rm -f -- "${AI2027_BOOTSTRAP_COPY}"' EXIT

REPO_URL="${AI2027_REPO_URL:-https://github.com/sayedelazameydesign-crypto/ai-2027.git}"
REF="main"
PUBLIC_HOST=""
ACME_EMAIL=""
OPEN_FIREWALL=0
CHECK_ONLY=0
UV_VERSION="${UV_VERSION:-0.13.9}"

APP=/opt/ai2027
ETC=/etc/ai2027
STATE=/var/lib/ai2027
SVC_USER=ai2027
SERVICES=(ai2027-webhook ai2027-studio ai2027-live-agent ai2027-hud)
TIMERS=(ai2027-healthcheck.timer ai2027-backup.timer)

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --ref) REF="${2:?--ref needs a value}"; shift ;;
    --public-host) PUBLIC_HOST="${2:?--public-host needs a value}"; shift ;;
    --acme-email) ACME_EMAIL="${2:?--acme-email needs a value}"; shift ;;
    --open-firewall) OPEN_FIREWALL=1 ;;
    --check) CHECK_ONLY=1 ;;
    -h|--help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
  shift
done

health_report() {
  local rc=0 name url code
  for pair in \
    "webhook|http://127.0.0.1:8080/healthz" \
    "studio|http://127.0.0.1:8790/v1/studio/status" \
    "live-agent|http://127.0.0.1:8090/api/health" \
    "hud|http://127.0.0.1:8091/api/health"; do
    name="${pair%%|*}"; url="${pair#*|}"
    code="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$url" || true)"
    if [ "$code" = "200" ]; then
      printf '  [PASS] %-11s %s\n' "$name" "$url"
    else
      printf '  [FAIL] %-11s %s -> %s\n' "$name" "$url" "${code:-no answer}"; rc=1
    fi
  done
  return "$rc"
}

if [ "$CHECK_ONLY" -eq 1 ]; then
  say "health of the existing install"
  health_report && exit 0 || exit 1
fi

[ "$(id -u)" -eq 0 ] || die "run as root (sudo)"
# shellcheck source=/dev/null
[ -r /etc/os-release ] && . /etc/os-release
case "${ID:-}" in ubuntu|debian) ;; *) die "tested on Ubuntu 24.04 / Debian 12 only (found ${ID:-unknown})" ;; esac
if [ -n "$PUBLIC_HOST" ]; then
  [[ "$PUBLIC_HOST" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$ ]] || die "invalid --public-host"
  [[ "$PUBLIC_HOST" == *.* ]] || die "--public-host must be a DNS name (e.g. name.duckdns.org)"
  [ -n "$ACME_EMAIL" ] || die "--public-host needs --acme-email (Let's Encrypt expiry notices)"
  [[ "$ACME_EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] || die "invalid --acme-email"
fi

# ── 1. packages ──────────────────────────────────────────────────────────────
say "installing OS packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv git sqlite3 curl ca-certificates >/dev/null
if [ -n "$PUBLIC_HOST" ]; then apt-get install -y -qq caddy >/dev/null; fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || die "python >= 3.11 required (Ubuntu 24.04 ships 3.12)"

# ── 2. service user ──────────────────────────────────────────────────────────
if ! id "$SVC_USER" >/dev/null 2>&1; then
  say "creating system user $SVC_USER"
  useradd --system --home-dir "$STATE" --no-create-home --shell /usr/sbin/nologin "$SVC_USER"
fi
install -d -m 0755 -o root -g root "$STATE"
install -d -m 0700 -o root -g root "$ETC"   # systemd reads env files as root
install -d -m 0700 -o root -g root /var/backups/ai2027

# ── 3. code checkout (root-owned; services only read it) ─────────────────────
PREV=""
if [ -d "$APP/.git" ]; then
  PREV="$(git -C "$APP" rev-parse HEAD)"
  say "updating checkout (current $PREV)"
  git -C "$APP" fetch --quiet --tags origin
else
  say "cloning $REPO_URL"
  git clone --quiet "$REPO_URL" "$APP"
fi
if git -C "$APP" rev-parse --verify --quiet "origin/$REF^{commit}" >/dev/null; then
  TARGET="$(git -C "$APP" rev-parse "origin/$REF^{commit}")"
else
  TARGET="$(git -C "$APP" rev-parse --verify "$REF^{commit}")" || die "unknown ref: $REF"
fi
git -C "$APP" checkout --quiet --detach "$TARGET"
chown -R root:root "$APP"
chmod -R go-w "$APP"
say "checked out $TARGET"

rollback() {
  if [ -n "$PREV" ] && [ "$PREV" != "$TARGET" ]; then
    printf '\033[1;31m==> gate failed — restoring previous revision %s\033[0m\n' "$PREV" >&2
    git -C "$APP" checkout --quiet --detach "$PREV"
  fi
  exit 1
}

# ── 4. Studio venv (locked, no dev group) ────────────────────────────────────
say "building the Agent Studio venv (uv sync --locked --no-dev)"
PY27="$APP/python-2027"
[ -d "$PY27/.venv" ] || python3 -m venv "$PY27/.venv"
[ -x "$PY27/.venv/bin/uv" ] || "$PY27/.venv/bin/pip" install -q --upgrade pip "uv==${UV_VERSION}"
( cd "$PY27" && .venv/bin/uv sync --locked --no-dev --python .venv/bin/python -q ) || rollback

# ── 5. GATE: offline self-checks of the NEW revision before any restart ──────
say "gate: offline self-checks ($0, no network)"
gate() {
  cd "$APP"
  local scratch; scratch="$(mktemp -d)"
  trap 'rm -rf "$scratch"' RETURN
  # Run as the service user against a scratch dir so the gate proves the code works
  # without write access to /opt/ai2027.
  runuser -u "$SVC_USER" -- env -i PATH=/usr/bin:/bin HOME="$scratch" PYTHONDONTWRITEBYTECODE=1 \
    python3 scripts/whatsapp_cloud_rehearsal.py --assert &&
  runuser -u "$SVC_USER" -- env -i PATH=/usr/bin:/bin HOME="$scratch" PYTHONDONTWRITEBYTECODE=1 \
    python3 scripts/free_stack_runner.py --self-check &&
  runuser -u "$SVC_USER" -- env -i PATH=/usr/bin:/bin HOME="$scratch" PYTHONDONTWRITEBYTECODE=1 \
    "$PY27/.venv/bin/python" -c 'import ai2027_protocol.studio' &&
  runuser -u "$SVC_USER" -- env -i PATH=/usr/bin:/bin HOME="$scratch" PYTHONDONTWRITEBYTECODE=1 \
    python3 -c "import sys; sys.path.insert(0,'projects/agi-system'); from core.kernel import Kernel"
}
gate || rollback

# ── 6. env files (created once; existing files are never overwritten) ───────
make_env() {  # make_env <name>  — copies deployment/vm/env/<name>.env.example
  local dst="$ETC/$1.env"
  if [ ! -e "$dst" ]; then
    install -m 0600 -o root -g root "$APP/deployment/vm/env/$1.env.example" "$dst"
    say "created $dst (edit it to add secrets, then: systemctl restart ai2027-$1)"
  fi
}
make_env webhook
make_env studio
make_env backup

if [ ! -e "$ETC/live-agent.env" ]; then
  say "generating Live Agent operator + reader credentials (stored root-only, never printed)"
  umask 077
  python3 - "$ETC" <<'PY'
import hashlib, json, os, secrets, sys
etc = sys.argv[1]
write_token = secrets.token_urlsafe(48)
read_id, read_secret = "operator", secrets.token_urlsafe(32)
hashes = {read_id: hashlib.sha256(read_secret.encode()).hexdigest()}
cred = os.path.join(etc, "live-agent-operator.json")
fd = os.open(cred, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as fh:
    json.dump({"read_id": read_id, "read_secret": read_secret,
               "write_token": write_token, "read_hashes": hashes}, fh)
    fh.write("\n")
env = os.path.join(etc, "live-agent.env")
fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as fh:
    fh.write("# generated by bootstrap.sh — plaintext reader secret is in live-agent-operator.json (root only)\n")
    fh.write("AI2027_READ_ONLY=0\n")
    fh.write(f"AI2027_WRITE_TOKEN={write_token}\n")
    fh.write(f"AI2027_READ_TOKEN_HASHES={json.dumps(hashes, separators=(',', ':'))}\n")
PY
  umask 022
fi

# ── 7. units, timers, Caddy ──────────────────────────────────────────────────
say "installing systemd units"
install -m 0755 -o root -g root "$APP/deployment/vm/healthcheck.sh" /usr/local/sbin/ai2027-healthcheck
install -m 0755 -o root -g root "$APP/deployment/vm/backup.sh" /usr/local/sbin/ai2027-backup
for unit in "$APP"/deployment/vm/systemd/*.service "$APP"/deployment/vm/systemd/*.timer; do
  install -m 0644 -o root -g root "$unit" /etc/systemd/system/
done
systemctl daemon-reload

if [ -n "$PUBLIC_HOST" ]; then
  say "configuring Caddy for https://$PUBLIC_HOST (only /webhook and /healthz are public)"
  sed -e "s|__AI2027_PUBLIC_HOST__|$PUBLIC_HOST|g" -e "s|__AI2027_ACME_EMAIL__|$ACME_EMAIL|g" \
    "$APP/deployment/vm/Caddyfile" > /etc/caddy/Caddyfile.new
  caddy validate --config /etc/caddy/Caddyfile.new --adapter caddyfile >/dev/null \
    || die "generated Caddyfile does not validate (left at /etc/caddy/Caddyfile.new)"
  mv /etc/caddy/Caddyfile.new /etc/caddy/Caddyfile
  systemctl enable --quiet caddy
  systemctl reload-or-restart caddy
fi

if [ "$OPEN_FIREWALL" -eq 1 ]; then
  say "opening tcp/80 and tcp/443 in the host firewall"
  if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
    ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
  else
    # Oracle's Ubuntu images ship a REJECT rule in INPUT; insert before it.
    for port in 80 443; do
      iptables -C INPUT -p tcp -m state --state NEW --dport "$port" -j ACCEPT 2>/dev/null \
        || iptables -I INPUT 5 -p tcp -m state --state NEW --dport "$port" -j ACCEPT
    done
    if command -v netfilter-persistent >/dev/null; then netfilter-persistent save >/dev/null; fi
  fi
  echo "    (the cloud provider's own firewall / security list must allow 80+443 too)"
fi

# ── 8. (re)start and verify ──────────────────────────────────────────────────
say "starting services"
systemctl enable --quiet "${SERVICES[@]}" "${TIMERS[@]}"
systemctl restart "${SERVICES[@]}"
systemctl start "${TIMERS[@]}"

say "verifying local health"
ok=0
for _ in $(seq 1 30); do
  if health_report >/dev/null 2>&1; then ok=1; break; fi
  sleep 1
done
health_report || true
if [ "$ok" -ne 1 ]; then
  echo "    inspect: journalctl -u 'ai2027-*' -n 50 --no-pager" >&2
  if [ -n "$PREV" ] && [ "$PREV" != "$TARGET" ]; then
    git -C "$APP" checkout --quiet --detach "$PREV"
    systemctl restart "${SERVICES[@]}"
    die "new revision failed its health check — restored $PREV and restarted"
  fi
  die "services are not healthy"
fi

say "deployed $TARGET"
if [ -n "$PUBLIC_HOST" ]; then
  echo "    public:  https://$PUBLIC_HOST/healthz   (YCloud webhook URL: https://$PUBLIC_HOST/webhook)"
fi
echo "    private: ssh -L 8790:127.0.0.1:8790 -L 8090:127.0.0.1:8090 -L 8091:127.0.0.1:8091 <vm>"
