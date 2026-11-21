"""Resolve Live Agent bearer headers without exposing or persisting credentials.

Explicit environment credentials are preferred. Bearer credentials are only
returned for HTTPS targets, except that loopback HTTP is allowed for local
services (and bypasses configured HTTP proxies). A generated credential file is
read only for loopback URLs, and only when it is a private regular file under the
ignored `.ember-up/` local state directory. No credential is ever inferred for a
remote host. Operator write tokens follow the shared contract: 32-512 visible
ASCII characters (`0x21`-`0x7e`), with no whitespace; symbols such as `+`, `/`,
and `=` are valid.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import stat
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
_LOCAL_CREDENTIALS = ROOT / ".ember-up" / "api-credentials.json"
_TOKEN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_TOKEN_SECRET = re.compile(r"[A-Za-z0-9_-]{32,256}\Z")
# Shared operator-token contract: visible ASCII 0x21-0x7e, 32-512 chars.
_WRITE_TOKEN_RE = re.compile(r"[!-~]{32,512}\Z")


class _NoAuthenticatedRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward a bearer header to a redirect target."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if request.has_header("Authorization"):
            return None
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def open_url(request: urllib.request.Request, timeout: float = 30):
    """Open a request, enforcing secure transport and refusing token redirects."""
    if request.has_header("Authorization"):
        if not _credential_transport_allowed(request.full_url):
            raise ValueError("refusing to send bearer credentials over insecure transport")
        parsed = urlsplit(request.full_url)
        handlers = [_NoAuthenticatedRedirect()]
        if parsed.scheme.casefold() == "http":
            # A configured HTTP proxy would see the bearer header even for a
            # loopback URL. Local plaintext credentials must stay on this host.
            handlers.insert(0, urllib.request.ProxyHandler({}))
        opener = urllib.request.build_opener(*handlers)
        return opener.open(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)


def _is_loopback_url(base: str) -> bool:
    try:
        parsed = urlsplit(base)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        if parsed.hostname.casefold() == "localhost":
            return True
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except (ValueError, UnicodeError):
        return False


def _credential_transport_allowed(base: str) -> bool:
    """Allow credentials on HTTPS or on HTTP loopback only."""
    try:
        parsed = urlsplit(base)
        # Accessing .port validates malformed and out-of-range ports.
        parsed.port
        if (
            parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
        ):
            return False
        if parsed.scheme.casefold() == "https":
            return True
        return parsed.scheme.casefold() == "http" and _is_loopback_url(base)
    except (ValueError, UnicodeError):
        return False


def _local_credentials(base: str) -> tuple[str, str, str] | None:
    """Read the ignored local credential file without following file symlinks."""
    if not _is_loopback_url(base) or os.name != "posix":
        # The stdlib cannot verify restrictive Windows ACLs. Require explicit
        # environment credentials there instead of trusting chmod semantics.
        return None
    state_dir = _LOCAL_CREDENTIALS.parent
    directory_fd: int | None = None
    credential_fd: int | None = None
    try:
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_flags |= getattr(os, "O_NOFOLLOW", 0)
        directory_fd = os.open(state_dir, directory_flags)
        directory_stat = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(directory_stat.st_mode)
            or directory_stat.st_uid != os.geteuid()
            or directory_stat.st_mode & 0o077
        ):
            return None
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        file_flags |= getattr(os, "O_CLOEXEC", 0)
        credential_fd = os.open(
            _LOCAL_CREDENTIALS.name, file_flags, dir_fd=directory_fd
        )

        metadata = os.fstat(credential_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > 4096
            or (os.name == "posix" and (
                metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077
            ))
        ):
            return None
        with os.fdopen(credential_fd, "r", encoding="utf-8") as handle:
            credential_fd = None  # fdopen owns it from here
            data = json.load(handle)
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    finally:
        if credential_fd is not None:
            os.close(credential_fd)
        if directory_fd is not None:
            os.close(directory_fd)

    if not isinstance(data, dict):
        return None
    token_id = data.get("read_id")
    reader = data.get("read_secret")
    writer = data.get("write_token")
    hashes = data.get("read_hashes")
    if (
        not isinstance(token_id, str) or not _TOKEN_ID.fullmatch(token_id)
        or not isinstance(reader, str) or not _TOKEN_SECRET.fullmatch(reader)
        or not isinstance(writer, str) or not _WRITE_TOKEN_RE.fullmatch(writer)
        or not isinstance(hashes, dict)
        or hashes.get(token_id) != hashlib.sha256(reader.encode("utf-8")).hexdigest()
    ):
        return None
    return token_id, reader, writer


def authorization_header(base: str, path: str, method: str) -> str | None:
    """Return the appropriate Authorization header, if explicitly configured."""
    try:
        api_path = urlsplit(path).path
    except (ValueError, UnicodeError):
        return None
    if not (api_path == "/api" or api_path.startswith("/api/")):
        return None

    verb = method.upper()
    if verb == "GET" and api_path == "/api/health":
        return None
    if not _credential_transport_allowed(base):
        return None
    if verb == "POST":
        if api_path != "/api/run":
            return None
        token = os.environ.get("AI2027_WRITE_TOKEN", "")
        if token:
            return f"Bearer {token}" if _WRITE_TOKEN_RE.fullmatch(token) else None
        credentials = _local_credentials(base)
        return f"Bearer {credentials[2]}" if credentials else None
    if verb != "GET":
        return None

    credential = os.environ.get("AI2027_READ_CREDENTIAL", "")
    if credential:
        token_id, separator, secret = credential.partition(".")
        if separator and _TOKEN_ID.fullmatch(token_id) and _TOKEN_SECRET.fullmatch(secret):
            return f"Bearer {credential}"
        return None

    token_id = os.environ.get("AI2027_READ_TOKEN_ID", "")
    secret = os.environ.get("AI2027_READ_TOKEN_SECRET", "")
    if token_id or secret:
        if _TOKEN_ID.fullmatch(token_id) and _TOKEN_SECRET.fullmatch(secret):
            return f"Bearer {token_id}.{secret}"
        return None

    credentials = _local_credentials(base)
    return f"Bearer {credentials[0]}.{credentials[1]}" if credentials else None
