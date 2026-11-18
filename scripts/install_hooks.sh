#!/bin/sh
# Install the repository's git hooks (idempotent, no network, no dependencies).
#
#   ./scripts/install_hooks.sh          # enable .githooks for this clone
#   ./scripts/install_hooks.sh --check  # report status, change nothing
#   ./scripts/install_hooks.sh --off    # restore git's default hook path
#
# The only hook today is pre-commit → scripts/secret_scan.py --staged, the gate
# that stops a credential before it becomes history (issue #21).
set -eu

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

want=".githooks"
current=$(git config --get core.hooksPath || true)

case "${1:-}" in
  --check)
    if [ "$current" = "$want" ]; then
      echo "hooks: enabled (core.hooksPath=$want)"
    else
      echo "hooks: NOT enabled (core.hooksPath=${current:-<default>})"
      echo "run: ./scripts/install_hooks.sh"
      exit 1
    fi
    ;;
  --off)
    git config --unset core.hooksPath || true
    echo "hooks: disabled (git default hook path restored)"
    ;;
  "")
    chmod +x .githooks/pre-commit 2>/dev/null || true
    git config core.hooksPath "$want"
    echo "hooks: enabled (core.hooksPath=$want)"
    echo "check: python3 scripts/secret_scan.py --staged"
    ;;
  *)
    echo "usage: $0 [--check|--off]" >&2
    exit 2
    ;;
esac
