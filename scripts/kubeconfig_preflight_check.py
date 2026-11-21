#!/usr/bin/env python3
"""Fail-closed safety gate for the Agent Substrate connectivity preflight.

The preflight workflow must only ever reach the cluster API with a
kubeconfig that is static and has TLS verification enabled.  A previous
implementation checked the kubeconfig by grepping its raw text for the
strings ``exec:`` and ``insecure-skip-tls-verify:``.  That approach was
unsound: YAML treats a quoted key (``"insecure-skip-tls-verify"``) and a
Flow-style mapping as exactly the same key as the plain form, so a
kubeconfig could disable TLS verification while passing the grep.  The
same held for ``exec`` credential plugins.

This module therefore inspects the *parsed* structure of the kubeconfig.
The workflow renders it to JSON with ``kubectl config view --raw -o json``
(``--raw`` keeps credential material, which is why the rendered JSON is
never printed) and this module validates the fields that matter using only
the Python standard library.

Deliberate properties:

* **Fail closed.**  Anything that cannot be parsed, or that uses an
  unsupported shape, is rejected.
* **Quiet.**  Nothing is printed except the single verdict word.  Cluster
  addresses, credential material and kubectl diagnostics never reach the
  log or the job summary.
* **Stdlib only.**  No third-party dependency is required, so the gate
  behaves identically on any runner.
"""

from __future__ import annotations

import json
import os
import re
import sys

# Verdict words.  Exactly these are ever printed.
CONNECTED = "CONNECTED"
FAILED = "FAILED"

# Keys that must never appear anywhere in the rendered kubeconfig.
# ``insecure-skip-tls-verify`` disables TLS verification; ``exec`` names a
# credential plugin that the workflow must never run.
FORBIDDEN_KEYS = frozenset({"exec", "insecure-skip-tls-verify"})

# Keys whose *value* is a map that must be checked recursively.  These are
# the kubeconfig sections that may carry a cluster or a user entry.
_MAP_SECTIONS = ("clusters", "users", "contexts")


def _walk(node: object) -> None:
    """Raise ``ValueError`` if a forbidden key appears anywhere below ``node``.

    The rendered kubeconfig is a tree of mappings and lists.  A forbidden
    key is rejected wherever it appears, not only at a fixed depth, so that
    nested or renamed placements cannot slip past.  Checking the whole
    tree is deliberate: it is stricter than checking only
    ``clusters[].cluster`` and ``users[].user``, and a stricter gate is the
    safe direction for a check whose purpose is to refuse.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.strip().lower() in FORBIDDEN_KEYS:
                raise ValueError("forbidden kubeconfig key present")
            _walk(value)
    elif isinstance(node, list):
        for item in node:
            _walk(item)


def _check_shape(document: object) -> None:
    """Reject a document that is not shaped like a kubeconfig.

    A JSON document that parses cleanly but carries none of the expected
    sections means the input was not a kubeconfig at all.  Accepting it
    would let a malformed or substituted file through the gate, so the
    shape is verified before any other check.
    """
    if not isinstance(document, dict):
        raise ValueError("kubeconfig is not a JSON object")
    if not any(section in document for section in _MAP_SECTIONS):
        raise ValueError("kubeconfig has no clusters, users or contexts section")
    for section in _MAP_SECTIONS:
        if section in document and not isinstance(document[section], list):
            raise ValueError(f"kubeconfig section {section!r} is not a list")


def check_kubeconfig_json(text: str) -> None:
    """Validate the rendered JSON of a kubeconfig.

    Raises ``ValueError`` when the kubeconfig must be refused.  Returns
    normally when it is safe to use.
    """
    try:
        document = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ValueError("kubeconfig could not be parsed as JSON") from exc

    _check_shape(document)
    _walk(document)


def main(argv: list[str]) -> int:
    """Check the rendered kubeconfig JSON on stdin, or a version pair.

    Two modes:

    * ``kubeconfig_preflight_check.py < rendered.json`` - read the rendered
      kubeconfig and print ``CONNECTED`` or ``FAILED``.
    * ``kubeconfig_preflight_check.py --check-version`` - read
      ``kubectl version -o json`` on stdin and compare the server's minor
      version against ``PREFLIGHT_KUBECTL_VERSION``.  Exit status ``0``
      when the client is within one minor version of the server.

    Exit status is ``0`` when the input is acceptable and ``1`` when it is
    refused.  The only output is the verdict word.
    """
    if argv and argv[0] == "--check-version":
        return _check_version_mode()

    try:
        raw = sys.stdin.read()
    except (OSError, UnicodeDecodeError):
        print(FAILED)
        return 1

    try:
        check_kubeconfig_json(raw)
    except ValueError:
        print(FAILED)
        return 1

    print(CONNECTED)
    return 0


def _check_version_mode() -> int:
    """Verify the kubectl client against the cluster's API server version.

    The server minor version comes from ``kubectl version -o json`` on
    stdin; the client version comes from the ``PREFLIGHT_KUBECTL_VERSION``
    environment variable.  Anything unreadable is refused.
    """
    try:
        raw = sys.stdin.read()
    except (OSError, UnicodeDecodeError):
        return 1

    server_minor = server_minor_from_version_json(raw)
    if server_minor is None:
        return 1

    client = os.environ.get("PREFLIGHT_KUBECTL_VERSION", "")
    if not versions_are_compatible(client, server_minor):
        return 1

    return 0


# --------------------------------------------------------------------------
# kubectl / kube-apiserver version compatibility
#
# The Kubernetes version-skew policy supports kubectl within one minor
# version (older or newer) of kube-apiserver.  The preflight therefore
# treats the client version as an operator choice that is verified against
# the cluster, not assumed.
# --------------------------------------------------------------------------

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


def parse_minor(version: str) -> int | None:
    """Return the minor version number of a ``x.y.z`` version string.

    Returns ``None`` when the string is not a version at all, so that a
    value such as ``latest`` is refused instead of silently accepted.
    """
    match = _VERSION_RE.match(version.strip())
    if match is None:
        return None
    return int(match.group(2))


def versions_are_compatible(client: str, server_minor: str) -> bool:
    """Return whether ``client`` is within one minor version of the server.

    ``server_minor`` is the minor number reported by the API server, which
    Kubernetes renders with a trailing ``+`` (for example ``37+``).  A
    client that is more than one minor away from the server is refused.
    """
    client_minor = parse_minor(client)
    if client_minor is None:
        return False
    try:
        # Kubernetes renders the minor version with a trailing "+" (37+),
        # so the suffix is stripped from both ends before conversion.
        server = int(server_minor.strip().strip("+").strip())
    except (TypeError, ValueError):
        return False
    return abs(server - client_minor) <= 1


def server_minor_from_version_json(text: str) -> str | None:
    """Extract the server minor version from ``kubectl version -o json``.

    Returns ``None`` when the payload cannot be parsed or carries no
    server version, so that an unreadable version fails closed.
    """
    try:
        document = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(document, dict):
        return None
    server_version = document.get("serverVersion")
    if not isinstance(server_version, dict):
        return None
    minor = server_version.get("minor")
    if minor is None:
        return None
    return str(minor)


if __name__ == "__main__":  # pragma: no cover - exercised via main()
    raise SystemExit(main(sys.argv[1:]))
