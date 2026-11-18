#!/usr/bin/env sh
# install_browserskill.sh — fail-closed installer for the pinned bsk CLI (Stage 1 · Mode 1).
#
# Why we do not pipe upstream `install.sh` straight into `sh`:
#   1. upstream tracks floating `latest`; this repository pins `0.5.0` (Protocol 2.0);
#   2. upstream warns and installs anyway if `version.json` or `sha256sum` is absent;
#      this script is FAIL-CLOSED — no verified 64-hex SHA-256, no binary on disk;
#   3. upstream edits ~/.bashrc / ~/.zshrc automatically; this script never touches
#      shell profiles and never invokes sudo;
#   4. the binary lives outside Git ($HOME/.local/bin/bsk), keeping the repository
#      zero-dependency (see docs/adr/0005-browserskill-adapter-boundary.md).
#
# Usage:
#   ./scripts/install_browserskill.sh              # install pinned bsk (0.5.0)
#   ./scripts/install_browserskill.sh --check      # verify installed bsk (0 ok, 2 missing, 1 drift)
#   ./scripts/install_browserskill.sh --dry-run    # print resolved plan, change nothing
#
# Environment overrides:
#   BSK_VERSION           Pinned CLI version (default: 0.5.0)
#   BSK_INSTALL_DIR       Target directory (default: $HOME/.local/bin)
#   BSK_REPO              GitHub owner/repo (default: Tencent/BrowserSkill)
#   BSK_RELEASE_BASE_URL  Override release base URL (offline mirror / tests)
#   BSK_EXPECTED_SHA256   Optional explicit 64-hex SHA-256 (must also match manifest)

set -eu

PINNED_VERSION="0.5.0"
VERSION="${BSK_VERSION:-$PINNED_VERSION}"
VERSION="${VERSION#v}"
REPO="${BSK_REPO:-Tencent/BrowserSkill}"
INSTALL_DIR="${BSK_INSTALL_DIR:-$HOME/.local/bin}"
TAG="cli-v${VERSION}"
BASE_URL="${BSK_RELEASE_BASE_URL:-https://github.com/${REPO}/releases/download/${TAG}}"
EXPECTED_OVERRIDE="${BSK_EXPECTED_SHA256:-}"

MODE="install"

usage() {
  sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --check) MODE="check" ;;
    --dry-run) MODE="dry-run" ;;
    -h | --help) usage; exit 0 ;;
    *) printf 'error: unknown option: %s (try --help)\n' "$1" >&2; exit 2 ;;
  esac
  shift
done

log() {
  printf '==> %s\n' "$1" >&2
}

die() {
  printf 'error: %s\n' "$1" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

detect_platform() {
  os="$(uname -s)"
  arch="$(uname -m)"
  case "$os" in
    Darwin) os_id="darwin" ;;
    Linux) os_id="linux" ;;
    *) die "unsupported OS: $os (use scripts/install_browserskill.ps1 on Windows)" ;;
  esac
  case "$arch" in
    arm64 | aarch64) arch_id="arm64" ;;
    x86_64 | amd64) arch_id="x64" ;;
    *) die "unsupported architecture: $arch" ;;
  esac
  PLATFORM_KEY="${os_id}-${arch_id}"
  case "$PLATFORM_KEY" in
    darwin-arm64) TRIPLE="aarch64-apple-darwin" ;;
    darwin-x64) TRIPLE="x86_64-apple-darwin" ;;
    linux-arm64) TRIPLE="aarch64-unknown-linux-musl" ;;
    linux-x64) TRIPLE="x86_64-unknown-linux-musl" ;;
    *) die "unsupported platform: $PLATFORM_KEY" ;;
  esac
  ARCHIVE="bsk-v${VERSION}-${TRIPLE}.tar.gz"
  MANIFEST_URL="${BASE_URL}/version.json"
  ARCHIVE_URL="${BASE_URL}/${ARCHIVE}"
  TARGET_BIN="${INSTALL_DIR}/bsk"
}

check_installed() {
  bin=""
  if [ -x "$TARGET_BIN" ]; then
    bin="$TARGET_BIN"
  elif command -v bsk >/dev/null 2>&1; then
    bin="$(command -v bsk)"
  else
    printf 'SKIPPED: bsk is not installed (expected %s for pinned v%s)\n' "$TARGET_BIN" "$VERSION"
    exit 2
  fi
  out="$(BSK_AUTO_START=0 "$bin" --version 2>&1)" || die "bsk --version failed at $bin: $out"
  case "$out" in
    *"$VERSION"*)
      printf 'OK: %s reports %s (matches pinned %s)\n' "$bin" "$out" "$VERSION"
      exit 0
      ;;
    *)
      printf 'DRIFT: %s reports %s, expected pinned %s\n' "$bin" "$out" "$VERSION" >&2
      exit 1
      ;;
  esac
}

verify_and_extract_sha() {
  # Fail-closed JSON parse + SHA-256 extraction via Python 3 stdlib.
  python3 - "$1" "$VERSION" "$PLATFORM_KEY" "$EXPECTED_OVERRIDE" <<'PY'
import json, re, sys

manifest_path, want_ver, platform_key, override = sys.argv[1:5]
try:
    data = json.loads(open(manifest_path, encoding="utf-8").read())
except Exception as exc:
    sys.stderr.write(f"invalid version.json: {exc}\n")
    sys.exit(1)

got_ver = str(data.get("version") or "").lstrip("v")
if got_ver != want_ver:
    sys.stderr.write(f"manifest version mismatch: expected {want_ver!r}, got {got_ver!r}\n")
    sys.exit(1)

assets = data.get("assets")
if not isinstance(assets, dict) or not isinstance(assets.get(platform_key), dict):
    sys.stderr.write(f"manifest has no asset block for {platform_key!r}\n")
    sys.exit(1)

sha = str(assets[platform_key].get("sha256") or "").strip().lower()
if not re.fullmatch(r"[0-9a-f]{64}", sha):
    sys.stderr.write(f"manifest has missing/invalid sha256 for {platform_key!r}\n")
    sys.exit(1)

if override:
    ov = override.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", ov):
        sys.stderr.write("BSK_EXPECTED_SHA256 must be 64 hex chars\n")
        sys.exit(1)
    if ov != sha:
        sys.stderr.write(f"BSK_EXPECTED_SHA256 ({ov[:12]}…) != manifest ({sha[:12]}…)\n")
        sys.exit(1)

print(sha)
PY
}

compute_sha256() {
  python3 - "$1" <<'PY'
import hashlib, sys
h = hashlib.sha256()
with open(sys.argv[1], "rb") as fh:
    for chunk in iter(lambda: fh.read(65536), b""):
        h.update(chunk)
print(h.hexdigest())
PY
}

safe_extract_binary() {
  # Reject tar path traversal / symlinks — extract only the regular `bsk` entry.
  python3 - "$1" "$2" <<'PY'
import os, stat, sys, tarfile

archive_path, out_path = sys.argv[1:3]
with tarfile.open(archive_path, "r:gz") as tf:
    members = tf.getmembers()
    bsk_members = [m for m in members if os.path.basename(m.name) == "bsk"
                   and m.name in ("bsk", "./bsk")]
    if len(bsk_members) != 1:
        sys.stderr.write(f"archive does not contain a single root 'bsk' entry: "
                         f"{[m.name for m in members][:10]}\n")
        sys.exit(1)
    m = bsk_members[0]
    if not m.isfile() or m.issym() or m.islnk():
        sys.stderr.write("archive 'bsk' entry is not a regular file\n")
        sys.exit(1)
    src = tf.extractfile(m)
    if src is None:
        sys.stderr.write("could not read 'bsk' from archive\n")
        sys.exit(1)
    with open(out_path, "wb") as dst:
        while True:
            buf = src.read(65536)
            if not buf:
                break
            dst.write(buf)
os.chmod(out_path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
PY
}

main() {
  need_cmd uname
  detect_platform

  if [ "$MODE" = "dry-run" ]; then
    cat <<EOF
{
  "mode": "dry-run",
  "repo": "${REPO}",
  "pinned_version": "${VERSION}",
  "tag": "${TAG}",
  "platform": "${PLATFORM_KEY}",
  "triple": "${TRIPLE}",
  "manifest_url": "${MANIFEST_URL}",
  "archive_url": "${ARCHIVE_URL}",
  "target_bin": "${TARGET_BIN}",
  "checksum_policy": "required (fail-closed)",
  "uses_sudo": false,
  "mutates_shell_rc": false
}
EOF
    exit 0
  fi

  if [ "$MODE" = "check" ]; then
    check_installed
  fi

  need_cmd curl
  need_cmd python3

  case "$INSTALL_DIR" in
    *"/projects/"* | *"/projects")
      die "refusing to install inside projects/ (protected archive)"
      ;;
  esac

  tmp_dir="$(mktemp -d)"
  trap 'rm -rf "$tmp_dir"' EXIT INT TERM

  log "fetching release manifest ${MANIFEST_URL}"
  if ! curl -fsSL "$MANIFEST_URL" -o "${tmp_dir}/version.json"; then
    printf 'error: could not fetch %s (if running in a restricted sandbox, release downloads are BLOCKED — run on your local machine)\n' "$MANIFEST_URL" >&2
    exit 3
  fi

  expected_sha="$(verify_and_extract_sha "${tmp_dir}/version.json")" \
    || die "refusing install: manifest verification failed (fail-closed)"

  log "downloading ${ARCHIVE_URL}"
  if ! curl -fsSL "$ARCHIVE_URL" -o "${tmp_dir}/${ARCHIVE}"; then
    printf 'error: could not download %s (egress BLOCKED or asset missing)\n' "$ARCHIVE_URL" >&2
    exit 3
  fi

  log "verifying SHA-256 (fail-closed)"
  actual_sha="$(compute_sha256 "${tmp_dir}/${ARCHIVE}")"
  if [ "$actual_sha" != "$expected_sha" ]; then
    die "SHA-256 MISMATCH: expected ${expected_sha}, got ${actual_sha} — refusing install"
  fi
  log "SHA-256 verified (${actual_sha})"

  safe_extract_binary "${tmp_dir}/${ARCHIVE}" "${tmp_dir}/bsk.new"

  mkdir -p "$INSTALL_DIR"
  # Atomic same-directory rename so a running caller never sees a half-written binary.
  cp "${tmp_dir}/bsk.new" "${TARGET_BIN}.tmp.$$"
  chmod 0755 "${TARGET_BIN}.tmp.$$"
  mv -f "${TARGET_BIN}.tmp.$$" "$TARGET_BIN"

  ver_out="$(BSK_AUTO_START=0 "$TARGET_BIN" --version 2>&1)" \
    || die "installed binary failed --version check: $ver_out"
  log "installed ${TARGET_BIN} (${ver_out})"

  case ":${PATH}:" in
    *":${INSTALL_DIR}:"*) ;;
    *) log "add to your shell PATH (not edited automatically): export PATH=\"${INSTALL_DIR}:\$PATH\"" ;;
  esac

  cat >&2 <<EOF

Next steps (Stage 1 · Mode 1 — Local browser + local machine):
  1. Install the extension (Chromium 125+):
       Chrome : https://chromewebstore.google.com/detail/hhcmgoofomhgciiibhipgmgkgnoenaoi
       Edge   : https://microsoftedge.microsoft.com/addons/detail/browserskill/emacgiaaaiojkkpkddmmdfhmokgmnikg
  2. Start the local daemon and pair once (never share or commit the pairing link):
       bsk daemon start
       bsk daemon pair
  3. Verify from AI-2027 (read-only, zero mutations):
       python3 browser_skill_adapter/probe.py --report browser_skill_adapter/phase0-report.json
  4. Run one verified local task (produces SHA-256 evidence + closes its session):
       python3 browser_skill_adapter/run_local_task.py --url https://example.com
EOF
}

main
