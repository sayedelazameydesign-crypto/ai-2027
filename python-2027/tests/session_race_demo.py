#!/usr/bin/env python3
"""Manual harness: what happens when two writers save the SAME session at once.

This is **not** a pytest test and it is **not** run in CI. It is the reproduction
behind ``docs/PAP-2027-RESILIENCE-AUDIT.md`` (gap R1): today the harness reports
write failures and torn reads; after the session-lease phase it must report zero.

    python-2027/.venv/bin/python python-2027/tests/session_race_demo.py --assert

Each writer process builds its own ``SessionStore`` (as the CLI and the Studio
worker do) and writes the same session id with a distinct payload. Nothing is
mocked: this is the real ``save_sync`` path, the real ``session.tmp`` filename and
the real ``Path.replace`` call.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import tempfile
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # run as a script: make ``src`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai2027_protocol.agent import AgentSessionState, SessionStore  # noqa: E402

_PAD = "x" * 40_000  # large enough that a write is not a single small syscall
_READ_PROBES = 400


def _writer(studio: str, session_id: str, index: int, rounds: int, errors: Any) -> None:
    store = SessionStore(Path(studio))
    for round_no in range(rounds):
        state = AgentSessionState(
            session_id=session_id,
            title=f"writer-{index}",
            created_at="2026-11-19T00:00:00+00:00",
            updated_at="2026-11-19T00:00:00+00:00",
            final_answer=f"{_PAD} writer={index} round={round_no}",
            checkpoint_step=index * 1000 + round_no,
        )
        try:
            store.save_sync(state)
        except Exception as exc:  # noqa: BLE001 - the harness records, it does not judge
            errors.put(f"{type(exc).__name__}: {exc}")


def run(*, writers: int, rounds: int) -> dict[str, Any]:
    """Run the contention scenario and return the measured outcome."""
    studio = Path(tempfile.mkdtemp(prefix="pap-race-"))
    session_id = SessionStore(studio).create_session(title="race demo").session_id
    target = studio / "sessions" / session_id / "session.json"
    errors: Any = mp.Queue()

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

    failures: list[str] = []
    while not errors.empty():
        failures.append(errors.get())

    final = json.loads(target.read_text(encoding="utf-8"))
    return {
        "writers": writers,
        "rounds_per_writer": rounds,
        "intended_writes": writers * rounds,
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
    parser.add_argument("--assert", dest="assert_clean", action="store_true")
    args = parser.parse_args(argv)

    result = run(writers=args.writers, rounds=args.rounds)
    hazards = result["write_exceptions"] + result["torn_json_reads"]
    result["hazards"] = hazards
    print(json.dumps(result, indent=2))

    if args.assert_clean and hazards:
        print(
            f"\nFAIL: {result['write_exceptions']} lost writes and "
            f"{result['torn_json_reads']} torn reads — a session has no writer lease.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
