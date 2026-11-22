"""Draft lifecycle log — closes P1b.

The constitution requires every draft transition to emit an audit event,
but only the release/revocation log was defined. Mixing the two would make
a draft rename look like a release, so drafts get their own log:

    skills/drafts/{draft_id}/events.jsonl

Same chain rules as the release log, same verification, separate file and
separate anchor. A draft transition can never be mistaken for a release.

$0: standard library only.
"""
from __future__ import annotations

import os
import re
from typing import Any

from .eventlog import Anchor, Event, EventLog, verify_all

DRAFT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")

# The constitution's draft lifecycle. Transitions not listed are rejected.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "DRAFT": ("SUBMITTED", "ABANDONED"),
    "SUBMITTED": ("IN_REVIEW", "ABANDONED"),
    "IN_REVIEW": ("CHANGES_REQUESTED", "APPROVED", "ABANDONED"),
    "CHANGES_REQUESTED": ("SUBMITTED", "ABANDONED"),
    "APPROVED": ("PUBLISHED", "ABANDONED"),
    "PUBLISHED": (),
    "ABANDONED": (),
}

TERMINAL = ("PUBLISHED", "ABANDONED")


class DraftError(Exception):
    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


def valid_draft_id(draft_id: str) -> bool:
    return bool(DRAFT_ID_RE.match(draft_id))


class DraftLog:
    """Per-draft append-only transition log."""

    def __init__(self, root: str, draft_id: str):
        if not valid_draft_id(draft_id):
            raise DraftError("INVALID_DRAFT_ID", f"{draft_id!r} fails {DRAFT_ID_RE.pattern}")
        self.draft_id = draft_id
        self.dir = os.path.join(root, "skills", "drafts", draft_id)
        os.makedirs(self.dir, exist_ok=True)
        self.log = EventLog(os.path.join(self.dir, "events.jsonl"))
        self.anchor = Anchor(os.path.join(self.dir, "anchor.json"))
        self.log.verify_chain()          # fail fast on a tampered draft log
        self.anchor.verify_against(self.log)

    # ---------- state ----------

    def state(self) -> str:
        for ev in reversed(self.log.events()):
            if ev.event_type == "DRAFT_TRANSITION":
                return ev.payload["to_state"]
        return "DRAFT"

    def _commit(self) -> None:
        self.anchor.write(self.log)

    # ---------- transitions ----------

    def transition(self, to_state: str, actor: str, reason: str = "") -> Event:
        current = self.state()
        if to_state not in TRANSITIONS.get(current, ()):
            raise DraftError(
                "ILLEGAL_TRANSITION",
                f"{current} -> {to_state} not allowed "
                f"(allowed: {', '.join(TRANSITIONS.get(current, ())) or 'none'})",
            )
        if not actor:
            raise DraftError("MISSING_ACTOR", "every transition needs an actor")
        ev = self.log.append(
            "DRAFT_TRANSITION",
            {"draft_id": self.draft_id, "from_state": current, "to_state": to_state,
             "actor": actor, "reason": reason},
        )
        self._commit()
        return ev

    def attach(self, kind: str, digest: str, actor: str) -> Event:
        """Attach an artefact digest to the draft (evidence, tests, signature)."""
        if self.state() in TERMINAL:
            raise DraftError("DRAFT_CLOSED", f"cannot attach to a {self.state()} draft")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise DraftError("BAD_DIGEST", f"{digest!r} is not a sha256 hex digest")
        ev = self.log.append(
            "DRAFT_ARTEFACT",
            {"draft_id": self.draft_id, "kind": kind, "sha256": digest, "actor": actor},
        )
        self._commit()
        return ev

    def verify(self) -> None:
        verify_all(self.log, self.anchor)

    # ---------- aggregate checks ----------

    def events(self) -> tuple[Event, ...]:
        return self.log.events()

    def count(self) -> int:
        return len(self.log.events())


def draft_events_path(root: str, draft_id: str) -> str:
    """The exact path the ADR commits to."""
    return os.path.join(root, "skills", "drafts", draft_id, "events.jsonl")


__all__ = [
    "DraftLog", "DraftError", "TRANSITIONS", "TERMINAL",
    "valid_draft_id", "draft_events_path",
]
