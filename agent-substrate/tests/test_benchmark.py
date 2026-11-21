from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from substrate.benchmark import measure_wake_cycles, percentile, summarize


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, duration: float) -> None:
        self.now += duration


class WakeBenchmarkTests(unittest.TestCase):
    def test_percentile_uses_nearest_rank(self):
        values = [9.0, 1.0, 4.0, 2.0]
        self.assertEqual(percentile(values, 50), 2.0)
        self.assertEqual(percentile(values, 95), 9.0)
        self.assertEqual(percentile([7.0], 99), 7.0)

    def test_percentile_rejects_invalid_inputs(self):
        with self.assertRaises(ValueError):
            percentile([], 95)
        with self.assertRaises(ValueError):
            percentile([1.0], 0)
        with self.assertRaises(ValueError):
            percentile([1.0], float("nan"))

    def test_summary_reports_count_and_tail_latencies(self):
        report = summarize([
            {"latencyMs": 10.0},
            {"latencyMs": 20.0},
            {"latencyMs": 30.0},
            {"latencyMs": 40.0},
        ])
        self.assertEqual(report["count"], 4)
        self.assertEqual(report["p50Ms"], 20.0)
        self.assertEqual(report["p95Ms"], 40.0)
        self.assertEqual(report["maxMs"], 40.0)

    def test_measurement_suspends_between_cycles_and_times_only_invoke(self):
        clock = FakeClock()
        suspends = 0
        status_reads = 0
        payloads: list[dict[str, Any]] = []

        def suspend():
            nonlocal suspends
            suspends += 1

        def get_status():
            nonlocal status_reads
            status_reads += 1
            if status_reads % 2:
                return {"desiredState": "Running", "phase": "Running"}
            return {"desiredState": "Sleeping", "phase": "Sleeping"}

        def invoke(payload, _timeout):
            payloads.append(payload)
            clock.now += 0.125
            return {"value": 0}

        samples = measure_wake_cycles(
            cycles=2,
            request_prefix="test-wake",
            sleep_timeout=2,
            request_timeout=5,
            poll_interval=0.1,
            suspend=suspend,
            get_status=get_status,
            invoke=invoke,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        self.assertEqual(suspends, 3)  # before each cycle and once more for cleanup
        self.assertEqual([sample["latencyMs"] for sample in samples], [125.0, 125.0])
        self.assertEqual([payload["by"] for payload in payloads], [0, 0])
        self.assertEqual(len({payload["requestId"] for payload in payloads}), 2)
        self.assertTrue(all(set(sample) == {"cycle", "latencyMs"} for sample in samples))

    def test_checkpoint_failure_stops_before_invocation(self):
        invoked = False

        def invoke(_payload, _timeout):
            nonlocal invoked
            invoked = True
            return {}

        with self.assertRaisesRegex(RuntimeError, "CheckpointFailed"):
            measure_wake_cycles(
                cycles=1,
                request_prefix="test",
                sleep_timeout=1,
                request_timeout=1,
                poll_interval=0.1,
                suspend=lambda: None,
                get_status=lambda: {
                    "desiredState": "Sleeping",
                    "phase": "CheckpointFailed",
                    "reason": "checkpoint unavailable",
                },
                invoke=invoke,
                monotonic=lambda: 0.0,
                sleep=lambda _duration: None,
            )
        self.assertFalse(invoked)

    def test_invoke_failure_still_attempts_to_leave_agent_sleeping(self):
        suspends = 0

        def suspend():
            nonlocal suspends
            suspends += 1

        def fail_invoke(_payload, _timeout):
            raise RuntimeError("simulated invoke failure")

        with self.assertRaisesRegex(RuntimeError, "simulated invoke failure"):
            measure_wake_cycles(
                cycles=1,
                request_prefix="test",
                sleep_timeout=1,
                request_timeout=1,
                poll_interval=0.1,
                suspend=suspend,
                get_status=lambda: {"desiredState": "Sleeping", "phase": "Sleeping"},
                invoke=fail_invoke,
                monotonic=lambda: 0.0,
                sleep=lambda _duration: None,
            )
        self.assertEqual(suspends, 2)  # initial suspend and failure cleanup

    def test_sleep_wait_times_out(self):
        clock = FakeClock()
        with self.assertRaisesRegex(TimeoutError, "did not reach Sleeping"):
            measure_wake_cycles(
                cycles=1,
                request_prefix="test",
                sleep_timeout=0.3,
                request_timeout=1,
                poll_interval=0.1,
                suspend=lambda: None,
                get_status=lambda: {"desiredState": "Sleeping", "phase": "Starting"},
                invoke=lambda _payload, _timeout: {},
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )

    def test_invalid_benchmark_settings_fail_before_calls(self):
        suspends = 0

        def suspend():
            nonlocal suspends
            suspends += 1

        with self.assertRaises(ValueError):
            measure_wake_cycles(
                cycles=0,
                request_prefix="test",
                sleep_timeout=1,
                request_timeout=1,
                poll_interval=0.1,
                suspend=suspend,
                get_status=lambda: {},
                invoke=lambda _payload, _timeout: {},
            )
        with self.assertRaises(ValueError):
            measure_wake_cycles(
                cycles=1,
                request_prefix="bad prefix",
                sleep_timeout=1,
                request_timeout=1,
                poll_interval=0.1,
                suspend=suspend,
                get_status=lambda: {},
                invoke=lambda _payload, _timeout: {},
            )
        self.assertEqual(suspends, 0)


if __name__ == "__main__":
    unittest.main()
