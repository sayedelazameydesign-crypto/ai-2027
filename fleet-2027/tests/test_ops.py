"""Local, network-free tests for the AWR-Ops request/proposal/approval path."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from fleet2027 import (  # noqa: E402
    AwrApproval,
    AwrExecutionDeniedError,
    AwrOps,
    AwrProposal,
    AwrTaskRequest,
    ContractError,
    DurableTaskStore,
    canonical_json,
    OpsState,
    OpsTransitionError,
    PlanStep,
    StaleProposalError,
    TaskConflictError,
    TaskSpec,
    decide_transition,
    sha256_text,
)

_NOW_1 = "2026-11-19T10:00:00.000000Z"
_NOW_2 = "2026-11-19T10:01:00.000000Z"
_NOW_3 = "2026-11-19T10:02:00.000000Z"
_NOW_4 = "2026-11-19T10:03:00.000000Z"


def _request(task_id: str = "awr-1", *, goal: str = "fix the local example") -> AwrTaskRequest:
    return AwrTaskRequest(
        task_id=task_id,
        goal=goal,
        requested_by="requester-7",
        facts=("The local test currently fails",),
        requirements=("Keep the change local",),
        constraints=("Do not use the network",),
        sentiment_signals=("The failure is frustrating",),
    )


def _proposal(request: AwrTaskRequest, *, summary: str = "Propose a narrow fix") -> AwrProposal:
    task_spec = TaskSpec(
        task_id=request.task_id,
        goal=request.goal,
        created_by="ops-planner",
        metadata={"target": "fleet-2027", "network": False},
        steps=(
            PlanStep(
                step_id="inspect",
                agent="local-reviewer",
                action="inspect-local-tests",
                inputs={"scope": "fleet-2027"},
                verification=("output_non_empty",),
            ),
        ),
    )
    return AwrProposal(
        task_id=request.task_id,
        request_sha256=request.request_sha256,
        summary=summary,
        task_spec=task_spec,
        acceptance_criteria=("The targeted local test passes",),
        created_by="ops-planner",
        risk_level="low",
        rollback_plan="Revert the local change if verification fails",
        affected_paths=("fleet-2027/",),
        non_goals=("No network calls or external repository writes",),
        assumptions=("The request describes the observed test failure accurately",),
        unknowns=("Whether the same failure occurs on other platforms",),
        risk_notes=("No execution is performed in this phase",),
    )


def _approval(proposal: AwrProposal, *, at: str = _NOW_2, approved: bool = True) -> AwrApproval:
    return AwrApproval(
        task_id=proposal.task_id,
        proposal_sha256=proposal.proposal_sha256,
        approved=approved,
        approver_id="human-reviewer-2",
        decided_at=at,
    )


class AwrOpsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = DurableTaskStore(str(Path(self.temp.name) / "fleet.db"))
        self.addCleanup(self.store.close)
        self.ops = AwrOps(self.store)

    def test_new_request_is_requested_and_never_queued_for_execution(self) -> None:
        request = _request()
        self.assertTrue(self.ops.create_request(request, now=_NOW_1))

        snapshot = self.ops.get_task(request.task_id)
        self.assertEqual(snapshot.state, OpsState.REQUESTED)
        self.assertEqual(snapshot.request.sentiment_signals, ("The failure is frustrating",))
        self.assertIsNone(snapshot.proposal)
        self.assertIsNone(snapshot.approval)
        self.assertFalse(snapshot.approved_for_current_proposal)
        self.assertEqual(snapshot.last_checked_at, _NOW_1)
        with self.assertRaises(AwrExecutionDeniedError) as raised:
            self.store.submit(_proposal(request).task_spec, now=_NOW_2)
        self.assertEqual(raised.exception.reason_code, "approval_missing_or_stale")
        self.assertEqual(
            self.store._conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0,
            "AWR intake must not create a runnable Fleet task",
        )
        ok, _, problem = self.store.verify_audit_chain()
        self.assertTrue(ok, problem)

    def test_request_is_idempotent_by_stable_id_and_detects_changed_content(self) -> None:
        request = _request()
        self.assertTrue(self.ops.create_request(request, now=_NOW_1))
        audit_before = self.store.stats()["audit_rows"]
        self.assertFalse(self.ops.create_request(request, now=_NOW_2))
        self.assertEqual(self.ops.get_task(request.task_id).last_checked_at, _NOW_2)
        self.assertEqual(self.store.stats()["audit_rows"], audit_before)
        self.assertEqual(
            self.store._conn.execute("SELECT COUNT(*) FROM awr_ops_workflows").fetchone()[0], 1
        )
        with self.assertRaises(TaskConflictError):
            self.ops.create_request(replace(request, goal="different request"), now=_NOW_3)
        self.assertEqual(self.ops.get_task(request.task_id).state, OpsState.REQUESTED)

    def test_existing_queued_task_id_cannot_be_relabelled_as_awr(self) -> None:
        request = _request()
        self.store.submit(_proposal(request).task_spec, now=_NOW_1)

        with self.assertRaises(OpsTransitionError):
            self.ops.create_request(request, now=_NOW_2)
        self.assertEqual(
            self.store._conn.execute("SELECT COUNT(*) FROM awr_ops_workflows").fetchone()[0], 0
        )
        self.assertEqual(
            self.store._conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1
        )

    def test_non_awr_task_submission_remains_idempotent(self) -> None:
        ordinary = TaskSpec(
            task_id="ordinary-task",
            goal="keep normal Fleet intake working",
            steps=(PlanStep(step_id="inspect", agent="local", action="inspect"),),
        )

        self.assertTrue(self.store.submit(ordinary, now=_NOW_1))
        self.assertFalse(self.store.submit(ordinary, now=_NOW_2))
        self.assertEqual(self.store.spec(ordinary.task_id), ordinary)
        self.assertEqual(self.store.get_task(ordinary.task_id).status.value, "queued")
        self.assertEqual(
            self.store._conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE kind = 'task.submitted'"
            ).fetchone()[0],
            1,
        )

    def test_concurrent_retries_on_separate_connections_create_one_workflow(self) -> None:
        db = str(Path(self.temp.name) / "shared.db")
        first_store = DurableTaskStore(db)
        second_store = DurableTaskStore(db)
        self.addCleanup(first_store.close)
        self.addCleanup(second_store.close)
        first = AwrOps(first_store)
        second = AwrOps(second_store)
        request = _request("concurrent-task")
        barrier = threading.Barrier(2)
        results: list[bool] = []
        errors: list[BaseException] = []

        def contender(service: AwrOps, at: str) -> None:
            try:
                barrier.wait()
                results.append(service.create_request(request, now=at))
            except BaseException as exc:  # capture thread failures for the assertion
                errors.append(exc)

        threads = [
            threading.Thread(target=contender, args=(first, _NOW_1)),
            threading.Thread(target=contender, args=(second, _NOW_2)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertCountEqual(results, [True, False])
        self.assertEqual(
            first_store._conn.execute(
                "SELECT COUNT(*) FROM awr_ops_workflows WHERE task_id = ?",
                (request.task_id,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            first_store._conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE kind = 'awr.requested'"
            ).fetchone()[0],
            1,
        )

    def test_two_concurrent_approvals_and_queue_attempts_fail_closed(self) -> None:
        second_store = DurableTaskStore(self.store.path)
        self.addCleanup(second_store.close)
        second_ops = AwrOps(second_store)
        prepared: list[tuple[AwrOps, AwrProposal, AwrApproval]] = []
        for service, task_id in (
            (self.ops, "awr-race-one"),
            (second_ops, "awr-race-two"),
        ):
            request = _request(task_id)
            proposal = _proposal(request)
            service.create_request(request, now=_NOW_1)
            service.submit_proposal(proposal, now=_NOW_2)
            prepared.append((service, proposal, _approval(proposal, at=_NOW_3)))

        barrier = threading.Barrier(2)
        denied: list[str] = []
        errors: list[BaseException] = []

        def approve_and_submit(
            service: AwrOps, proposal: AwrProposal, approval: AwrApproval
        ) -> None:
            try:
                barrier.wait()
                if not service.record_approval(approval):
                    errors.append(AssertionError("fresh approval unexpectedly became a retry"))
                    return
                try:
                    service.store.submit(proposal.task_spec, now=_NOW_4)
                except AwrExecutionDeniedError as exc:
                    denied.append(exc.reason_code)
                else:
                    errors.append(AssertionError("AWR task passed the closed admission gate"))
            except BaseException as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=approve_and_submit, args=item) for item in prepared
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertCountEqual(
            denied, ["trusted_identity_source_unavailable"] * 2
        )
        self.assertEqual(self.store._conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
        self.assertEqual(
            self.store._conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE kind = 'awr.approval.granted'"
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            self.store._conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE kind = 'awr.execution.denied'"
            ).fetchone()[0],
            2,
        )
        for service, proposal, _ in prepared:
            self.assertTrue(service.get_task(proposal.task_id).approved_for_current_proposal)
        ok, _, problem = self.store.verify_audit_chain()
        self.assertTrue(ok, problem)

    def test_request_payload_is_not_copied_into_audit_log(self) -> None:
        request = _request(goal="private customer wording that is not needed in logs")
        self.ops.create_request(request, now=_NOW_1)
        audit_body = self.store._conn.execute(
            "SELECT payload_json FROM audit_log WHERE kind = 'awr.requested'"
        ).fetchone()[0]
        self.assertNotIn("private customer wording", audit_body)
        self.assertIn(request.request_sha256, audit_body)

    def test_proposal_hash_changes_for_review_and_execution_fields(self) -> None:
        request = _request()
        base = _proposal(request)
        changed_step = replace(
            base.task_spec,
            steps=(replace(base.task_spec.steps[0], action="different-action"),),
        )
        variants = (
            replace(base, summary="different summary"),
            replace(base, task_spec=changed_step),
            replace(base, acceptance_criteria=("different test",)),
            replace(base, risk_level="high"),
            replace(base, rollback_plan="different rollback"),
            replace(base, affected_paths=("another/path",)),
            replace(base, non_goals=("different non-goal",)),
            replace(base, assumptions=("different assumption",)),
            replace(base, unknowns=("different unknown",)),
            replace(base, risk_notes=("different risk",)),
            replace(base, request_sha256="f" * 64),
        )
        self.assertEqual(base.proposal_sha256, _proposal(request).proposal_sha256)
        for changed in variants:
            with self.subTest(changed=changed):
                self.assertNotEqual(base.proposal_sha256, changed.proposal_sha256)

    def test_explicit_approval_is_bound_to_current_proposal_and_audited(self) -> None:
        request = _request()
        proposal = _proposal(request)
        self.ops.create_request(request, now=_NOW_1)
        self.assertTrue(self.ops.submit_proposal(proposal, now=_NOW_2))
        proposed = self.ops.get_task(request.task_id)
        self.assertEqual(proposed.state, OpsState.PROPOSED)
        self.assertEqual(proposed.proposal_sha256, proposal.proposal_sha256)
        self.assertIsNone(proposed.approval)
        self.assertEqual(
            self.store._conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0,
            "A proposal remains non-runnable even before approval",
        )

        approval = _approval(proposal, at=_NOW_3)
        self.assertTrue(self.ops.record_approval(approval))
        approved = self.ops.get_task(request.task_id)
        self.assertEqual(approved.state, OpsState.APPROVED)
        self.assertEqual(approved.approval, approval)
        self.assertTrue(approved.approved_for_current_proposal)
        kinds = {
            row["kind"]
            for row in self.store._conn.execute(
                "SELECT kind FROM audit_log WHERE task_id = ?", (request.task_id,)
            )
        }
        self.assertIn("awr.requested", kinds)
        self.assertIn("awr.proposal.submitted", kinds)
        self.assertIn("awr.approval.granted", kinds)
        ok, _, problem = self.store.verify_audit_chain()
        self.assertTrue(ok, problem)

    def test_unverified_approver_cannot_submit_and_retries_are_audited(self) -> None:
        request = _request()
        proposal = _proposal(request)
        self.ops.create_request(request, now=_NOW_1)
        self.ops.submit_proposal(proposal, now=_NOW_2)
        claimed_approval = replace(
            _approval(proposal, at=_NOW_3), approver_id="self-asserted-admin"
        )
        self.ops.record_approval(claimed_approval)
        self.assertTrue(self.ops.get_task(request.task_id).approved_for_current_proposal)

        for attempted_at in (_NOW_3, _NOW_4):
            with self.assertRaises(AwrExecutionDeniedError) as raised:
                self.store.submit(proposal.task_spec, now=attempted_at)
            self.assertEqual(
                raised.exception.reason_code, "trusted_identity_source_unavailable"
            )

        self.assertEqual(self.store._conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
        self.assertEqual(self.ops.get_task(request.task_id).last_checked_at, _NOW_4)
        denied = self.store._conn.execute(
            "SELECT payload_json FROM audit_log WHERE kind = 'awr.execution.denied'"
        ).fetchall()
        self.assertEqual(len(denied), 2)
        self.assertTrue(all("trusted_identity_source_unavailable" in row[0] for row in denied))
        self.assertTrue(all("self-asserted-admin" not in row[0] for row in denied))
        ok, _, problem = self.store.verify_audit_chain()
        self.assertTrue(ok, problem)

    def test_resume_import_cannot_bypass_the_awr_admission_gate(self) -> None:
        request = _request()
        proposal = _proposal(request)
        self.ops.create_request(request, now=_NOW_1)
        self.ops.submit_proposal(proposal, now=_NOW_2)
        self.ops.record_approval(_approval(proposal, at=_NOW_3))
        seed = {
            "kind": "fleet-2027/resume-seed",
            "version": 1,
            "exported_at": _NOW_4,
            "task": proposal.task_spec.to_dict(),
            "spec_sha256": proposal.task_spec.spec_hash(),
            "completed": [],
            "degraded": False,
        }
        seed["sha256"] = sha256_text(canonical_json(seed))

        with self.assertRaises(AwrExecutionDeniedError) as raised:
            self.store.import_resume_seed(seed, now=_NOW_4)
        self.assertEqual(raised.exception.reason_code, "trusted_identity_source_unavailable")
        self.assertEqual(self.store._conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
        self.assertEqual(
            self.store._conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE kind = 'awr.execution.denied'"
            ).fetchone()[0],
            1,
        )
        ok, _, problem = self.store.verify_audit_chain()
        self.assertTrue(ok, problem)

    def test_queue_gate_requires_the_exact_approved_task_spec(self) -> None:
        request = _request()
        proposal = _proposal(request)
        self.ops.create_request(request, now=_NOW_1)
        self.ops.submit_proposal(proposal, now=_NOW_2)
        self.ops.record_approval(_approval(proposal, at=_NOW_3))
        changed_spec = replace(
            proposal.task_spec,
            steps=(replace(proposal.task_spec.steps[0], action="unreviewed-action"),),
        )

        with self.assertRaises(AwrExecutionDeniedError) as raised:
            self.store.submit(changed_spec, now=_NOW_4)
        self.assertEqual(raised.exception.reason_code, "submitted_spec_mismatch")
        self.assertEqual(self.store._conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
        ok, _, problem = self.store.verify_audit_chain()
        self.assertTrue(ok, problem)

    def test_changed_proposal_invalidates_approval_and_retains_old_revision(self) -> None:
        request = _request()
        proposal = _proposal(request)
        self.ops.create_request(request, now=_NOW_1)
        self.ops.submit_proposal(proposal, now=_NOW_2)
        approval = _approval(proposal, at=_NOW_3)
        self.ops.record_approval(approval)

        revised = replace(
            proposal,
            acceptance_criteria=("A different local verification must pass",),
        )
        self.assertTrue(self.ops.submit_proposal(revised, now=_NOW_3))
        current = self.ops.get_task(request.task_id)
        self.assertEqual(current.state, OpsState.PROPOSED)
        self.assertNotEqual(current.proposal_sha256, approval.proposal_sha256)
        self.assertIsNone(current.approval)
        self.assertFalse(current.approved_for_current_proposal)
        revisions = self.ops.proposal_history(request.task_id)
        self.assertEqual([item.revision for item in revisions], [1, 2])
        self.assertEqual(revisions[0].proposal, proposal)
        self.assertEqual(revisions[1].proposal, revised)

        with self.assertRaises(StaleProposalError):
            self.ops.record_approval(approval)
        self.assertEqual(self.ops.get_task(request.task_id), current)
        with self.assertRaises(AwrExecutionDeniedError) as raised:
            self.store.submit(revised.task_spec, now=_NOW_4)
        self.assertEqual(raised.exception.reason_code, "approval_missing_or_stale")
        self.assertEqual(self.store._conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
        kinds = {
            row["kind"]
            for row in self.store._conn.execute(
                "SELECT kind FROM audit_log WHERE task_id = ?", (request.task_id,)
            )
        }
        self.assertIn("awr.approval.invalidated", kinds)
        ok, _, problem = self.store.verify_audit_chain()
        self.assertTrue(ok, problem)

    def test_identical_proposal_retry_keeps_existing_approval(self) -> None:
        request = _request()
        proposal = _proposal(request)
        self.ops.create_request(request, now=_NOW_1)
        self.ops.submit_proposal(proposal, now=_NOW_2)
        approval = _approval(proposal, at=_NOW_3)
        self.ops.record_approval(approval)
        audit_before = self.store.stats()["audit_rows"]

        self.assertFalse(self.ops.submit_proposal(proposal, now=_NOW_3))
        self.assertFalse(self.ops.record_approval(approval))
        current = self.ops.get_task(request.task_id)
        self.assertEqual(current.state, OpsState.APPROVED)
        self.assertEqual(current.approval, approval)
        self.assertEqual(self.store.stats()["audit_rows"], audit_before)

    def test_forbidden_transitions_fail_without_mutating_state(self) -> None:
        self.assertTrue(decide_transition(OpsState.REQUESTED, OpsState.PROPOSED).allowed)
        self.assertFalse(decide_transition(OpsState.REQUESTED, OpsState.APPROVED).allowed)
        self.assertFalse(decide_transition(OpsState.APPROVED, OpsState.REJECTED).allowed)
        self.assertTrue(decide_transition(OpsState.PROPOSED, OpsState.PROPOSED).allowed)

        request = _request()
        self.ops.create_request(request, now=_NOW_1)
        before = self.ops.get_task(request.task_id)
        premature = AwrApproval(
            task_id=request.task_id,
            proposal_sha256="0" * 64,
            approved=True,
            approver_id="human-reviewer-2",
            decided_at=_NOW_2,
        )
        with self.assertRaises(OpsTransitionError):
            self.ops.record_approval(premature)
        self.assertEqual(self.ops.get_task(request.task_id), before)

    def test_a_second_different_decision_cannot_overwrite_a_human_decision(self) -> None:
        request = _request()
        proposal = _proposal(request)
        self.ops.create_request(request, now=_NOW_1)
        self.ops.submit_proposal(proposal, now=_NOW_2)
        first = _approval(proposal, at=_NOW_3)
        self.ops.record_approval(first)
        before = self.ops.get_task(request.task_id)
        other = replace(first, approver_id="different-reviewer")
        with self.assertRaises(OpsTransitionError):
            self.ops.record_approval(other)
        self.assertEqual(self.ops.get_task(request.task_id), before)

    def test_workflow_survives_reopening_the_same_fleet_database(self) -> None:
        request = _request()
        proposal = _proposal(request)
        self.ops.create_request(request, now=_NOW_1)
        self.ops.submit_proposal(proposal, now=_NOW_2)
        approval = _approval(proposal, at=_NOW_3)
        self.ops.record_approval(approval)
        expected = self.ops.get_task(request.task_id)
        db = self.store.path
        self.store.close()

        reopened = DurableTaskStore(db)
        try:
            self.assertEqual(AwrOps(reopened).get_task(request.task_id), expected)
            self.assertEqual(len(AwrOps(reopened).proposal_history(request.task_id)), 1)
            self.assertGreaterEqual(reopened.stats()["schema_version"], 4)
        finally:
            reopened.close()

    def test_v3_database_gets_additive_ops_tables_without_losing_tasks(self) -> None:
        db = Path(self.temp.name) / "legacy-v3.db"
        legacy = DurableTaskStore(db)
        spec = TaskSpec(
            task_id="legacy-task",
            goal="keep existing task data",
            steps=(PlanStep(step_id="step", agent="local", action="inspect"),),
        )
        legacy.submit(spec)
        legacy.close()

        with sqlite3.connect(db) as connection:
            connection.execute("DROP TABLE awr_ops_proposals")
            connection.execute("DROP TABLE awr_ops_workflows")
            connection.execute("UPDATE meta SET value = '3' WHERE key = 'schema_version'")

        migrated = DurableTaskStore(db)
        try:
            self.assertEqual(migrated.stats()["schema_version"], 4)
            self.assertEqual(migrated.spec("legacy-task"), spec)
            tables = {
                row[0]
                for row in migrated._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertIn("awr_ops_workflows", tables)
            self.assertIn("awr_ops_proposals", tables)
        finally:
            migrated.close()

    def test_contracts_reject_unknown_fields_bad_digests_and_naive_times(self) -> None:
        request = _request()
        with self.assertRaises(ContractError):
            AwrTaskRequest.from_dict({**request.to_dict(), "surprise": True})
        proposal = _proposal(request)
        with self.assertRaises(ContractError):
            AwrApproval(
                task_id=request.task_id,
                proposal_sha256="not-a-hash",
                approved=True,
                approver_id="human-reviewer-2",
                decided_at=_NOW_2,
            )
        with self.assertRaises(ContractError):
            replace(_approval(proposal), decided_at="2026-11-19T10:00:00")


if __name__ == "__main__":
    unittest.main(verbosity=2)
