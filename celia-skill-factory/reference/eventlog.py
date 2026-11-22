"""Hash-chained event log — closes P1 (tamper detection) and P1b (anchor).

Three layers, as specified in the review:

  1. CHAIN      sequence + previous_event_sha256 + event_sha256
                -> detects edit, reorder, and mid-file deletion.
  2. ANCHOR     independent head anchor (sequence, head_sha256)
                -> detects tail truncation and rollback to older state.
  3. DIGESTS    evidence digests bound to approval/release events
                -> detects substituted evidence (see evidence.py).

Honest limit, stated in code and in the ADR: an actor with write access
to BOTH the log and the anchor can rewrite history consistently. That is
the TRUSTED-ADMIN THREAT, and no in-repo mechanism defeats it. It is
reported as BLOCKED, not claimed as solved.

$0: standard library only.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable

from . import canonical

GENESIS = canonical.GENESIS


class LogIntegrityError(Exception):
    """The log failed verification. `reason` is a stable machine code."""

    def __init__(self, reason: str, detail: str = "", line: int | None = None):
        self.reason = reason
        self.detail = detail
        self.line = line
        super().__init__(f"{reason}: {detail}" + (f" (line {line})" if line else ""))


@dataclass(frozen=True)
class Event:
    sequence: int
    event_type: str
    payload: dict[str, Any]
    previous_event_sha256: str
    event_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "event_type": self.event_type,
            "payload": self.payload,
            "previous_event_sha256": self.previous_event_sha256,
            "event_sha256": self.event_sha256,
        }


def _body(sequence: int, event_type: str, payload: dict[str, Any], prev: str) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "event_type": event_type,
        "payload": payload,
        "previous_event_sha256": prev,
    }


def make_event(
    sequence: int, event_type: str, payload: dict[str, Any], prev: str
) -> Event:
    body = _body(sequence, event_type, payload, prev)
    return Event(
        sequence=sequence,
        event_type=event_type,
        payload=payload,
        previous_event_sha256=prev,
        event_sha256=canonical.digest(body),
    )


class EventLog:
    """Append-only, hash-chained log backed by a single JSONL file."""

    def __init__(self, path: str):
        self.path = path
        self._events: list[Event] = []
        if os.path.exists(path):
            self._load()

    # ---------- read ----------

    def _load(self) -> None:
        with open(self.path, "r", encoding="utf-8", newline="") as fh:
            raw = fh.read()
        self._events = []
        for i, line in enumerate(raw.split("\n")[:-1] if raw.endswith("\n") else raw.split("\n"), 1):
            if not line:
                continue
            obj = canonical.loads_strict(line)
            ev = Event(
                sequence=obj["sequence"],
                event_type=obj["event_type"],
                payload=obj["payload"],
                previous_event_sha256=obj["previous_event_sha256"],
                event_sha256=obj["event_sha256"],
            )
            # recompute: a stored hash that does not match the body is tampering
            expected = canonical.digest(_body(ev.sequence, ev.event_type, ev.payload, ev.previous_event_sha256))
            if expected != ev.event_sha256:
                raise LogIntegrityError("HASH_MISMATCH", f"event_sha256 recomputation failed", i)
            self._events.append(ev)

        # A tampered log must not even OPEN: verify sequence continuity and
        # link integrity at load time, not only on an explicit verify() call.
        self.verify_chain()

    @property
    def head(self) -> str:
        return self._events[-1].event_sha256 if self._events else GENESIS

    @property
    def sequence(self) -> int:
        return self._events[-1].sequence if self._events else 0

    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    # ---------- write ----------

    def append(self, event_type: str, payload: dict[str, Any]) -> Event:
        ev = make_event(self.sequence + 1, event_type, payload, self.head)
        line = canonical.dumps(ev.to_dict()) + "\n"
        with open(self.path, "a", encoding="utf-8", newline="") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        self._events.append(ev)
        return ev

    # ---------- verify ----------

    def verify_chain(self) -> None:
        """Detect reorder, edit, gap, and mid-file deletion."""
        prev = GENESIS
        for i, ev in enumerate(self._events, 1):
            if ev.sequence != i:
                raise LogIntegrityError(
                    "SEQUENCE_GAP", f"expected sequence {i}, found {ev.sequence}", i
                )
            if ev.previous_event_sha256 != prev:
                raise LogIntegrityError(
                    "CHAIN_BROKEN",
                    f"previous_event_sha256 {ev.previous_event_sha256[:8]} != {prev[:8]}",
                    i,
                )
            expected = canonical.digest(
                _body(ev.sequence, ev.event_type, ev.payload, ev.previous_event_sha256)
            )
            if expected != ev.event_sha256:
                raise LogIntegrityError("HASH_MISMATCH", "event body does not match its hash", i)
            prev = ev.event_sha256


class Anchor:
    """Independent head anchor — detects tail truncation and rollback.

    Stored OUTSIDE the log file. In-repo placement is a known weakness
    (TRUSTED_ADMIN): record it, do not hide it.
    """

    def __init__(self, path: str):
        self.path = path

    def read(self) -> tuple[int, str] | None:
        if not os.path.exists(self.path):
            return None
        with open(self.path, encoding="utf-8") as fh:
            obj = canonical.loads_strict(fh.read())
        return obj["sequence"], obj["head_sha256"]

    def write(self, log: EventLog) -> None:
        obj = {"sequence": log.sequence, "head_sha256": log.head}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(canonical.dumps(obj))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    def verify_against(self, log: EventLog) -> None:
        """Fail if the log is behind, ahead, or diverged from the anchor."""
        recorded = self.read()
        if recorded is None:
            return  # no anchor yet: chain verification alone
        seq, head = recorded

        # 1) The anchored event must still exist and still hash to the same value.
        anchored = next((ev for ev in log.events() if ev.sequence == seq), None)
        if anchored is None:
            # Log is shorter than the anchor: the tail was cut off, or an
            # older snapshot was restored over a newer one.
            raise LogIntegrityError(
                "TRUNCATED",
                f"anchored event {seq} is missing (log has {log.sequence})",
            )
        if anchored.event_sha256 != head:
            raise LogIntegrityError(
                "ANCHOR_MISMATCH",
                f"anchored event {seq} was rewritten "
                f"({anchored.event_sha256[:8]} != {head[:8]})",
            )

        # 2) The log may legitimately be ahead of the anchor, never behind.
        if log.sequence < seq:
            raise LogIntegrityError(
                "TRUNCATED", f"log is behind the anchor ({log.sequence} < {seq})"
            )


def verify_all(log: EventLog, anchor: Anchor) -> None:
    log.verify_chain()
    anchor.verify_against(log)


def replay_types(events: Iterable[Event]) -> list[str]:
    return [e.event_type for e in events]
