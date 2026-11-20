"""Endpoint safety — HTTPS anywhere, plain HTTP only on loopback.

Every engine that talks to a provider needs this rule, and the ones that wrote
it themselves disagreed about ``localhost`` vs ``127.0.0.1`` vs ``::1``.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from .contracts import UnsafeValueError

LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})


def validate_endpoint(raw_url: str, *, allow_loopback_http: bool = True) -> str:
    """Validate and normalise a provider endpoint.

    Rejects embedded credentials, query strings and fragments — all three leak
    secrets into logs when the URL is printed on failure.
    """
    candidate = (raw_url or "").strip()
    if not candidate:
        raise UnsafeValueError("endpoint must not be empty")
    parsed = urlparse(candidate)
    if parsed.username or parsed.password:
        raise UnsafeValueError("endpoint must not embed credentials")
    if parsed.query or parsed.fragment:
        raise UnsafeValueError("endpoint must not include query strings or fragments")
    host = (parsed.hostname or "").lower()
    if not host:
        raise UnsafeValueError("endpoint must specify a hostname")
    is_loopback = host in LOOPBACK_HOSTS
    if parsed.scheme == "http":
        if not (allow_loopback_http and is_loopback):
            raise UnsafeValueError("plain HTTP is allowed only for loopback hosts")
    elif parsed.scheme != "https":
        raise UnsafeValueError("endpoint must use https, or loopback http")

    # Adopted from python-2027's stronger copy: a public-looking scheme is still
    # an SSRF risk when the host is a private, link-local or reserved address.
    if not is_loopback:
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None and (
            ip.is_private or ip.is_reserved or ip.is_multicast
        ):
            raise UnsafeValueError("endpoint cannot target private or link-local IPs")

    return candidate.rstrip("/")
