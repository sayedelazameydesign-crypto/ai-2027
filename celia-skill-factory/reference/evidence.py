"""Evidence manifests and the release gate — closes P2 (evidence integrity).

An approval that references "evidence" proves nothing unless the evidence
itself is pinned. Here every evidence bundle is hashed two ways:

  - per file:   sha256 of the file bytes
  - per bundle: sha256 of the canonical manifest of those digests

The approval event records the bundle digest, and the release event records
the approval digest. Substituting a file, reordering the manifest, or
dropping an entry all change the bundle digest and break the gate.

$0: standard library only.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable

from . import canonical
from .eventlog import EventLog


class GateError(Exception):
    """Release refused. `blockers` lists stable machine codes."""

    def __init__(self, blockers: list[str], detail: str = ""):
        self.blockers = blockers
        self.detail = detail
        super().__init__("RELEASE_BLOCKED: " + ", ".join(blockers) + (f" | {detail}" if detail else ""))


@dataclass(frozen=True)
class EvidenceEntry:
    path: str
    sha256: str
    size: int


def _norm_path(path: str) -> str:
    """Reject traversal and absolute paths, THEN normalize.

    Order matters: `lstrip("./")` applied first would turn
    "../../etc/passwd" into "etc/passwd" and silently accept the escape.
    """
    raw = path.replace(os.sep, "/")
    if raw.startswith("/") or ".." in raw.split("/"):
        raise GateError(["PATH_ESCAPE"], f"evidence path {path!r} escapes the bundle")
    p = raw[2:] if raw.startswith("./") else raw
    if not p or p.startswith("/"):
        raise GateError(["PATH_ESCAPE"], f"evidence path {path!r} escapes the bundle")
    return p


def digest_file(path: str) -> str:
    h = __import__("hashlib").sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(root: str, rel_paths: Iterable[str]) -> list[EvidenceEntry]:
    """Digest every evidence file listed, sorted by normalized path."""
    entries: list[EvidenceEntry] = []
    for rel in rel_paths:
        norm = _norm_path(rel)
        full = os.path.join(root, norm)
        if not os.path.isfile(full):
            raise GateError(["EVIDENCE_MISSING"], f"{norm} does not exist")
        if os.path.islink(full):
            raise GateError(["SYMLINK_REJECTED"], f"{norm} is a symlink")
        entries.append(EvidenceEntry(norm, digest_file(full), os.path.getsize(full)))
    entries.sort(key=lambda e: e.path)
    if len({e.path for e in entries}) != len(entries):
        raise GateError(["DUPLICATE_EVIDENCE"], "the same path appears twice")
    return entries


def bundle_digest(entries: Iterable[EvidenceEntry]) -> str:
    """Single digest over the whole bundle, order-independent by construction."""
    payload = [{"path": e.path, "sha256": e.sha256, "size": e.size}
               for e in sorted(entries, key=lambda e: e.path)]
    return canonical.digest({"evidence": payload})


# --------------------------------------------------------------------------
# Release gate
# --------------------------------------------------------------------------

REQUIRED_AT_RELEASE = ("TESTS_PASSED", "EVIDENCE_PINNED", "HUMAN_APPROVED", "NO_BLOCKED")


@dataclass
class ReleaseRequest:
    approval_sha256: str
    evidence_digest: str
    approval_evidence_digest: str
    tests_passed: bool
    blocked_items: list[str]
    human_approver: str


def check_release(req: ReleaseRequest, log: EventLog) -> list[str]:
    """Return the list of blockers. Empty list means the gate is open."""
    blockers: list[str] = []

    if not req.human_approver.strip():
        blockers.append("HUMAN_APPROVED")

    if not req.tests_passed:
        blockers.append("TESTS_PASSED")

    if req.blocked_items:
        blockers.append("NO_BLOCKED")

    # evidence must be the same bundle the human approved
    if not req.evidence_digest or req.evidence_digest != req.approval_evidence_digest:
        blockers.append("EVIDENCE_PINNED")

    # approval must exist in the log and be an APPROVED transition
    known = {ev.event_sha256 for ev in log.events()}
    if not req.approval_sha256 or req.approval_sha256 not in known:
        blockers.append("APPROVAL_NOT_IN_LOG")

    return blockers


def assert_release_allowed(req: ReleaseRequest, log: EventLog) -> None:
    blockers = check_release(req, log)
    if blockers:
        raise GateError(blockers)


__all__ = [
    "EvidenceEntry", "build_manifest", "bundle_digest", "digest_file",
    "ReleaseRequest", "check_release", "assert_release_allowed",
    "GateError", "REQUIRED_AT_RELEASE",
]
