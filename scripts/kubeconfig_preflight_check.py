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
    """Read the rendered kubeconfig JSON on stdin and print one verdict.

    Usage: ``kubeconfig_preflight_check.py < rendered.json``

    Exit status is ``0`` when the kubeconfig is acceptable and ``1`` when
    it is refused.  The only output is the verdict word.
    """
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


if __name__ == "__main__":  # pragma: no cover - exercised via main()
    raise SystemExit(main(sys.argv[1:]))
