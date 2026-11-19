"""Registry tests: capability routing, failover, circuit breaking, bulkheads."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from fleet2027 import (  # noqa: E402
    AgentRegistrationError,
    AgentRegistry,
    AgentSpec,
    Bulkhead,
    CircuitBreaker,
    ContractError,
    CostTier,
    StepResult,
    build_reviewer,
    cost_guard,
)
from fleet2027.contracts import PlanStep  # noqa: E402


def _handler(name: str, *, ok: bool = True):
    def run(ctx):
        return StepResult(ok=ok, summary=f"{name} ran", output=name, error=None if ok else "failed")

    return run


class RegistrationTests(unittest.TestCase):
    def test_duplicate_names_and_ambiguous_capabilities_are_refused(self) -> None:
        registry = AgentRegistry()
        spec = AgentSpec(name="a", role="r", capabilities=("code",))
        registry.register(spec, _handler("a"))
        with self.assertRaises(AgentRegistrationError):
            registry.register(AgentSpec(name="a", role="r2"), _handler("a2"))
        with self.assertRaises(AgentRegistrationError):
            registry.register(AgentSpec(name="b", role="r", capabilities=("code",)), _handler("b"))

    def test_resolution_by_name_and_by_capability(self) -> None:
        registry = AgentRegistry()
        registry.register(AgentSpec(name="coder", role="r", capabilities=("write-code",)), _handler("coder"))
        self.assertEqual(registry.handler_for("coder").spec.name, "coder")
        self.assertEqual(registry.handler_for("write-code").spec.name, "coder")
        with self.assertRaises(AgentRegistrationError):
            registry.handler_for("nonexistent")

    def test_decorator_registration(self) -> None:
        registry = AgentRegistry()

        @registry.agent(AgentSpec(name="deco", role="r"))
        def handler(ctx):
            return StepResult(ok=True, summary="ok", output="x")

        self.assertEqual(registry.names, ("deco",))
        self.assertTrue(callable(registry.handler_for("deco").handler))


class FailoverTests(unittest.TestCase):
    def test_a_fallback_is_used_when_the_primary_breaker_is_open(self) -> None:
        registry = AgentRegistry()
        primary = AgentSpec(name="primary", role="r", fallbacks=("backup",))
        registry.register(primary, _handler("primary"))
        registry.register(AgentSpec(name="backup", role="r"), _handler("backup"))
        self.assertEqual(registry.candidates("primary")[0].spec.name, "primary")
        registry.handler_for("primary").breaker.failures = 99
        registry.handler_for("primary").breaker.opened_at = 1.0
        chain = registry.candidates("primary", now=2.0)
        self.assertEqual([entry.spec.name for entry in chain], ["backup"])

    def test_no_candidate_remains_when_everything_is_open(self) -> None:
        registry = AgentRegistry()
        registry.register(AgentSpec(name="only", role="r"), _handler("only"))
        entry = registry.handler_for("only")
        entry.breaker.opened_at = 1.0
        self.assertEqual(registry.candidates("only", now=1.5), [])

    def test_breaker_closes_after_cooldown(self) -> None:
        breaker = CircuitBreaker(failure_threshold=1, cooldown_s=10)
        breaker.record_failure(now=100.0)
        self.assertEqual(breaker.state, "open")
        self.assertFalse(breaker.is_available(105.0))
        self.assertTrue(breaker.is_available(111.0))
        breaker.record_success()
        self.assertEqual(breaker.state, "closed")


class BulkheadTests(unittest.TestCase):
    def test_capacity_is_bounded_and_returned(self) -> None:
        bulkhead = Bulkhead(2)
        self.assertTrue(bulkhead.acquire(timeout=0))
        self.assertTrue(bulkhead.acquire(timeout=0))
        self.assertFalse(bulkhead.acquire(timeout=0.01))
        bulkhead.release()
        self.assertTrue(bulkhead.acquire(timeout=0.01))
        self.assertEqual(bulkhead.in_flight, 2)
        bulkhead.release()
        bulkhead.release()
        bulkhead.release()  # over-release must not go negative
        self.assertEqual(bulkhead.in_flight, 0)

    def test_zero_limit_is_refused(self) -> None:
        with self.assertRaises(ContractError):
            Bulkhead(0)


class CostGuardTests(unittest.TestCase):
    def test_a_fully_paid_fleet_is_refused(self) -> None:
        with self.assertRaises(ContractError):
            cost_guard(
                [AgentSpec(name="a", role="r", cost_tier=CostTier.PAID),
                 AgentSpec(name="b", role="r", cost_tier=CostTier.UNKNOWN)]
            )

    def test_one_local_agent_is_enough(self) -> None:
        result = cost_guard(
            [
                AgentSpec(name="a", role="r", cost_tier=CostTier.PAID),
                AgentSpec(name="b", role="r", cost_tier=CostTier.LOCAL),
            ]
        )
        self.assertTrue(result["free_capable"])

    def test_report_exposes_breaker_state(self) -> None:
        registry = AgentRegistry()
        registry.register(AgentSpec(name="a", role="r"), _handler("a"))
        report = registry.report()
        self.assertEqual(report["agents"][0]["breaker"], "closed")
        self.assertTrue(report["agents"][0]["available"])


class ReviewerTests(unittest.TestCase):
    def test_self_review_is_refused(self) -> None:
        registry = AgentRegistry()
        registry.register(AgentSpec(name="same", role="r"), _handler("same"))
        reviewer = build_reviewer(registry, reviewer_agent="same")
        ok, note = reviewer(
            PlanStep(step_id="s", agent="same", action="a"),
            StepResult(ok=True, output="x"),
        )
        self.assertFalse(ok)
        self.assertIn("not independent", note)

    def test_a_different_agent_reviews_normally(self) -> None:
        registry = AgentRegistry()
        registry.register(AgentSpec(name="worker", role="r"), _handler("worker"))
        registry.register(AgentSpec(name="judge", role="r"), _handler("judge"))
        reviewer = build_reviewer(registry, reviewer_agent="judge")
        ok, _ = reviewer(
            PlanStep(step_id="s", agent="worker", action="a"), StepResult(ok=True, output="x")
        )
        self.assertTrue(ok)

    def test_a_crashing_reviewer_fails_closed(self) -> None:
        registry = AgentRegistry()

        def explode(ctx):
            raise RuntimeError("reviewer is down")

        registry.register(AgentSpec(name="worker", role="r"), _handler("worker"))
        registry.register(AgentSpec(name="judge", role="r"), explode)
        reviewer = build_reviewer(registry, reviewer_agent="judge")
        ok, note = reviewer(
            PlanStep(step_id="s", agent="worker", action="a"), StepResult(ok=True, output="x")
        )
        self.assertFalse(ok)
        self.assertIn("crashed", note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
