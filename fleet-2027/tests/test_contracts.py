"""Contract tests: invalid plans and records must be refused, not coerced.

Run: python3 -m pytest fleet-2027/tests -q      (or)  python3 -m unittest discover fleet-2027/tests
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from fleet2027 import (  # noqa: E402
    AgentSpec,
    ArtifactRef,
    ContractError,
    CostTier,
    EvidenceKind,
    PlanError,
    PlanStep,
    StepResult,
    TaskSpec,
    canonical_json,
    sha256_text,
)


def _step(step_id: str, *, deps: tuple[str, ...] = (), **kwargs) -> PlanStep:
    kwargs.setdefault("action", "act")
    return PlanStep(step_id=step_id, agent=kwargs.pop("agent", "a"), depends_on=deps, **kwargs)


class TaskSpecTests(unittest.TestCase):
    def test_accepts_a_simple_dag_and_hashes_it_stably(self) -> None:
        spec = TaskSpec(
            task_id="t-1",
            goal="goal",
            steps=(
                _step("a"),
                _step("b", deps=("a",)),
                _step("c", deps=("a",)),
                _step("d", deps=("b", "c")),
            ),
        )
        self.assertEqual(spec.spec_hash(), spec.spec_hash())
        self.assertEqual(len(spec.spec_hash()), 64)

    def test_hash_changes_when_any_field_changes(self) -> None:
        base = TaskSpec(task_id="t-1", goal="goal", steps=(_step("a"),))
        changed_goal = TaskSpec(task_id="t-1", goal="other", steps=(_step("a"),))
        changed_step = TaskSpec(
            task_id="t-1", goal="goal", steps=(_step("a", action="different"),)
        )
        self.assertNotEqual(base.spec_hash(), changed_goal.spec_hash())
        self.assertNotEqual(base.spec_hash(), changed_step.spec_hash())

    def test_refuses_dependency_cycles(self) -> None:
        with self.assertRaises(PlanError):
            TaskSpec(task_id="t", goal="g", steps=(_step("a", deps=("c",)), _step("b", deps=("a",)), _step("c", deps=("b",))))

    def test_refuses_self_dependency(self) -> None:
        with self.assertRaises(PlanError):
            _step("a", deps=("a",))

    def test_refuses_unknown_dependency(self) -> None:
        with self.assertRaises(PlanError):
            TaskSpec(task_id="t", goal="g", steps=(_step("a", deps=("ghost",)),))

    def test_refuses_duplicate_step_ids(self) -> None:
        with self.assertRaises(PlanError):
            TaskSpec(task_id="t", goal="g", steps=(_step("a"), _step("a")))

    def test_refuses_empty_steps_and_blank_goal(self) -> None:
        with self.assertRaises(ContractError):
            TaskSpec(task_id="t", goal="g", steps=())
        with self.assertRaises(ContractError):
            TaskSpec(task_id="t", goal="   ", steps=(_step("a"),))

    def test_refuses_unsafe_task_id(self) -> None:
        with self.assertRaises(ContractError):
            TaskSpec(task_id="../../etc/passwd", goal="g", steps=(_step("a"),))

    def test_round_trips_through_json(self) -> None:
        spec = TaskSpec(
            task_id="t-1",
            goal="g",
            steps=(_step("a", verification=(EvidenceKind.ARTIFACT_SHA256.value,)),),
            metadata={"k": [1, 2]},
        )
        again = TaskSpec.from_dict(json.loads(json.dumps(spec.to_dict())))
        self.assertEqual(spec.spec_hash(), again.spec_hash())

    def test_from_dict_refuses_unknown_fields(self) -> None:
        raw = {"task_id": "t", "goal": "g", "steps": [], "surprise": 1}
        with self.assertRaises(ContractError):
            TaskSpec.from_dict(raw)

    def test_refuses_unknown_evidence_kind(self) -> None:
        with self.assertRaises(ValueError):
            _step("a", verification=("looks_done_to_me",))


class AgentSpecTests(unittest.TestCase):
    def test_rejects_unknown_evidence_requirement(self) -> None:
        with self.assertRaises(ValueError):
            AgentSpec(name="x", role="r", requires_evidence=("vibes",))

    def test_rejects_bad_attempt_budget_and_timeout(self) -> None:
        with self.assertRaises(ContractError):
            AgentSpec(name="x", role="r", max_attempts=0)
        with self.assertRaises(ContractError):
            AgentSpec(name="x", role="r", timeout_s=0)

    def test_round_trip(self) -> None:
        spec = AgentSpec(
            name="coder",
            role="writes code",
            capabilities=("code",),
            produces=("code",),
            cost_tier=CostTier.FREE_TIER,
            fallbacks=("coder-lite",),
        )
        self.assertEqual(AgentSpec.from_dict(spec.to_dict()), spec)

    def test_parsed_number_is_refused_for_int_field(self) -> None:
        raw = {**AgentSpec(name="x", role="r").to_dict(), "max_attempts": True}
        with self.assertRaises(ContractError):
            AgentSpec.from_dict(raw)


class RecordTests(unittest.TestCase):
    def test_artifact_ref_refuses_bad_digest(self) -> None:
        with self.assertRaises(ContractError):
            ArtifactRef(path="a.txt", sha256="not-a-digest")
        with self.assertRaises(ContractError):
            ArtifactRef(path="a.txt", sha256="A" * 64)  # uppercase is not our format

    def test_step_result_cannot_be_ok_and_failed_at_once(self) -> None:
        with self.assertRaises(ContractError):
            StepResult(ok=True, error="boom")
        with self.assertRaises(ContractError):
            StepResult(ok=False)
        with self.assertRaises(ContractError):
            StepResult(ok=True, cost_usd=-1)

    def test_canonical_json_is_key_order_independent(self) -> None:
        self.assertEqual(canonical_json({"b": 1, "a": 2}), canonical_json({"a": 2, "b": 1}))
        self.assertEqual(len(sha256_text("x")), 64)


if __name__ == "__main__":
    unittest.main(verbosity=2)
