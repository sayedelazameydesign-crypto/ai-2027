#!/usr/bin/env python3
"""Block a real secret before it becomes a commit (stdlib only).

Why this exists: this repository shipped `projects/ai-store2/env.local` with a
live `AIza…` Gemini key inside a byte-mirror of an upstream repo (issue #21).
Redacting the mirror (D-2) did not un-leak it — revocation did. The cheapest
place to stop the next one is *before* the commit exists.

Policy, quoted from `SECURITY.md` (this file implements it, it does not invent it):

* `.env` / `.env.local` / `env.local` never belong in a commit;
* well-known fake fixtures are allowed (`AKIAIOSFODNN7EXAMPLE`,
  `ghp_thisIsAFakeToken1234`) — they are strings, not credentials;
* everything else that looks like a credential is refused with file + line.

The scanner never prints the secret it found: a gate that echoes the key into a
CI log has simply published it twice.

Usage
    python3 scripts/secret_scan.py                  # staged changes (git hook)
    python3 scripts/secret_scan.py --all            # every tracked file, code only
    python3 scripts/secret_scan.py --all --scope archive   # include projects/
    python3 scripts/secret_scan.py --path some/file --json
    python3 scripts/secret_scan.py --staged --repo /tmp/repo

Exit codes: 0 clean · 1 findings · 2 usage/git error.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MAX_BYTES = 2 * 1024 * 1024          # a 2 MB file is not a config leak surface
ALLOWLIST_MARKERS = ("allowlist-secret", "pragma: allowlist secret")

# Values that SECURITY.md already declares as fixtures. Kept literal and short:
# any credential with a real prefix is matched by the patterns below regardless.
KNOWN_FAKE = (
    "AKIAIOSFODNN7EXAMPLE",          # AWS docs example key
    "ghp_thisIsAFakeToken1234",      # SECURITY.md fixture
    "AIzaSyExampleNotARealKey0000000000000000",
    "sk-test-not-a-real-key-000000000000",
)

PLACEHOLDER_HINTS = ("redacted", "example", "your-", "your_", "xxxx", "changeme",
                     "placeholder", "dummy", "fake", "sample", "not-a-real",
                     "<", "${", "$(", "{{", "***", "…")

# High-confidence shapes: a real credential with these prefixes is not accidental.
PATTERNS: tuple[tuple[str, str], ...] = (
    ("google_api_key", r"AIza[0-9A-Za-z_\-]{35}"),
    ("aws_access_key", r"(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|A3T[A-Z0-9])[A-Z0-9]{16}"),
    ("github_token", r"gh[pousr]_[A-Za-z0-9]{36,}"),
    ("openai_key", r"sk-(?:proj-)?[A-Za-z0-9_\-]{32,}"),
    ("slack_token", r"xox[baprs]-[0-9A-Za-z\-]{10,}"),
    ("stripe_live_key", r"(?:sk|rk)_live_[0-9A-Za-z]{16,}"),
    ("google_oauth_token", r"ya29\.[0-9A-Za-z_\-]{20,}"),
    ("private_key_block", r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
    ("vercel_token", r"vercel_[A-Za-z0-9]{24,}"),
    ("jwt", r"eyJ[A-Za-z0-9_\-]{16,}\.eyJ[A-Za-z0-9_\-]{16,}\."),
)

# A generic `key = "…"` is only suspicious when the value looks like a secret
# (length + entropy), otherwise every `api_key = "from_env"` becomes an alarm.
GENERIC_ASSIGNMENT = re.compile(
    r"""(?ix)
    (?:api[_-]?key | secret[_-]?key | access[_-]?token | auth[_-]?token |
       client[_-]?secret | private[_-]?key | password)
    \s*[:=]\s*
    ["']([^"'\s]{24,})["']
    """
)

COMPILED = tuple((name, re.compile(rx)) for name, rx in PATTERNS)

# Files that must never exist in a commit. `*.env.example` is deliberately NOT
# here: a template full of placeholders is the correct way to ship config.
FORBIDDEN_NAMES = {".env", ".env.local", "env.local"}
FORBIDDEN_SUFFIXES = (".key", ".p12", ".pfx")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".next", "dist", "build", ".venv"}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    masked: str
    severity: str = "high"      # "high" = refuse · "warn" = review, do not block

    def to_dict(self) -> dict:
        return {"path": self.path, "line": self.line, "rule": self.rule,
                "masked": self.masked, "severity": self.severity}

    def as_text(self) -> str:
        return f"{self.path}:{self.line}  {self.rule}  {self.masked}"


def entropy(value: str) -> float:
    """Shannon entropy per character — `aaaaaaaa…` scores 0, a key scores high."""
    if not value:
        return 0.0
    counts = {c: value.count(c) for c in set(value)}
    n = len(value)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def is_allowed_value(value: str) -> bool:
    low = value.lower()
    return any(fake.lower() in low for fake in KNOWN_FAKE) or \
        any(hint in low for hint in PLACEHOLDER_HINTS)


def mask(value: str) -> str:
    """`AIzaSyBa…` — enough to locate it, not enough to use it."""
    keep = 6 if len(value) > 12 else 2
    return f"{value[:keep]}…({len(value)} chars)"


def scan_text(text: str, path: str = "<text>") -> list[Finding]:
    """Every credential-shaped string in one file's content."""
    findings: list[Finding] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if any(marker in raw for marker in ALLOWLIST_MARKERS):
            continue
        for rule, rx in COMPILED:
            for match in rx.finditer(raw):
                value = match.group(0)
                if is_allowed_value(value):
                    continue
                findings.append(Finding(path, lineno, rule, mask(value)))
        for match in GENERIC_ASSIGNMENT.finditer(raw):
            value = match.group(1)
            if is_allowed_value(value) or entropy(value) < 3.2:
                continue
            # A generic `api_key = "…"` is a *maybe*: real archives are full of
            # test fixtures. It is surfaced, but it never blocks a commit alone.
            findings.append(Finding(path, lineno, "assigned_secret", mask(value),
                                    severity="warn"))
    # de-duplicate identical (line, rule) pairs produced by several rules
    seen, unique = set(), []
    for f in findings:
        key = (f.line, f.rule, f.masked, f.severity)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def scan_bytes(data: bytes, path: str = "<bytes>") -> list[Finding]:
    if len(data) > MAX_BYTES or b"\x00" in data[:8192]:
        return []                      # binary or oversized: not a text leak surface
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return []
    return scan_text(text, path)


def forbidden_path(name: str) -> Finding | None:
    base = os.path.basename(name)
    if base in FORBIDDEN_NAMES or base.endswith(FORBIDDEN_SUFFIXES):
        return Finding(name, 0, "forbidden_file", basename_mask(base))
    return None


def basename_mask(name: str) -> str:
    return f"{name} (never belongs in a commit)"


def _git(args: list[str], repo: Path) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: "
                           f"{proc.stderr.decode('utf-8', 'replace')[:200]}")
    return proc.stdout.decode("utf-8", "replace")


def staged_paths(repo: Path) -> list[str]:
    raw = _git(["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"], repo)
    return [p for p in raw.split("\0") if p]


def staged_content(repo: Path, path: str) -> bytes:
    proc = subprocess.run(["git", "show", f":{path}"], cwd=repo, capture_output=True)
    return proc.stdout if proc.returncode == 0 else b""


def tracked_paths(repo: Path) -> list[str]:
    raw = _git(["ls-files", "-z"], repo)
    return [p for p in raw.split("\0") if p]


def in_scope(path: str, scope: str) -> bool:
    """`code` skips the archive · `archive` scans only it · `all` scans everything.

    The archive is a byte-mirror of 48 upstream repos: a finding there is
    reported as information, never as this repository's commit blocker.
    """
    parts = Path(path).parts
    if any(part in SKIP_DIRS for part in parts):
        return False
    in_archive = bool(parts) and parts[0] == "projects"
    if scope == "code":
        return not in_archive
    if scope == "archive":
        return in_archive
    return True


def scan_paths(repo: Path, paths: list[str], scope: str,
               reader=None) -> list[Finding]:
    findings: list[Finding] = []
    read = reader or (lambda p: (repo / p).read_bytes())
    for path in paths:
        if not in_scope(path, scope):
            continue
        bad = forbidden_path(path)
        if bad:
            findings.append(bad)
        try:
            data = read(path)
        except OSError:
            continue
        findings.extend(scan_bytes(data, path))
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Refuse commits that contain credentials.")
    ap.add_argument("--staged", action="store_true",
                    help="scan the git index (what a pre-commit hook sees)")
    ap.add_argument("--all", action="store_true", help="scan every tracked file")
    ap.add_argument("--path", action="append", default=[],
                    help="scan a specific file (repeatable)")
    ap.add_argument("--scope", choices=("code", "all", "archive"), default="code",
                    help="'code' skips projects/ (byte-mirror), 'all' includes it")
    ap.add_argument("--repo", default=str(ROOT))
    ap.add_argument("--strict-generic", action="store_true",
                    help="also fail on low-confidence 'assigned_secret' warnings")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    repo = Path(args.repo).resolve()
    staged = args.staged or not (args.all or args.path)

    findings: list[Finding] = []
    archive_findings: list[Finding] = []
    try:
        if args.path:
            findings = scan_paths(repo, args.path, "all",
                                  reader=lambda p: Path(p).read_bytes()
                                  if Path(p).is_absolute() else (repo / p).read_bytes())
        elif staged:
            paths = staged_paths(repo)
            findings = scan_paths(repo, paths, "all",
                                  reader=lambda p: staged_content(repo, p))
        else:
            paths = tracked_paths(repo)
            findings = scan_paths(repo, paths, args.scope)
            if args.scope == "code":
                archive_findings = scan_paths(repo, paths, "archive")
    except RuntimeError as exc:
        print(f"secret_scan: {exc}", file=sys.stderr)
        return 2

    blocking = [f for f in findings if f.severity == "high"]
    warning = [f for f in findings if f.severity != "high"]
    failed = bool(blocking) or (args.strict_generic and bool(warning))

    if args.json:
        print(json.dumps({"ok": not failed, "staged": staged,
                          "blocking": len(blocking), "warnings": len(warning),
                          "findings": [f.to_dict() for f in findings],
                          "archive_findings": [f.to_dict() for f in archive_findings],
                          "known_fake_allowed": list(KNOWN_FAKE)},
                         ensure_ascii=False, indent=2))
    else:
        mode = "staged" if staged else f"tracked ({args.scope})"
        print(f"SECRET SCAN — {mode} · {len(blocking)} blocking · "
              f"{len(warning)} warning(s)")
        for f in blocking:
            print(f"  ✗ {f.as_text()}")
        for f in warning:
            print(f"  ? {f.as_text()}   (possible fixture — review, not blocking)")
        if archive_findings:
            print(f"  … {len(archive_findings)} more inside projects/ (byte-mirror; "
                  "fix upstream, not here)")
            for f in archive_findings[:5]:
                print(f"    · {f.as_text()}")
        if failed:
            print("  => REFUSED: a credential must never be committed.")
        elif warning:
            print("  ✓ no blocking credential found.")
        else:
            print("  ✓ clean")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
