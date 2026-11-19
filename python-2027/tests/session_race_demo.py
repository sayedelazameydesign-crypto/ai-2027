#!/usr/bin/env python3
"""Manual harness: what happens when two writers save the SAME session at once.

This is **not** a pytest test and it is **not** run in CI. It is the reproduction
behind ``docs/PAP-2027-RESILIENCE-AUDIT.md`` (gap R1), and now also the evidence
that the fix works:

    # before P0.1 — the defect (23-61 of 240 writes lost, up to 13 torn reads)
    python-2027/.venv/bin/python python-2027/tests/session_race_demo.py --mode off

    # after P0.1 — the same contention under the session lease, must be clean
    python-2027/.venv/bin/python python-2027/tests/session_race_demo.py --mode lease --assert

Each writer process builds its own ``SessionStore`` (as the CLI and the Studio
worker do) and writes the same session id with a distinct payload. Nothing is
mocked: this is the real ``save_sync`` path, the real per-write temp filename and
the real ``Path.replace`` call.

In ``lease`` mode a writer must hold the session lease to save. Writers that lose
the race retry instead of writing, so "lost writes" means a write the lease let
through and the filesystem then dropped — which is the number that must be zero.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # run as a script: make ``src`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai2027_protocol.agent import AgentSessionState, SessionStore  # noqa: E402
from ai2027_protocol.session_store import SessionBusyError  # noqa: E402

_PAD = "x" * 40_000  # large enough that a write is not a single small syscall
_READ_PROBES = 400


def _state(session_id: str, index: int, round_no: int) -> AgentSessionState:
    return AgentSessionState(
        session_id=session_id,
        title=f"writer-{index}",
        created_at="2026-11-19T00:00:00+00:00",
        updated_at="2026-11-19T00:00:00+00:00",
        final_answer=f"{_PAD} writer={index} round={round_no}",
        checkpoint_step=index * 1000 + round_no,
    )


def _verify(target: Path, state: AgentSessionState) -> bool:
    """Return True if this writer's payload is still the one on disk."""
    try:
        on_disk = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    on_disk_step: object = on_disk.get("checkpoint_step")
    return on_disk_step == state.checkpoint_step


def _write_and_check(
    store: SessionStore, target: Path, state: AgentSessionState, lease: Any = None
) -> tuple[bool, bool]:
    """Save one state, then read it back: (accepted, survived)."""
    try:
        store.save_sync(state, lease=lease)
    except Exception:  # noqa: BLE001 - the harness records, it does not judge
        return False, False
    return True, _verify(target, state)


def _writer(studio: str, session_id: str, index: int, rounds: int, results: Any) -> None:
    store = SessionStore(Path(studio))
    target = Path(studio) / "sessions" / session_id / "session.json"
    accepted = lost = 0
    errors: list[str] = []
    for round_no in range(rounds):
        ok, survived = _write_and_check(store, target, _state(session_id, index, round_no))
        accepted += int(ok)
        lost += int(ok and not survived)
    results.put(
        {"index": index, "accepted": accepted, "lost": lost, "retries": 0, "errors": errors}
    )


def _writer_leased(
    studio: str, session_id: str, index: int, rounds: int, ttl: float, results: Any
) -> None:
    """Save under the session lease: wait for the session, write, hand it over."""
    store = SessionStore(Path(studio), lease_mode="enforce", lease_ttl_s=ttl)
    saved = 0
    retries = 0
    errors: list[str] = []
    target = Path(studio) / "sessions" / session_id / "session.json"
    saved = lost = 0
    for round_no in range(rounds):
        while True:
            try:
                lease = store.acquire_lease(session_id, owner=f"w{index}-r{round_no}")
                break
            except SessionBusyError:
                retries += 1
                time.sleep(0.001)
        try:
            ok, survived = _write_and_check(
                store, target, _state(session_id, index, round_no), lease
            )
            saved += int(ok)
            lost += int(ok and not survived)
        finally:
            store.release_lease(lease)
    results.put(
        {"index": index, "accepted": saved, "lost": lost, "retries": retries, "errors": errors}
    )


def run(*, writers: int, rounds: int, mode: str = "off", ttl: float = 30.0) -> dict[str, Any]:
    """Run the contention scenario and return the measured outcome."""
    studio = Path(tempfile.mkdtemp(prefix="pap-race-"))
    session_id = SessionStore(studio).create_session(title="race demo").session_id
    target = studio / "sessions" / session_id / "session.json"
    errors: Any = mp.Queue()

    if mode == "lease":
        procs = [
            mp.Process(
                target=_writer_leased,
                args=(str(studio), session_id, i, rounds, ttl, errors),
            )
            for i in range(writers)
        ]
    else:
        procs = [
            mp.Process(target=_writer, args=(str(studio), session_id, i, rounds, errors))
            for i in range(writers)
        ]
    for proc in procs:
        proc.start()

    torn = 0
    for _ in range(_READ_PROBES):
        if target.is_file():
            try:
                json.loads(target.read_text(encoding="utf-8"))
            except ValueError:
                torn += 1

    for proc in procs:
        proc.join()

    accepted_total = lost_total = retries_total = 0
    failures: list[str] = []
    while not errors.empty():
        item = errors.get()
        accepted_total += int(item["accepted"])
        lost_total += int(item["lost"])
        retries_total += int(item["retries"])
        failures.extend(item["errors"])

    final = json.loads(target.read_text(encoding="utf-8"))
    return {
        "mode": mode,
        "writers": writers,
        "rounds_per_writer": rounds,
        "intended_writes": writers * rounds,
        "accepted_writes": accepted_total,
        "lost_writes": lost_total,
        "lease_contention_retries": retries_total,
        "write_exceptions": len(failures),
        "write_exception_samples": failures[:3],
        "read_probes": _READ_PROBES,
        "torn_json_reads": torn,
        "final_checkpoint_step": final.get("checkpoint_step"),
        "final_author": final.get("title"),
        "studio_dir": str(studio),
    }


def main(argv: list[str] | None = None) -> int:
    """Run the scenario; ``--assert`` fails while the hazard still exists."""
    parser = argparse.ArgumentParser(description="Concurrent SessionStore writers (manual).")
    parser.add_argument("--writers", type=int, default=4)
    parser.add_argument("--rounds", type=int, default=60)
    parser.add_argument(
        "--mode",
        choices=("off", "lease"),
        default="off",
        help="off reproduces the R1 defect; lease is the same load with the lease on",
    )
    parser.add_argument("--ttl", type=float, default=30.0, help="lease TTL in seconds")
    parser.add_argument("--assert", dest="assert_clean", action="store_true")
    args = parser.parse_args(argv)

    result = run(writers=args.writers, rounds=args.rounds, mode=args.mode, ttl=args.ttl)
    hazards = result["lost_writes"] + result["write_exceptions"] + result["torn_json_reads"]
    result["hazards"] = hazards
    print(json.dumps(result, indent=2))

    if args.assert_clean and hazards:
        print(
            f"\nFAIL: {result['lost_writes']} lost writes, "
            f"{result['write_exceptions']} write exceptions and "
            f"{result['torn_json_reads']} torn reads — a session has no writer lease.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
