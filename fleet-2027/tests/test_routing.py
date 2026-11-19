#!/usr/bin/env python3
"""A2 — routing fleet steps through a Free-First router.

These tests use a *fake* router that mimics the real one's contract
(``await router.route(request)`` returning an object with ``text``,
``provider``, ``attempts``; raising ``AllProvidersFailedError`` on total
failure). The real ``FreeFirstRouter`` is exercised separately by
``scripts/fleet_router_bridge_check.py`` and the ``router-failover`` gate, so
this package keeps its zero-dependency guarantee.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from fleet2027 import (  # noqa: E402
    DurableTaskStore,
    EvidenceKind,
    FleetRunner,
    PlanStep,
    RunnerConfig,
    TaskSpec,
    TaskStatus,
)
from fleet2027.llm import LLMAgent, LLMRequest, build_llm_registry  # noqa: E402
from fleet2027.routing import (  # noqa: E402
    DeterministicRouterBackend,
    RouterBackend,
    RoutingError,
    RoutingExhausted,
    read_attempts,
    require_configured,
)


# --------------------------------------------------------------------------- #
# Fakes that obey the real router's contract
# --------------------------------------------------------------------------- #
def _attempt(provider: str, *, ok: bool, error: str | None = None, model: str | None = None):
    return SimpleNamespace(
        provider=SimpleNamespace(value=provider),
        model=model or f"{provider}-model",
        attempt=1,
        ok=ok,
        latency_ms=12.5,
        error_code=error,
        detail=None if ok else f"{provider} failed",
        billing_tier=SimpleNamespace(value="local"),
    )


class _Router:
    """Minimal router stand-in: recorded calls, scripted answers/failures."""

    def __init__(self, *, answer: str = "```python\nx = 1\n```", fail_all: bool = False,
                 attempts: list[Any] | None = None, provider: str = "ollama",
                 simulated: bool = False) -> None:
        self.answer = answer
        self.fail_all = fail_all
        self.attempts = attempts or [
            _attempt(provider, ok=True),
        ]
        self.provider = provider
        self.simulated = simulated
        self.requests: list[Any] = []
        # Configured-but-failing is the interesting case: a real router still has
        # credentials when the provider starts timing out.
        self.providers: list[Any] = [SimpleNamespace(is_configured=lambda: True)]

    async def route(self, request: Any) -> Any:
        self.requests.append(request)
        if self.fail_all:
            error = type("AllProvidersFailedError", (RuntimeError,), {})("all failed")
            error.attempts = tuple(self.attempts)
            raise error
        return SimpleNamespace(
            text=self.answer,
            provider=SimpleNamespace(value=self.provider),
            model=f"{self.provider}-model",
            simulated=self.simulated,
            latency_ms=42.0,
            cost_usd=0.0,
            trace_id="trace-abcdef123",
            attempts=tuple(self.attempts),
        )


def _factory(request: LLMRequest) -> dict:
    return {"prompt": request.prompt, "max_tokens": request.max_tokens}


class AttemptParsingTests(unittest.TestCase):
    def test_reads_enums_dataclasses_and_mappings_without_importing_them(self) -> None:
        views = read_attempts([
            _attempt("ollama", ok=False, error="timeout"),
            _attempt("gemini", ok=True),
        ])
        self.assertEqual([view.provider for view in views], ["ollama", "gemini"])
        self.assertFalse(views[0].ok)
        self.assertEqual(views[0].error_code, "timeout")
        self.assertEqual(views[0].billing_tier, "local")

    def test_unknown_shapes_degrade_to_unknown_instead_of_crashing(self) -> None:
        views = read_attempts([SimpleNamespace()])
        self.assertEqual(views[0].provider, "unknown")
        self.assertEqual(views[0].model, "unknown")

    def test_phrasing_names_the_known_reasons(self) -> None:
        view = read_attempts([_attempt("ollama", ok=False, error="timeout")])[0]
        self.assertIn("never answered", view.phrase())
        blocked = read_attempts([_attempt("groq", ok=False, error="budget_blocked")])[0]
        self.assertIn("budget guard", blocked.phrase())


class RouterBackendTests(unittest.TestCase):
    def test_a_clean_route_reports_no_failover(self) -> None:
        backend = RouterBackend(router=_Router(), request_factory=_factory)
        response = backend.complete(LLMRequest(prompt="write add"))
        self.assertEqual(response.backend, "free-first-router:ollama")
        self.assertEqual(backend.calls, 1)
        evidence = backend.routing_evidence()
        self.assertTrue(evidence["routed"])
        self.assertEqual(evidence["failovers"], 0)
        self.assertFalse(evidence["used_fallback"])

    def test_a_failover_is_counted_and_attributed(self) -> None:
        router = _Router(
            provider="gemini",
            attempts=[
                _attempt("ollama", ok=False, error="timeout"),
                _attempt("gemini", ok=True),
            ],
        )
        backend = RouterBackend(router=router, request_factory=_factory)
        backend.complete(LLMRequest(prompt="write add"))
        evidence = backend.routing_evidence()
        self.assertEqual(evidence["failovers"], 1)
        self.assertTrue(evidence["used_fallback"])
        self.assertEqual(evidence["provider"], "gemini")
        self.assertEqual(evidence["attempts"][0]["error_code"], "timeout")

    def test_exhaustion_becomes_a_retryable_step_failure_with_a_trail(self) -> None:
        router = _Router(
            fail_all=True,
            attempts=[
                _attempt("ollama", ok=False, error="upstream_error"),
                _attempt("groq", ok=False, error="rate_limited"),
            ],
        )
        backend = RouterBackend(router=router, request_factory=_factory)
        with self.assertRaises(RoutingExhausted) as caught:
            backend.complete(LLMRequest(prompt="write add"))
        message = str(caught.exception)
        self.assertIn("every provider failed", message)
        self.assertIn("ollama", message)
        self.assertIn("groq", message)
        # Retryable business failure, not an agent crash.
        from fleet2027.runner import StepFailure

        self.assertIsInstance(caught.exception, StepFailure)
        self.assertEqual(len(caught.exception.trail), 2)

    def test_an_empty_completion_is_an_error_not_a_pass(self) -> None:
        backend = RouterBackend(router=_Router(answer="   "), request_factory=_factory)
        with self.assertRaises(RoutingError):
            backend.complete(LLMRequest(prompt="write add"))

    def test_is_configured_follows_the_router(self) -> None:
        self.assertTrue(RouterBackend(router=_Router(), request_factory=_factory).is_configured())
        unconfigured = _Router()
        unconfigured.providers = [SimpleNamespace(is_configured=lambda: False)]
        self.assertFalse(
            RouterBackend(router=unconfigured, request_factory=_factory).is_configured()
        )

    def test_a_broken_provider_is_treated_as_unconfigured(self) -> None:
        router = _Router()
        router.providers = [SimpleNamespace(is_configured=lambda: (_ for _ in ()).throw(ValueError))]
        self.assertFalse(RouterBackend(router=router, request_factory=_factory).is_configured())

    def test_unrouted_evidence_is_explicit(self) -> None:
        backend = RouterBackend(router=_Router(), request_factory=_factory)
        self.assertEqual(backend.routing_evidence(), {"routed": False})


class DeterministicBackendTests(unittest.TestCase):
    def test_the_label_cannot_be_mistaken_for_a_model(self) -> None:
        backend = DeterministicRouterBackend()
        response = backend.complete(LLMRequest(prompt="write add(a, b)"))
        self.assertEqual(backend.name, "deterministic-simulated")
        self.assertIn("no model was called", response.text)
        self.assertIn("deterministic", response.backend)

    def test_require_configured_refuses_a_dead_backend(self) -> None:
        from fleet2027.contracts import ContractError

        unconfigured = _Router()
        unconfigured.providers = [SimpleNamespace(is_configured=lambda: False)]
        dead = RouterBackend(router=unconfigured, request_factory=_factory)
        with self.assertRaises(ContractError):
            require_configured(dead)
        require_configured(DeterministicRouterBackend())


class RoutedAgentTests(unittest.TestCase):
    """The agent must carry the trail into evidence, and the runner must persist it."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "ws"
        self.workspace.mkdir()
        self.store = DurableTaskStore(str(self.root / "fleet.db"))
        self.addCleanup(self.store.close)

    def _run(self, backend, *, task_id: str = "t-route") -> tuple[Any, dict]:
        agent = LLMAgent(backend=backend, agent_name="routed")
        registry, _ = build_llm_registry(agent)
        spec = TaskSpec(
            task_id=task_id,
            goal="route it",
            steps=(
                PlanStep(
                    step_id="write",
                    agent="routed",
                    action="generate",
                    inputs={"prompt": "write add", "out": "app.py", "language": "python"},
                    verification=(EvidenceKind.ARTIFACT_SHA256.value,
                                  EvidenceKind.OUTPUT_NON_EMPTY.value),
                    max_attempts=1,
                ),
            ),
        )
        runner = FleetRunner(
            self.store, registry, workspace_root=self.workspace,
            config=RunnerConfig(backoff_base_s=0.01),
        )
        report = runner.submit_and_run(spec)
        return report, self.store.step_result(task_id, "write") or {}

    def test_failover_reaches_evidence_and_the_persisted_event_log(self) -> None:
        router = _Router(
            provider="gemini",
            attempts=[
                _attempt("ollama", ok=False, error="timeout"),
                _attempt("gemini", ok=True),
            ],
        )
        report, step_result = self._run(
            RouterBackend(router=router, request_factory=_factory)
        )
        self.assertEqual(report.status, TaskStatus.SUCCEEDED)
        routing = step_result["evidence"]["routing"]
        self.assertEqual(routing["failovers"], 1)
        self.assertEqual(routing["provider"], "gemini")

        kinds = [event["kind"] for event in self.store.events("t-route")]
        self.assertIn("step.failover", kinds)
        failover = next(
            event for event in self.store.events("t-route") if event["kind"] == "step.failover"
        )
        self.assertIn("ollama:timeout", failover["message"])
        self.assertIn("gemini", failover["message"])

        # And it is in the hash-chained audit log, not only in the task events.
        audit_kinds = {
            row["kind"]
            for row in self.store._conn.execute(
                "SELECT kind FROM audit_log WHERE task_id = 't-route'"
            )
        }
        self.assertIn("step.failover", audit_kinds)

    def test_a_simulated_answer_is_labelled_in_evidence_and_events(self) -> None:
        router = _Router(provider="deterministic", simulated=True, attempts=[
            _attempt("deterministic", ok=True),
        ])
        report, step_result = self._run(
            RouterBackend(router=router, request_factory=_factory), task_id="t-sim"
        )
        self.assertEqual(report.status, TaskStatus.SUCCEEDED)
        self.assertTrue(step_result["evidence"]["routing"]["simulated"])
        kinds = [event["kind"] for event in self.store.events("t-sim")]
        self.assertIn("step.simulated", kinds)

    def test_exhausted_router_dead_letters_with_the_trail_in_the_reason(self) -> None:
        router = _Router(
            fail_all=True,
            attempts=[
                _attempt("ollama", ok=False, error="upstream_error"),
                _attempt("groq", ok=False, error="rate_limited"),
            ],
        )
        report, step_result = self._run(
            RouterBackend(router=router, request_factory=_factory), task_id="t-dead"
        )
        self.assertEqual(report.status, TaskStatus.DEAD_LETTER)
        self.assertFalse(
            (self.workspace / "app.py").exists() and step_result.get("ok") is True
        )
        dead = self.store.list_dead_letters()
        self.assertEqual(len(dead), 1)
        self.assertIn("every provider failed", dead[0]["reason"])
        self.assertIn("ollama", dead[0]["reason"])

    def test_a_non_routed_backend_says_so_rather_than_pretending(self) -> None:
        from fleet2027.llm import ScriptedBackend

        agent = LLMAgent(
            backend=ScriptedBackend(script=["```python\nx = 1\n```"]), agent_name="plain"
        )
        self.assertEqual(agent.routing_evidence(), {"routed": False})


if __name__ == "__main__":
    unittest.main(verbosity=2)
