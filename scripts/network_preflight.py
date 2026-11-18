#!/usr/bin/env python3
"""Prove whether this process can reach the public internet BEFORE judging a URL.

The problem this solves is not hypothetical. On 2026-11-18 the verification
session ran inside a sandbox whose egress was limited to `github.com`,
`pypi.org` and `registry.npmjs.org`. Every `curl` to a public site returned
`000`, and `urllib` raises the *same* `URLError` for "the site is dead" and for
"this machine cannot reach the internet at all". A checker that treats both as
`status: 0 / live: false` therefore publishes a false claim — "the sites are
down" — when the truth is "this environment is blind".

`network_preflight` answers the one question that must be answered first:
*can I judge this URL here, at all?*

    python3 scripts/network_preflight.py                # human table
    python3 scripts/network_preflight.py --json         # machine
    python3 scripts/network_preflight.py --require-open # exit 1 unless OPEN

Classification (measured, never assumed):

    OPEN             >= 2 non-allowlisted canaries answered (any HTTP status)
    ALLOWLISTED_ONLY  something answered, but no general egress
    CLOSED            nothing answered

An HTTP error status (404/403/451) still proves reachability: DNS resolved, the
TCP/TLS handshake completed and a server answered. Only a transport failure
(URLError/OSError) counts as unreachable.

Stdlib only. No third-party imports. No hidden retries.
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Iterable

# Canaries chosen for diversity, not for brand: an ICANN-reserved page, a
# large non-profit, and a CDN/edge provider. If two of three answer, the
# process has general egress.
CANARIES: tuple[str, ...] = (
    "https://example.com/",
    "https://www.wikipedia.org/",
    "https://www.cloudflare.com/",
)

# Hosts this project's tooling needs even when general egress is blocked
# (GitHub API for archive identity, registries for installs). Kept explicit so
# "allowlisted" is a fact about a named list, never a guess.
ALLOWLIST_HOSTS: frozenset[str] = frozenset({
    "github.com",
    "api.github.com",
    "codeload.github.com",
    "objects.githubusercontent.com",
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
})

OPEN = "OPEN"
ALLOWLISTED_ONLY = "ALLOWLISTED_ONLY"
CLOSED = "CLOSED"

DEFAULT_TIMEOUT = 4.0


@dataclass(frozen=True)
class Probe:
    """One measured reachability result."""

    url: str
    host: str
    reachable: bool
    status: int | None
    ms: float
    error: str | None = None
    allowlisted: bool = False

    def to_dict(self) -> dict:
        return {"url": self.url, "host": self.host, "reachable": self.reachable,
                "status": self.status, "ms": round(self.ms, 1),
                "error": self.error, "allowlisted": self.allowlisted}


@dataclass(frozen=True)
class Report:
    """The preflight verdict plus every probe that produced it."""

    egress: str
    probes: tuple[Probe, ...] = field(default_factory=tuple)

    # -- judging gates ---------------------------------------------------- #
    @property
    def general_egress(self) -> bool:
        return self.egress == OPEN

    def allows(self, url: str) -> bool:
        """True only if a verdict about `url` would be *evidence*, not blindness."""
        if self.egress == OPEN:
            return True
        host = _host(url)
        if self.egress == ALLOWLISTED_ONLY:
            return host in ALLOWLIST_HOSTS
        return False

    def verdict_for_failure(self, url: str) -> str:
        """What a failed fetch of `url` means here: 'blocked' or 'dead'.

        This is the whole point of the module: without it, "blocked" is
        published as "dead".
        """
        return "dead" if self.allows(url) else "blocked"

    def to_dict(self) -> dict:
        return {
            "egress": self.egress,
            "general_egress": self.general_egress,
            "can_judge_public_urls": self.allows("https://example.com/"),
            "probes": [p.to_dict() for p in self.probes],
        }


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def probe(url: str, timeout: float = DEFAULT_TIMEOUT,
          opener: Callable[..., object] | None = None) -> Probe:
    """Measure one URL. Any HTTP answer means reachable; only transport counts as not."""
    import time

    open_fn = opener or urllib.request.urlopen
    started = time.time()
    host = _host(url)
    try:
        req = urllib.request.Request(url, method="GET",
                                     headers={"User-Agent": "ai-2027-preflight/1"})
        with open_fn(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or getattr(resp, "code", None)
        return Probe(url, host, True, int(status) if status else None,
                     (time.time() - started) * 1000,
                     allowlisted=host in ALLOWLIST_HOSTS)
    except urllib.error.HTTPError as exc:  # an answer — the host is reachable
        return Probe(url, host, True, int(exc.code), (time.time() - started) * 1000,
                     error=f"HTTP {exc.code}", allowlisted=host in ALLOWLIST_HOSTS)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return Probe(url, host, False, None, (time.time() - started) * 1000,
                     error=str(reason)[:120], allowlisted=host in ALLOWLIST_HOSTS)


def classify(probes: Iterable[Probe]) -> str:
    """OPEN / ALLOWLISTED_ONLY / CLOSED — from measurements only."""
    probes = list(probes)
    general = [p for p in probes if p.reachable and not p.allowlisted]
    if len(general) >= 2:
        return OPEN
    if any(p.reachable for p in probes):
        return ALLOWLISTED_ONLY
    return CLOSED


def preflight(urls: Iterable[str] = CANARIES, timeout: float = DEFAULT_TIMEOUT,
              opener: Callable[..., object] | None = None) -> Report:
    """Probe the canaries (plus the allowlist reference) and classify egress."""
    targets = list(urls)
    # The allowlist reference is always measured: it is what distinguishes
    # "the internet is closed" from "only the allowlist is open".
    if "https://api.github.com/" not in targets:
        targets.append("https://api.github.com/")
    probes = tuple(probe(u, timeout=timeout, opener=opener) for u in targets)
    return Report(classify(probes), probes)


def cached_preflight(timeout: float = DEFAULT_TIMEOUT,
                     opener: Callable[..., object] | None = None) -> Report:
    """Run the preflight at most once per process (probes cost seconds offline)."""
    global _CACHE
    if _CACHE is None:
        _CACHE = preflight(timeout=timeout, opener=opener)
    return _CACHE


_CACHE: Report | None = None


def reset_cache() -> None:
    """Drop the memoised report (tests, and any long-lived process that re-checks)."""
    global _CACHE
    _CACHE = None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Measure whether this environment can reach the public internet.")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--url", action="append", default=[],
                    help="extra URL to measure (repeatable)")
    ap.add_argument("--require-open", action="store_true",
                    help="exit 1 unless general egress is OPEN")
    args = ap.parse_args(argv)

    report = preflight(list(CANARIES) + args.url, timeout=args.timeout)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False))
    else:
        print("NETWORK PREFLIGHT — can this environment judge public URLs?")
        print("=" * 70)
        for p in report.probes:
            mark = "OK  " if p.reachable else "FAIL"
            status = p.status if p.status is not None else "-"
            print(f"  {mark} {p.host:<28} {str(status):>4}  {p.ms:6.0f} ms"
                  + (f"  ({p.error})" if p.error else ""))
        print("-" * 70)
        print(f"  egress                  : {report.egress}")
        print(f"  can judge public URLs   : {report.allows('https://example.com/')}")
        if not report.general_egress:
            print("  => any public-site check must be recorded as BLOCKED, not dead.")
    if args.require_open and not report.general_egress:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
