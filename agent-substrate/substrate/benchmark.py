"""Measure suspend-to-wake invocation latency for the counter example."""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from .cli import _request
from .controller import validate_agent_name


_REQUEST_PREFIX = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")


def percentile(values: list[float], percent: float) -> float:
    """Nearest-rank percentile, including sensible results for small samples."""
    if not values:
        raise ValueError("At least one measurement is required")
    if not math.isfinite(percent) or not 0 < percent <= 100:
        raise ValueError("Percentile must be greater than 0 and at most 100")
    ordered = sorted(values)
    index = max(0, math.ceil(percent / 100 * len(ordered)) - 1)
    return ordered[index]


def summarize(samples: list[dict[str, Any]]) -> dict[str, float | int]:
    values = [float(sample["latencyMs"]) for sample in samples]
    if not values:
        raise ValueError("At least one measurement is required")
    return {
        "count": len(values),
        "minMs": min(values),
        "meanMs": statistics.fmean(values),
        "p50Ms": percentile(values, 50),
        "p95Ms": percentile(values, 95),
        "p99Ms": percentile(values, 99),
        "maxMs": max(values),
    }


def _wait_until_sleeping(
    get_status: Callable[[], dict[str, Any]],
    *,
    timeout: float,
    poll_interval: float,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    deadline = monotonic() + timeout
    while True:
        status = get_status()
        phase = status.get("phase")
        if status.get("desiredState") == "Sleeping" and phase == "Sleeping":
            return
        if phase in {"Failed", "CheckpointFailed"}:
            reason = status.get("reason") or "no reason reported"
            raise RuntimeError(f"Agent suspend failed ({phase}): {reason}")
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("Agent did not reach Sleeping before the timeout")
        sleep(min(poll_interval, remaining))


def measure_wake_cycles(
    *,
    cycles: int,
    request_prefix: str,
    sleep_timeout: float,
    request_timeout: float,
    poll_interval: float,
    suspend: Callable[[], Any],
    get_status: Callable[[], dict[str, Any]],
    invoke: Callable[[dict[str, Any], float], dict[str, Any]],
    monotonic: Callable[[], float] = time.perf_counter,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Suspend between samples, then measure gateway invoke through response."""
    if not isinstance(cycles, int) or cycles < 1 or cycles > 10000:
        raise ValueError("cycles must be an integer from 1 through 10000")
    if not _REQUEST_PREFIX.fullmatch(request_prefix):
        raise ValueError("request prefix must use 1-80 letters, digits, dot, underscore, colon, or hyphen")
    if any(not math.isfinite(value) or value <= 0 for value in (sleep_timeout, request_timeout, poll_interval)):
        raise ValueError("timeouts and poll interval must be positive finite numbers")

    def suspend_and_wait() -> None:
        suspend()
        _wait_until_sleeping(
            get_status,
            timeout=sleep_timeout,
            poll_interval=poll_interval,
            monotonic=monotonic,
            sleep=sleep,
        )

    samples: list[dict[str, Any]] = []
    try:
        for cycle in range(1, cycles + 1):
            suspend_and_wait()
            request_id = f"{request_prefix}-{cycle}-{uuid.uuid4().hex[:12]}"
            payload = {"op": "increment", "by": 0, "requestId": request_id}
            started = monotonic()
            invoke(payload, request_timeout)
            latency_ms = max(0.0, (monotonic() - started) * 1000.0)
            samples.append({"cycle": cycle, "latencyMs": latency_ms})
    except BaseException:
        # An invocation error can occur after the gateway has already woken the
        # actor. Try to restore the idle state without hiding the original error.
        try:
            suspend_and_wait()
        except BaseException:
            pass
        raise

    # Do not leave a billable/always-on actor running after the benchmark.
    suspend_and_wait()
    return samples


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-substrate-wake-benchmark",
        description="Measure end-to-end invoke latency after suspending the counter Agent.",
    )
    parser.add_argument("agent", help="name of the deployed counter Agent")
    parser.add_argument("--cycles", type=int, default=20, help="suspend/wake samples (1-10000)")
    parser.add_argument("--request-prefix", default="wake-bench", help="prefix for unique requestIds")
    parser.add_argument("--sleep-timeout", type=float, default=120.0, help="seconds to wait for checkpoint and suspension")
    parser.add_argument("--request-timeout", type=float, default=180.0, help="seconds allowed per invoke request")
    parser.add_argument("--poll-interval", type=float, default=0.5, help="seconds between sleep-status polls")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    try:
        name = validate_agent_name(args.agent)
        base = f"/v1/agents/{name}"
        samples = measure_wake_cycles(
            cycles=args.cycles,
            request_prefix=args.request_prefix,
            sleep_timeout=args.sleep_timeout,
            request_timeout=args.request_timeout,
            poll_interval=args.poll_interval,
            suspend=lambda: _request(f"{base}/suspend", method="POST", payload={}),
            get_status=lambda: _request(base, method="GET", timeout=min(10.0, args.request_timeout)),
            invoke=lambda payload, timeout: _request(
                f"{base}/invoke", method="POST", payload=payload, timeout=timeout,
            ),
        )
    except (ValueError, RuntimeError, TimeoutError) as exc:
        print(f"agent-substrate-wake-benchmark: {exc}", file=sys.stderr)
        return 2

    report = {
        "agent": name,
        "measuredAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "definition": "POST /invoke start to JSON response, after status reports desiredState=Sleeping and phase=Sleeping",
        "summary": summarize(samples),
        "samples": samples,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        summary = report["summary"]
        print(f"Agent: {name} · end-to-end invoke after suspend · {summary['count']} samples")
        print(
            "Latency ms: "
            f"min={summary['minMs']:.1f} "
            f"p50={summary['p50Ms']:.1f} "
            f"p95={summary['p95Ms']:.1f} "
            f"p99={summary['p99Ms']:.1f} "
            f"max={summary['maxMs']:.1f}"
        )
        print("Includes gateway, controller polling, scheduling, storage/image startup, and actor response.")
        print("This is not a claim of sub-500ms wake or million-Agent capacity.")
        for sample in samples:
            print(f"  {sample['cycle']:>4}: {sample['latencyMs']:.1f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
