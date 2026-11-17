#!/usr/bin/env python3
"""Phase 2A — a local watchdog with a tamper-EVIDENT event log.

Pure standard library. No third-party imports; a CI job parses this file with
``ast`` and fails the build if that stops being true.

Why this exists
---------------
Render cannot be the thing that watches Render. A monitor hosted on the platform
it monitors dies with it, and its silence is indistinguishable from "all fine".
So the loop runs locally and writes to a log on disk. That log is the only
evidence that the loop ever ran — which means the log has to be trustworthy in a
way that a plain JSONL file is not.

Three properties are implemented, and each one is tested against a way it fails:

1. **Tamper-evident, with a stated boundary.** Records form a SHA-256 hash chain:
   every record commits to the hash of the one before it. What that actually buys
   you, tested rather than asserted:

   | Attack                                   | Caught by | Caught? |
   |------------------------------------------|-----------|---------|
   | change a value in place                  | hash_mismatch  | yes |
   | reorder two records                      | prev_mismatch  | yes |
   | delete/replace a record, hashes untouched | seq_break, prev_mismatch | yes |
   | rewrite the WHOLE file, recompute all hashes | **nothing** | **no** |
   | ...the same rewrite, with an anchor      | anchor_mismatch | yes |

   The fourth row is not a bug to be fixed by a cleverer chain — no purely local
   chain can beat an attacker who controls the whole file. Measured, not assumed:
   a fully recomputed rewrite verifies as intact when there is no anchor, and
   fails when there is one (test_13, test_30). Confusing "tamper-evident" with
   "tamper-proof" is how people end up trusting a log an attacker rewrote.

2. **Gaps are recorded, never smoothed.** A watchdog that silently skips a window
   and reports a clean series has produced a lie. Two different gaps are kept
   distinct on purpose:
     * ``silence``     — the process was not running or not recording.
     * ``poll_failed`` — the process was alive, the source was not reachable.
   Collapsing those into one "no data" bucket destroys the only information that
   tells you whether your host died or your network did.

3. **Crash-safe.** A power loss can leave a half-written line. That is detected,
   reported as ``truncated_tail`` (a crash, *not* forgery), and repaired on the
   next append so the chain does not corrupt every later verification. The bytes
   dropped by the repair are recorded.

LIMIT — read this before believing the word "tamper-evident"
------------------------------------------------------------
A hash chain proves the *file as stored* was not edited in place. It does **not**
stop an attacker who can rewrite the whole file from rewriting every hash too.
The defence against that is an **anchor**: the head hash written somewhere else
(``--anchor``). With an anchor, a full rewrite is detected as long as the attacker
could not also rewrite the anchor. If both files live on the same disk and the
attacker has the disk, nothing here helps — use an off-machine anchor
(pasted into a commit, a ticket, or a second host). This module will not pretend
otherwise.

Usage
-----
    python3 monitoring/local_watchdog.py run    --log watch.jsonl --interval 60
    python3 monitoring/local_watchdog.py run    --log watch.jsonl --polls 5 --anchor watch.anchor
    python3 monitoring/local_watchdog.py verify --log watch.jsonl --anchor watch.anchor
    python3 monitoring/local_watchdog.py report --log watch.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]

RECORD_VERSION = 1
GENESIS = "genesis"


# --------------------------------------------------------------------------- #
# hashing
# --------------------------------------------------------------------------- #
def canonical(obj) -> bytes:
    """Deterministic bytes for hashing.

    ``sort_keys`` and ``separators`` matter: without them the same record hashes
    differently across Python versions and the chain breaks for no reason.
    ``default=str`` means a source returning a datetime is recorded as text
    instead of crashing the loop mid-write.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode("utf-8")


def digest(obj) -> str:
    return "sha256:" + hashlib.sha256(canonical(obj)).hexdigest()


def record_hash(record: dict) -> str:
    """Hash of a record excluding its own ``hash`` field."""
    body = {k: v for k, v in record.items() if k != "hash"}
    return digest(body)


def iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


# --------------------------------------------------------------------------- #
# the log
# --------------------------------------------------------------------------- #
class EventLog:
    """Append-only, hash-chained JSONL. Every write takes an exclusive lock."""

    def __init__(self, path: str | Path, anchor_path: str | Path | None = None,
                 fsync: bool = True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.anchor_path = Path(anchor_path) if anchor_path else None
        if self.anchor_path:
            self.anchor_path.parent.mkdir(parents=True, exist_ok=True)
        self.fsync = fsync
        self._lock_path = self.path.with_name(self.path.name + ".lock")

    # -- locking ----------------------------------------------------------- #
    def _acquire(self):
        fd = os.open(self._lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def _release(self, fd: int) -> None:
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    # -- tail handling ----------------------------------------------------- #
    def head(self) -> dict | None:
        """Last complete record, or None. Reads only the tail of the file."""
        if not self.path.exists():
            return None
        size = self.path.stat().st_size
        if size == 0:
            return None
        window = 65536
        with open(self.path, "rb") as fh:
            start = max(0, size - window)
            fh.seek(start)
            chunk = fh.read()
        lines = chunk.split(b"\n")
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if isinstance(rec, dict) and "hash" in rec and "seq" in rec:
                return rec
        if start == 0:
            return None
        # Tail window contained no full record: a single record larger than the
        # window, or a torn file. Fall back to a full read rather than guessing.
        for line in reversed(self.path.read_bytes().split(b"\n")):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if isinstance(rec, dict) and "hash" in rec and "seq" in rec:
                return rec
        return None

    def _repair_tail(self) -> int:
        """Drop a half-written final line. Returns the number of bytes dropped.

        A crash mid-``write`` leaves a line with no trailing newline. If the next
        append simply writes after it, that fragment fuses with the new record
        and *every* later verification fails at a random index. Repairing it at
        append time keeps one crash local to one record, and the dropped byte
        count is written into the log so the loss is visible rather than secret.
        """
        if not self.path.exists():
            return 0
        with open(self.path, "rb+") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            if size == 0:
                return 0
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) == b"\n":
                return 0
            fh.seek(0)
            data = fh.read()
            keep = data.rfind(b"\n") + 1
            dropped = len(data) - keep
            fh.seek(keep)
            fh.truncate()
            fh.flush()
            os.fsync(fh.fileno())
            return dropped

    # -- append ------------------------------------------------------------ #
    def append(self, kind: str, data: dict | None = None,
               ts: float | None = None) -> dict:
        lock = self._acquire()
        try:
            repaired = self._repair_tail()
            head = self.head()
            seq = 0 if head is None else int(head["seq"]) + 1
            prev = GENESIS if head is None else head["hash"]
            ts = time.time() if ts is None else ts
            record = {
                "v": RECORD_VERSION,
                "seq": seq,
                "ts": ts,
                "ts_iso": iso(ts),
                "kind": kind,
                "prev": prev,
                "data": data or {},
            }
            record["hash"] = record_hash(record)
            self._write_line(record)
            if repaired:
                # Recorded AFTER the repaired record so the chain stays linear;
                # `seq` points back at the fragment that was dropped.
                note = {
                    "v": RECORD_VERSION,
                    "seq": seq + 1,
                    "ts": ts,
                    "ts_iso": iso(ts),
                    "kind": "tail_repaired",
                    "prev": record["hash"],
                    "data": {"dropped_bytes": repaired, "before_seq": seq},
                }
                note["hash"] = record_hash(note)
                self._write_line(note)
                return note
            return record
        finally:
            self._release(lock)

    def _write_line(self, record: dict) -> None:
        line = canonical(record) + b"\n"
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line)
            if self.fsync:
                os.fsync(fd)
        finally:
            os.close(fd)

    # -- read / verify ----------------------------------------------------- #
    def read_all(self) -> tuple[list[dict], list[dict]]:
        records: list[dict] = []
        problems: list[dict] = []
        if not self.path.exists():
            return records, problems
        raw = self.path.read_bytes()
        torn = bool(raw) and not raw.endswith(b"\n")
        if torn:
            problems.append({
                "type": "truncated_tail",
                "detail": "file does not end with a newline: a write was interrupted",
            })
        lines = raw.split(b"\n")
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception as exc:
                # The final fragment of an interrupted write is ALREADY described
                # by `truncated_tail`. Reporting it a second time as an
                # unparseable line made it count as tampering, so a power cut
                # looked identical to a forged log. A crash and an attacker are
                # different facts and must stay different in the output.
                if torn and i == len(lines) - 1:
                    continue
                problems.append({"type": "unparseable_line", "index": i,
                                 "detail": f"{type(exc).__name__}"})
                continue
            if not isinstance(rec, dict):
                problems.append({"type": "unparseable_line", "index": i,
                                 "detail": "line is not an object"})
                continue
            records.append(rec)
        return records, problems

    def verify(self) -> dict:
        """Walk the chain.

        ``intact`` answers one question only: *was this file edited?*
        A crash (``truncated_tail``) is reported separately because a power cut
        is not an attacker, and conflating them trains you to ignore the alarm.
        """
        records, problems = self.read_all()
        tamper: list[dict] = []
        prev = GENESIS
        for i, rec in enumerate(records):
            if rec.get("seq") != i:
                tamper.append({"type": "seq_break", "index": i,
                               "expected": i, "found": rec.get("seq")})
            if rec.get("prev") != prev:
                tamper.append({"type": "prev_mismatch", "index": i,
                               "expected": prev[:23], "found": str(rec.get("prev"))[:23]})
            if rec.get("hash") != record_hash(rec):
                tamper.append({"type": "hash_mismatch", "index": i})
            prev = rec.get("hash")

        anchor_problems = self.verify_anchor(records)
        truncated = any(p["type"] == "truncated_tail" for p in problems)
        other_problems = [p for p in problems if p["type"] != "truncated_tail"]

        return {
            "records": len(records),
            "intact": not (tamper or anchor_problems or other_problems),
            "truncated_tail": truncated,
            "tamper_problems": tamper,
            "other_problems": other_problems,
            "anchor_problems": anchor_problems,
            "head": records[-1]["hash"] if records else GENESIS,
            "clean": not (tamper or anchor_problems or other_problems or truncated),
        }

    # -- anchor ------------------------------------------------------------ #
    def write_anchor(self) -> str | None:
        """Record the current head hash outside the log file."""
        if not self.anchor_path:
            return None
        head = self.head()
        if head is None:
            return None
        with open(self.anchor_path, "a", encoding="utf-8") as fh:
            fh.write(f"{iso(time.time())} {head['seq']} {head['hash']}\n")
            fh.flush()
            os.fsync(fh.fileno())
        return head["hash"]

    def verify_anchor(self, records: list[dict]) -> list[dict]:
        problems: list[dict] = []
        if not self.anchor_path or not self.anchor_path.exists():
            return problems
        for line in self.anchor_path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) != 3:
                continue
            _, seq_s, head = parts
            try:
                seq = int(seq_s)
            except ValueError:
                continue
            if seq >= len(records):
                problems.append({"type": "anchor_ahead", "seq": seq,
                                 "detail": "log is shorter than an anchored head"})
                continue
            if records[seq].get("hash") != head:
                problems.append({"type": "anchor_mismatch", "seq": seq,
                                 "detail": "record at an anchored seq no longer hashes "
                                           "to the anchored value (log was rewritten)"})
        return problems


# --------------------------------------------------------------------------- #
# state diffing
# --------------------------------------------------------------------------- #
def diff_snapshots(before: dict | None, after: dict | None) -> list[dict]:
    """Changed keys between two flat snapshots.

    Flat on purpose: the transition record has to be readable by a human at 3am,
    and nested-dict diffs are how "nothing changed" gets reported wrongly.
    """
    before = before or {}
    after = after or {}
    out = []
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key), after.get(key)
        if b != a:
            out.append({"key": key, "before": b, "after": a})
    return out


def last_snapshot(records: list[dict]) -> dict | None:
    for rec in reversed(records):
        if rec.get("kind") == "poll":
            snap = (rec.get("data") or {}).get("snapshot")
            if isinstance(snap, dict):
                return snap
    return None


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #
def run(interval: float = 60.0, log: EventLog | None = None, source=None,
        clock=time.time, sleep=time.sleep, max_polls: int | None = None,
        duration: float | None = None, tolerance: float = 0.5,
        min_gap: float = 1.0, on_record=None) -> dict:
    """Poll ``source`` on a fixed cadence, recording everything that happened.

    ``clock`` and ``sleep`` are injected so the arithmetic of silence, drift and
    missed intervals is tested exactly, with no test that waits 60 real seconds.

    ``min_gap`` is the shortest window worth calling an outage. Below it, a lateness
    is scheduler jitter or lock contention, not silence — a 3 ms gap is not
    something you can act on, and recording it as an incident is how a log gets
    ignored. Suppressed gaps are **counted, not hidden**: ``stop`` carries
    ``jitter_suppressed`` and the largest one, so you can see the noise level and
    argue with the threshold instead of discovering it was applied silently.
    """
    if log is None:
        raise ValueError("a log is required")
    started = clock()

    # 1. Was the loop absent before it started? Compare with the last record on
    #    disk — an unclean kill leaves no `stop`, so this is often the only trace
    #    that the host was asleep.
    prior = log.head()
    if prior is not None:
        gap = started - float(prior.get("ts", started))
        # Negative gap = another writer appended after we read the head. That is
        # concurrency, not silence.
        if gap > interval * (1 + tolerance) and gap >= min_gap:
            log.append("silence", {
                "gap_seconds": round(gap, 3),
                "missed_intervals": max(0, int(gap // interval) - 1),
                "reason": "loop was not running (no stop record)",
                "after_seq": prior.get("seq"),
            })

    class _Jitter:
        count = 0
        largest = 0.0

    jitter = _Jitter()

    records, _ = log.read_all()
    snapshot = last_snapshot(records)

    log.append("start", {"pid": os.getpid(), "interval": interval,
                         "tolerance": tolerance})

    polls = 0
    failures = 0
    expected = clock()
    while True:
        if max_polls is not None and polls >= max_polls:
            break
        expected += interval
        now = clock()
        drift = now - expected
        # Only a LATE start is drift. Being early (now < expected) is not a gap,
        # and recording it as one produced 92 "silence" records of 0.002s in a
        # 4-writer test — sub-millisecond scheduling noise reported as outages.
        # A log full of fake incidents is worse than no log: it trains the reader
        # to ignore the one entry that matters.
        missed = int(drift // interval) if drift > 0 else 0
        if drift > interval * tolerance and missed >= 1:
            if drift < min_gap:
                # Too short to be an outage, but NOT discarded from the record.
                jitter.count += 1
                jitter.largest = max(jitter.largest, drift)
            else:
                log.append("silence", {
                    "gap_seconds": round(drift, 3),
                    "missed_intervals": missed,
                    "reason": "scheduler drift or a poll that overran its slot",
                })
            expected = now

        try:
            fresh = source() if source is not None else None
            transitions = diff_snapshots(snapshot, fresh)
            if isinstance(fresh, dict):
                snapshot = fresh
            rec = log.append("poll", {"snapshot": fresh,
                                      "transitions": transitions})
        except Exception as exc:  # noqa: BLE001 - the loop must survive a dead source
            # The log stays valid and the failure is recorded WITH its class.
            # A watchdog that dies when the network dies is a watchdog that
            # reports "all clear" exactly when it is most wrong.
            failures += 1
            rec = log.append("poll_failed", {
                "error_class": type(exc).__name__,
                "message": str(exc)[:300],
                "was_alive": True,
            })
        polls += 1
        if on_record is not None:
            on_record(rec)

        if duration is not None and (clock() - started) >= duration:
            break
        remaining = expected - clock()
        if remaining > 0:
            sleep(remaining)

    stop = log.append("stop", {"polls": polls, "failures": failures,
                               "elapsed_seconds": round(clock() - started, 3),
                               "min_gap": min_gap,
                               # Visible, not silent: how much lateness was too
                               # short to call an outage.
                               "jitter_suppressed": jitter.count,
                               "jitter_largest_seconds": round(jitter.largest, 4)})
    if on_record is not None:
        on_record(stop)
    log.write_anchor()
    return {"polls": polls, "failures": failures, "stop": stop}


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def report(records: list[dict]) -> dict:
    """Summarise the log without inventing anything it does not contain."""
    kinds: dict[str, int] = {}
    for rec in records:
        kinds[rec.get("kind", "?")] = kinds.get(rec.get("kind", "?"), 0) + 1

    silences = [r for r in records if r.get("kind") == "silence"]
    failures = [r for r in records if r.get("kind") == "poll_failed"]
    transitions = [t for r in records if r.get("kind") == "poll"
                   for t in (r.get("data") or {}).get("transitions") or []]

    total_silence = sum(float((s.get("data") or {}).get("gap_seconds", 0))
                        for s in silences)
    span = None
    if records:
        span = round(float(records[-1]["ts"]) - float(records[0]["ts"]), 3)

    return {
        "records": len(records),
        "span_seconds": span,
        "kinds": kinds,
        "silence_events": len(silences),
        "silence_seconds_total": round(total_silence, 3),
        "silence_seconds_largest": round(max(
            [float((s.get("data") or {}).get("gap_seconds", 0)) for s in silences],
            default=0.0), 3),
        "poll_failures": len(failures),
        "failure_classes": sorted({(f.get("data") or {}).get("error_class", "?")
                                   for f in failures}),
        "transitions": transitions,
        "last_snapshot": last_snapshot(records),
        # The honest coverage number: what fraction of the wall clock the loop
        # can actually vouch for.
        "observed_fraction": (
            round(1 - total_silence / span, 4) if span else None
        ),
    }


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #
def _cmd_run(args) -> int:
    log = EventLog(args.log, anchor_path=args.anchor, fsync=not args.no_fsync)
    source = None
    if args.source:
        source = _load_source(args.source)
    result = run(interval=args.interval, log=log, source=source,
                 max_polls=args.polls, duration=args.duration,
                 tolerance=args.tolerance, min_gap=args.min_gap)
    print(json.dumps({"polls": result["polls"], "failures": result["failures"]},
                     indent=2))
    return 0


def _load_source(spec: str):
    """``module:function`` or a JSON file that is returned verbatim (fixtures)."""
    if spec.endswith(".json"):
        path = Path(spec)

        def from_file():
            return json.loads(path.read_text(encoding="utf-8"))

        return from_file
    module_name, _, func_name = spec.partition(":")
    if not func_name:
        raise SystemExit(f"--source must be 'module:function' or a .json file, got {spec!r}")
    import importlib
    module = importlib.import_module(module_name)
    return getattr(module, func_name)


def _cmd_verify(args) -> int:
    log = EventLog(args.log, anchor_path=args.anchor)
    result = log.verify()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result["intact"]:
        print("TAMPER DETECTED — this log is not what it claims to be", file=sys.stderr)
        return 1
    if result["truncated_tail"]:
        print("log intact; a write was interrupted (crash, not forgery)",
              file=sys.stderr)
        return 3
    print("log intact and clean", file=sys.stderr)
    return 0


def _cmd_report(args) -> int:
    log = EventLog(args.log, anchor_path=args.anchor)
    records, _ = log.read_all()
    print(json.dumps(report(records), indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="poll on a cadence and record it")
    r.add_argument("--log", required=True)
    r.add_argument("--anchor", default=None)
    r.add_argument("--interval", type=float, default=60.0)
    r.add_argument("--polls", type=int, default=None)
    r.add_argument("--duration", type=float, default=None)
    r.add_argument("--tolerance", type=float, default=0.5)
    r.add_argument("--min-gap", type=float, default=1.0,
                   help="shortest gap worth recording as silence, in seconds")
    r.add_argument("--source", default=None,
                   help="'module:function' or a .json fixture file")
    r.add_argument("--no-fsync", action="store_true")
    r.set_defaults(func=_cmd_run)

    v = sub.add_parser("verify", help="walk the hash chain")
    v.add_argument("--log", required=True)
    v.add_argument("--anchor", default=None)
    v.set_defaults(func=_cmd_verify)

    p = sub.add_parser("report", help="summarise the log")
    p.add_argument("--log", required=True)
    p.add_argument("--anchor", default=None)
    p.set_defaults(func=_cmd_report)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
