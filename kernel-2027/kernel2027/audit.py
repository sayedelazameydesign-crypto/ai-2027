"""Hash-chained audit links — one definition of "tamper-evident".

``fleet2027.store`` had the only chain in the repo, so no other engine could
make the same claim. This is the extractable core: a link is a pure function of
the previous digest and the canonical body, which is what makes independent
verification possible.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .contracts import canonical_json, sha256_text

#: The digest a chain starts from — 32 zero bytes in hex, not a secret.
GENESIS = "0" * 64


def audit_link(
    *,
    previous_sha: str,
    kind: str,
    payload: Mapping[str, Any],
    at: str,
    task_id: str | None = None,
) -> tuple[str, str]:
    """Return ``(body, digest)`` for one audit row.

    The body is canonical JSON so the digest can be recomputed by anyone holding
    the row — including a different engine, in a different language, later.
    """
    if len(previous_sha) != 64:
        raise ValueError(f"previous_sha must be 64 hex chars, got {len(previous_sha)}")
    body = canonical_json(
        {"at": at, "kind": kind, "task_id": task_id, "payload": dict(payload)}
    )
    return body, sha256_text(previous_sha + body)


def verify_chain(rows: Iterable[Mapping[str, Any]]) -> tuple[bool, int, str | None]:
    """Recompute a whole chain; return ``(ok, rows_checked, problem)``.

    ``problem`` names the first broken row and *why*, because "chain invalid"
    without a location is not actionable at 3am.
    """
    previous = GENESIS
    count = 0
    for row in rows:
        prev_sha = row.get("prev_sha")
        body = row.get("payload_json")
        claimed = row.get("sha256")
        if prev_sha != previous:
            return False, count, f"row {count + 1}: prev_sha does not match the previous digest"
        if not isinstance(body, str) or not isinstance(claimed, str):
            return False, count, f"row {count + 1}: body or digest is missing"
        if sha256_text(previous + body) != claimed:
            return False, count, f"row {count + 1}: digest mismatch (row was edited)"
        previous = claimed
        count += 1
    return True, count, None
