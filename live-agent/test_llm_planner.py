#!/usr/bin/env python3
"""Mock-provider integration tests for the Live Agent planning boundary."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import server  # noqa: E402
from llm_planner import LLMPlanner  # noqa: E402

AGI_DIR = ROOT / "projects" / "agi-system"
sys.path.insert(0, str(AGI_DIR))
from core.clock import FrozenClock  # noqa: E402
from core.config import Limits, Settings  # noqa: E402
from core.errors import PlanningError  # noqa: E402
from core.event_ledger import EventLedger  # noqa: E402
from core.kernel import Kernel  # noqa: E402
from core.models import Goal, Plan  # noqa: E402


def response(*actions: dict[str, Any]) -> str:
    """Encode one provider plan using the strict, expected JSON shape."""
    return json.dumps(
        {
            "plans": [
                {
                    "rationale": "mocked structured proposal",
                    "actions": list(actions),
                }
            ]
        }
    )


class MockProvider:
    """In-memory provider double; never opens a socket or reads credentials."""

    def __init__(
        self,
        output: str,
        *,
        error: Exception | None = None,
        on_call: Callable[[], None] | None = None,
    ) -> None:
        self.output = output
        self.error = error
        self.on_call = on_call
        self.calls: list[tuple[dict[str, Any], float]] = []

    def propose(self, request: dict[str, Any], *, timeout_seconds: float) -> str:
        self.calls.append((request, timeout_seconds))
        if self.on_call is not None:
            self.on_call()
        if self.error is not None:
            raise self.error
        return self.output


class MockBudgetGate:
    """External preflight guard controlled by the test, not by provider output."""

    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.calls = 0

    def allows(self, request: dict[str, Any]) -> bool:
        self.calls += 1
        return self.allowed


class MockApprovalGate:
    """Explicit, out-of-model approval result used only by this test suite."""

    def __init__(self, approved: bool) -> None:
        self.approved = approved
        self.calls = 0

    def approve(self, goal: Goal, plan: Plan) -> bool:
        self.calls += 1
        return self.approved


class LlmPlannerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="live-agent-llm-test-"))
        self.old_runtime = (server.RUNS_DIR, server.SANDBOXES_DIR, server.MEMORY_DB)
        server._configure_runtime(self.tmp / "live-runtime")
        server._prepare_state(fresh=True)

    def tearDown(self) -> None:
        server._memory = None
        server.RUNS_DIR, server.SANDBOXES_DIR, server.MEMORY_DB = self.old_runtime
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _read_action(path: str = ".") -> dict[str, Any]:
        return {
            "capability": "filesystem.list",
            "params": {"path": path},
            "rationale": "inspect a directory without changing it",
        }

    def _kernel(self, planner, *, max_actions: int = 32, clock=None) -> Kernel:
        settings = Settings(
            sandbox_dir=self.tmp / "kernel-sandbox",
            ledger_path=self.tmp / "kernel-ledger" / "events.jsonl",
            limits=Limits(max_actions_per_plan=max_actions),
        ).validate()
        return Kernel(settings=settings, planner=planner, clock=clock)

    def test_constructor_rejects_invalid_action_timeout_and_capability_limits(self) -> None:
        provider = MockProvider(response(self._read_action()))
        budget = MockBudgetGate(allowed=True)
        invalid_options = [
            {"max_actions": True},
            {"max_actions": 0},
            {"timeout_seconds": float("nan")},
            {"timeout_seconds": 60.1},
            {"allowed_capabilities": 42},
            {"allowed_capabilities": ("filesystem.read", 7)},
        ]

        for options in invalid_options:
            with self.subTest(options=options), self.assertRaises(ValueError):
                LLMPlanner(provider, budget_gate=budget, **options)

    def test_configured_action_limit_is_enforced_before_kernel_planning(self) -> None:
        provider = MockProvider(response(
            self._read_action(),
            {
                "capability": "filesystem.read",
                "params": {"path": "notes.txt"},
            },
        ))
        planner = LLMPlanner(
            provider, budget_gate=MockBudgetGate(allowed=True), max_actions=1
        )

        with self.assertRaisesRegex(PlanningError, "invalid plan response"):
            planner.propose(Goal("list_dir", {"path": "."}))

        self.assertEqual(len(provider.calls), 1)

    def test_configured_capability_allowlist_is_enforced(self) -> None:
        provider = MockProvider(response(self._read_action()))
        planner = LLMPlanner(
            provider,
            budget_gate=MockBudgetGate(allowed=True),
            allowed_capabilities=("filesystem.read",),
        )

        with self.assertRaisesRegex(PlanningError, "invalid plan response"):
            planner.propose(Goal("list_dir", {"path": "."}))

        self.assertEqual(len(provider.calls), 1)

    def test_mock_plan_flows_through_policy_executor_verifier_and_external_ledger(self) -> None:
        provider = MockProvider(response(self._read_action()))
        budget = MockBudgetGate(allowed=True)
        planner = LLMPlanner(
            provider,
            budget_gate=budget,
            context_provider=lambda _goal: {"history": ["user asked to inspect the sandbox"]},
        )

        result = server.run_goal("list_dir", {"path": "."}, planner=planner)

        self.assertEqual(result["status"], "VERIFIED")
        self.assertTrue(result["evidence"])
        self.assertTrue(all(all(e["checks"].values()) for e in result["evidence"]))
        self.assertEqual(budget.calls, 1)
        self.assertEqual(len(provider.calls), 1)
        request, timeout_seconds = provider.calls[0]
        self.assertEqual(request["goal"]["goal_type"], "list_dir")
        self.assertEqual(request["context"]["history"], ["user asked to inspect the sandbox"])
        self.assertEqual(request["constraints"]["max_actions_per_plan"], 32)
        self.assertGreater(timeout_seconds, 0)
        self.assertNotIn("executor", request)

        sandbox = server.SANDBOXES_DIR / result["sandbox"]
        ledger_path = server.RUNS_DIR / "ledgers" / f"{result['run_id']}.jsonl"
        self.assertTrue(ledger_path.is_file())
        self.assertFalse((sandbox / "ledger.jsonl").exists())
        self.assertNotIn(sandbox.resolve(), ledger_path.resolve().parents)
        self.assertTrue(EventLedger(ledger_path).verify_chain())

    def test_policy_denies_mocked_path_escape_without_executing_it(self) -> None:
        provider = MockProvider(response(self._read_action("../escape.txt")))
        planner = LLMPlanner(provider, budget_gate=MockBudgetGate(allowed=True))

        result = server.run_goal("list_dir", {"path": "."}, planner=planner)

        self.assertEqual(result["status"], "DENIED")
        self.assertTrue(any("escapes the sandbox" in reason for reason in result["denial_reasons"]))
        self.assertFalse(result["observations"])
        sandbox = server.SANDBOXES_DIR / result["sandbox"]
        self.assertFalse((sandbox.parent / "escape.txt").exists())
        ledger = EventLedger(server.RUNS_DIR / "ledgers" / f"{result['run_id']}.jsonl")
        event_types = [event["type"] for event in ledger.read_all()]
        self.assertIn("POLICY_DENIED", event_types)
        self.assertNotIn("EXECUTION_START", event_types)

    def test_kernel_stops_a_plan_that_exceeds_the_action_limit(self) -> None:
        provider = MockProvider(
            response(self._read_action(), {
                "capability": "filesystem.read",
                "params": {"path": "notes.txt"},
                "rationale": "read a second file",
            })
        )
        planner = LLMPlanner(provider, budget_gate=MockBudgetGate(allowed=True), max_actions=2)
        kernel = self._kernel(planner, max_actions=1)

        record = kernel.run(Goal("list_dir", {"path": "."}))

        self.assertEqual(record.status.value, "FAILED")
        self.assertEqual(record.error_code, "PLAN_TOO_LARGE")
        self.assertIsNone(record.plan)
        event_types = [event["type"] for event in kernel.ledger.read_all()]
        self.assertNotIn("EXECUTION_START", event_types)
        self.assertNotIn("ACTION_EXECUTING", event_types)

    def test_budget_denial_fails_closed_without_calling_provider_or_executing(self) -> None:
        provider = MockProvider(response(self._read_action()))
        budget = MockBudgetGate(allowed=False)
        planner = LLMPlanner(provider, budget_gate=budget)

        result = server.run_goal("list_dir", {"path": "."}, planner=planner)

        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["error_code"], "PLANNING_FAILED")
        self.assertIsNone(result["plan"])
        self.assertEqual(result["observations"], [])
        self.assertEqual(provider.calls, [])
        self.assertIn("budget", result["error"].lower())
        self.assertIn("no plan", result["error"].lower())
        ledger = EventLedger(server.RUNS_DIR / "ledgers" / f"{result['run_id']}.jsonl")
        event_types = [event["type"] for event in ledger.read_all()]
        self.assertIn("PLANNING_FAILED", event_types)
        self.assertNotIn("PLANS_PROPOSED", event_types)
        self.assertNotIn("EXECUTION_START", event_types)

    def test_provider_failure_fails_closed_without_a_fallback_plan(self) -> None:
        provider = MockProvider("", error=TimeoutError("mock timeout"))
        planner = LLMPlanner(provider, budget_gate=MockBudgetGate(allowed=True))

        result = server.run_goal("list_dir", {"path": "."}, planner=planner)

        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["error_code"], "PLANNING_FAILED")
        self.assertIsNone(result["plan"])
        self.assertEqual(result["observations"], [])
        self.assertEqual(len(provider.calls), 1)
        self.assertIn("provider request failed", result["error"].lower())
        self.assertNotIn("mock timeout", result["error"].lower())
        self.assertNotIn("EXECUTION_START", [
            event["type"]
            for event in EventLedger(
                server.RUNS_DIR / "ledgers" / f"{result['run_id']}.jsonl"
            ).read_all()
        ])

    def test_invalid_provider_response_fails_closed_without_a_fallback_plan(self) -> None:
        provider = MockProvider('{"plans": [{"actions": []}]}')
        planner = LLMPlanner(provider, budget_gate=MockBudgetGate(allowed=True))

        result = server.run_goal("list_dir", {"path": "."}, planner=planner)

        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["error_code"], "PLANNING_FAILED")
        self.assertIsNone(result["plan"])
        self.assertEqual(result["observations"], [])
        self.assertEqual(len(provider.calls), 1)
        self.assertIn("invalid plan response", result["error"].lower())
        self.assertNotIn("EXECUTION_START", [
            event["type"]
            for event in EventLedger(
                server.RUNS_DIR / "ledgers" / f"{result['run_id']}.jsonl"
            ).read_all()
        ])

    def test_mutating_plan_needs_an_external_approval_gate(self) -> None:
        provider = MockProvider(response({
            "capability": "filesystem.write",
            "params": {"path": "approved.txt", "content": "safe test"},
            "rationale": "write only inside the temporary sandbox",
        }))
        approval = MockApprovalGate(approved=False)
        planner = LLMPlanner(
            provider,
            budget_gate=MockBudgetGate(allowed=True),
            approval_gate=approval,
        )

        result = server.run_goal(
            "create_file", {"path": "approved.txt", "content": "safe test"}, planner=planner
        )

        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(approval.calls, 1)
        sandbox = server.SANDBOXES_DIR / result["sandbox"]
        self.assertFalse((sandbox / "approved.txt").exists())
        self.assertNotIn("EXECUTION_START", [
            event["type"]
            for event in EventLedger(
                server.RUNS_DIR / "ledgers" / f"{result['run_id']}.jsonl"
            ).read_all()
        ])

    def test_mutating_plan_runs_only_after_external_gate_approves(self) -> None:
        provider = MockProvider(response({
            "capability": "filesystem.write",
            "params": {"path": "approved.txt", "content": "approved in test"},
            "rationale": "write a test artifact in the isolated sandbox",
        }))
        approval = MockApprovalGate(approved=True)
        planner = LLMPlanner(
            provider,
            budget_gate=MockBudgetGate(allowed=True),
            approval_gate=approval,
        )

        result = server.run_goal(
            "create_file",
            {"path": "approved.txt", "content": "approved in test"},
            planner=planner,
        )

        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(approval.calls, 1)
        sandbox = server.SANDBOXES_DIR / result["sandbox"]
        self.assertEqual((sandbox / "approved.txt").read_text(encoding="utf-8"), "approved in test")
        self.assertTrue(all(all(e["checks"].values()) for e in result["evidence"]))

    def test_planning_timeout_is_checked_before_policy_or_execution(self) -> None:
        clock = FrozenClock()
        provider = MockProvider(response(self._read_action()), on_call=lambda: clock.advance(2))
        planner = LLMPlanner(
            provider,
            budget_gate=MockBudgetGate(allowed=True),
            timeout_seconds=0.25,
        )
        kernel = self._kernel(planner, clock=clock)

        record = kernel.run(Goal("list_dir", {"path": "."}), timeout=1)

        self.assertEqual(record.status.value, "FAILED")
        self.assertEqual(record.error_code, "RUN_TIMEOUT")
        self.assertIsNone(record.plan)
        event_types = [event["type"] for event in kernel.ledger.read_all()]
        self.assertNotIn("PLANS_PROPOSED", event_types)
        self.assertNotIn("PLAN_SELECTED", event_types)
        self.assertNotIn("EXECUTION_START", event_types)


if __name__ == "__main__":
    unittest.main(verbosity=2)
