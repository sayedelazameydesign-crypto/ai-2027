"""End-to-end integration tests for ReflectiveCognitivePipeline."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from ai2027_protocol.budget import BudgetPolicy
from ai2027_protocol.contracts import AIProvider, PromptRequest, ProviderKind
from ai2027_protocol.meta_router import MetaRouter, VerificationResult
from ai2027_protocol.reflective_graph_memory import ReflectiveGraphMemory
from ai2027_protocol.reflective_pipeline import ReflectiveCognitivePipeline
from ai2027_protocol.reflective_tree import TreeSearchPolicy
from ai2027_protocol.reflexion_contracts import (
    EvaluationSignal,
    ReflexionPolicy,
    TrialTrajectory,
)
from ai2027_protocol.router import FreeFirstRouter, RouterConfig

_FAST_CFG = RouterConfig(
    per_provider_timeout_s=0.1,
    max_retries_per_provider=0,
    base_backoff_s=0.0,
)
_REPO_ROOT = Path(__file__).resolve().parents[2]


class PipelineProvider:
    """Deterministic local provider recording prompts and returning scripted outputs."""

    kind: ProviderKind = ProviderKind.DETERMINISTIC
    model: str = "pipeline-local-v1"
    is_remote: bool = False

    def __init__(self, outputs: Sequence[str]) -> None:
        self._outputs = list(outputs)
        self.prompts_seen: list[str] = []
        self._idx = 0

    def is_configured(self) -> bool:
        return True

    async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
        del timeout_s
        call_no = self._idx
        self._idx += 1
        self.prompts_seen.append(request.prompt)
        return self._outputs[min(call_no, len(self._outputs) - 1)]


class ExactTokenEvaluator:
    """Independent evaluator passing only when ``VERIFIED_WIN`` is present."""

    def evaluate(self, task: str, trajectory: TrialTrajectory) -> EvaluationSignal:
        del task
        if "VERIFIED_WIN" in trajectory.output:
            return EvaluationSignal(
                passed=True,
                score=1.0,
                reason="independent check passed",
                independent=True,
            )
        return EvaluationSignal(
            passed=False,
            score=0.2,
            reason="missing boundary validation",
            independent=True,
        )


def _make_meta(
    providers: Sequence[AIProvider],
    *,
    verifier_fn: Callable[[str], VerificationResult] | None = None,
) -> MetaRouter:
    router = FreeFirstRouter(
        providers=list(providers),
        config=_FAST_CFG,
        budget_policy=BudgetPolicy(max_spend_usd=0.0),
    )
    return MetaRouter(router, verifier=verifier_fn)


async def test_pipeline_tree_and_reflexion_close_rmm_utility_and_graph_loop(
    tmp_path: Path,
) -> None:
    """Verified tree run indexes failure_mode nodes; second run retrieves & updates RMM utility."""
    db_file = tmp_path / "pipeline.sqlite3"
    provider = PipelineProvider(
        [
            "FIRST_BRANCH_FAIL",
            "VERIFIED_WIN :: sqlite wal lock fix in store.py",
            "VERIFIED_WIN :: second task reuses prior lesson",
            "NEVER_PASSES_1",
            "NEVER_PASSES_2",
        ]
    )
    meta = _make_meta([provider])

    with pytest.raises(ValueError, match="k_lessons"):
        with ReflectiveGraphMemory(db_file) as bad_mem:
            ReflectiveCognitivePipeline(meta, bad_mem, k_lessons=6)

    with ReflectiveGraphMemory(db_file) as mem:
        pipeline = ReflectiveCognitivePipeline(
            meta,
            mem,
            reflexion_policy=ReflexionPolicy(max_trials=2, memory_window=2),
            tree_policy=TreeSearchPolicy(max_branching=2, max_depth=2, max_expansions=4),
            evaluator=ExactTokenEvaluator(),
            k_lessons=2,
        )

        # 1. Auto mode with branching_hint=True selects BoundedReflectiveTreeSearch ("tree").
        first = await pipeline.execute(
            "Implement sqlite wal lock fix in store.py",
            mode="auto",
            branching_hint=True,
            task_type="coding",
        )
        assert first.strategy_used == "tree"
        assert first.verified is True
        assert first.admitted_to_graph is True
        assert first.retrieved_episode_ids == ()
        assert first.overcome_failure_nodes == ("failure_mode:missing boundary validation",)
        assert mem.get_node("failure_mode:missing boundary validation") is not None

        # 2. Second task retrieves episode #1, injects Verified Prior Lessons, and boosts utility!
        second = await pipeline.execute(
            "Extend sqlite wal lock fix in store.py for readers",
            mode="auto",
            branching_hint=False,
            task_type="coding",
        )
        assert second.strategy_used == "reflexion"
        assert second.verified is True
        assert second.admitted_to_graph is True
        assert first.episode.episode_id in second.retrieved_episode_ids
        assert second.utility_rows_updated == 1
        assert "Verified Prior Lessons" in provider.prompts_seen[2]

        retrieved_cnt, helpful_cnt, util = mem.utility_stats(first.episode.episode_id)
        assert retrieved_cnt == 1
        assert helpful_cnt == 1
        assert util > 0.5

        # 3. Third task fails verification -> refused into refusals, RMM records non-helpful!
        third = await pipeline.execute(
            "Broken sqlite wal migration in store.py",
            mode="reflexion",
            task_type="coding",
        )
        assert third.verified is False
        assert third.admitted_to_graph is False
        assert third.overcome_failure_nodes == ()
        stats = mem.stats()
        assert stats["stored"] == 2
        assert stats["refused"] == 1
        # Episode #1 was retrieved in failed task #3: helpful stayed 1, retrieved became 2.
        r_after, h_after, _ = mem.utility_stats(first.episode.episode_id)
        assert r_after == 2
        assert h_after == 1
        assert mem.verify_durability() == []


async def test_end_to_end_whatsapp_webhook_to_reflective_pipeline_to_governed_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full $0 customer loop: Webhook -> ReflectiveCognitivePipeline -> Preflight -> Executor."""
    import hashlib
    import hmac
    import importlib.util
    import json
    import sys
    from datetime import UTC, datetime

    wc_spec = importlib.util.spec_from_file_location(
        "wc_e2e_mod", _REPO_ROOT / "src" / "connectors" / "whatsapp_cloud.py"
    )
    ge_spec = importlib.util.spec_from_file_location(
        "ge_e2e_mod", _REPO_ROOT / "src" / "executors" / "governed_executor.py"
    )
    assert wc_spec is not None and wc_spec.loader is not None
    assert ge_spec is not None and ge_spec.loader is not None
    wc = importlib.util.module_from_spec(wc_spec)
    sys.modules["wc_e2e_mod"] = wc
    wc_spec.loader.exec_module(wc)
    ge = importlib.util.module_from_spec(ge_spec)
    sys.modules["ge_e2e_mod"] = ge
    ge_spec.loader.exec_module(ge)

    monkeypatch.setenv("WHATSAPP_CLOUD_ACCESS_TOKEN", "fixture-" + "e2e-bearer")
    monkeypatch.setenv("WHATSAPP_CLOUD_PHONE_NUMBER_ID", "109876543210987")
    monkeypatch.setattr(
        wc,
        "_transport",
        lambda _m, _u, _b, _tok: wc.CloudTransportResult(
            "ok", 200, {"messages": [{"id": "wamid.E2E_REPLY_OK"}]}
        ),
    )

    now_dt = datetime(2026, 11, 28, 16, 0, 0, tzinfo=UTC)
    now_epoch = int(now_dt.timestamp())
    wamid_in = "wamid.HBgMMjAxMDEyMzQ1Njc4FQIAEhggRTJFX0lO=="
    signing_bytes = b"fixture-e2e-signing-2027"
    webhook_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba_e2e",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": "109876543210987"},
                            "messages": [
                                {
                                    "from": "201012345678",
                                    "id": wamid_in,
                                    "timestamp": str(now_epoch - 90),
                                    "type": "text",
                                    "text": {"body": "ما حالة طلب التطريز رقم 402؟"},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    raw_bytes = json.dumps(webhook_payload, ensure_ascii=False).encode("utf-8")
    sig = "sha256=" + hmac.new(signing_bytes, raw_bytes, hashlib.sha256).hexdigest()
    assert wc.verify_hub_signature(raw_bytes, sig, signing_bytes) == (True, "ok")

    events = wc.parse_inbound_events(webhook_payload, now_epoch_s=now_epoch)
    assert len(events) == 1 and events[0].stage == "accepted_text"

    provider = PipelineProvider(
        ["DRAFT_MISSING_VALIDATION", "VERIFIED_WIN :: الطلب رقم 402 جاهز للتسليم غداً"]
    )
    meta = _make_meta([provider])
    db_file = tmp_path / "e2e_whatsapp_memory.sqlite3"
    with ReflectiveGraphMemory(db_file) as mem:
        pipeline = ReflectiveCognitivePipeline(
            meta,
            mem,
            tree_policy=TreeSearchPolicy(max_branching=2, max_depth=1, max_expansions=2),
            evaluator=ExactTokenEvaluator(),
        )
        cog_res = await pipeline.execute(
            events[0].text_body,
            mode="tree",
            task_type="coding",
        )
        assert cog_res.verified is True
        assert cog_res.admitted_to_graph is True

    reply_payload = {
        "recipient_phone": events[0].sender_wa_id,
        "message_text": cog_res.final_output,
        "inbound_timestamp_s": events[0].timestamp_s,
        "reply_to_wamid": events[0].wamid,
    }
    assert wc.preflight_reply_payload(reply_payload, now_epoch_s=now_epoch) == {
        "ok": True,
        "stage": "ok",
    }

    class _MemRows:
        def __init__(self, rows: list[list[str]]) -> None:
            self.rows = rows

        def get_all_values(self) -> list[list[str]]:
            return [list(r) for r in self.rows]

        def batch_update(self, items: list[dict[str, object]]) -> None:
            for item in items:
                idx = int(str(item["range"]).split(":")[0][1:]) - 1
                vals = item["values"]
                assert isinstance(vals, list)
                self.rows[idx] = [str(c) for c in vals[0]]

    class _MemBook:
        def __init__(self, mapping: dict[str, list[list[str]]]) -> None:
            self._m = {k: _MemRows(v) for k, v in mapping.items()}

        def worksheet(self, name: str) -> _MemRows:
            return self._m[name]

    appr = [""] * 22
    appr[0] = "01APPR_E2E"
    appr[1] = "2026-11-28T15:59:00.000Z"
    appr[2] = "2026-11-28T15:59:00.000Z"
    appr[3] = "1"
    appr[13] = "ef" * 32
    appr[14] = "whatsapp_cloud.send_reply"
    appr[15] = ge.compute_sha256(reply_payload)
    appr[16] = "Customer reply 402"
    appr[17] = "granted"
    appr[18] = "2026-11-28T15:59:00.000Z"
    appr[19] = "2026-11-28T17:00:00.000Z"
    appr[21] = "true"

    grant = [""] * 23
    grant[0] = "01GRANT_E2E"
    grant[14] = "agent.write"
    grant[15] = "active"
    grant[17] = json.dumps(["whatsapp_cloud.send_reply"])
    grant[18] = json.dumps([])
    grant[21] = "owner"

    book = _MemBook(
        {
            "pa_approvals": [["h"] * 22, appr],
            "pa_delegation_grants": [["h"] * 23, grant],
            "pa_audit_logs_202611": [["h"] * 25],
        }
    )
    audits: list[dict[str, object]] = []
    executor = ge.GovernedExecutor(book, lambda **kw: audits.append(kw), now_fn=lambda: now_dt)
    executor.register_tool(
        "whatsapp_cloud.send_reply",
        wc.make_send_reply_handler(now_epoch_fn=lambda: now_epoch),
    )
    exec_res = executor.execute("01APPR_E2E", "whatsapp_cloud.send_reply", reply_payload)
    assert exec_res.ok is True
    assert exec_res.output["outbound_message_id"] == "wamid.E2E_REPLY_OK"
    assert audits[-1]["outcome"] == "allowed"


async def test_zero_cost_orchestrator_reflective_pipeline_and_governed_bridge_ycloud(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """YCloud v2 -> ZeroCostOrchestrator + ReflectiveCognitivePipeline -> GovernedReplyBridge."""
    import hashlib
    import hmac
    import importlib.util
    import json
    import sys
    from datetime import UTC, datetime

    fs_spec = importlib.util.spec_from_file_location(
        "free_stack_e2e", _REPO_ROOT / "scripts" / "free_stack_runner.py"
    )
    assert fs_spec is not None and fs_spec.loader is not None
    fs = importlib.util.module_from_spec(fs_spec)
    sys.modules["free_stack_e2e"] = fs
    fs_spec.loader.exec_module(fs)

    signing_fixture = "whsec_ycloud_" + "bridge_2027"
    monkeypatch.setenv("YCLOUD_WEBHOOK_SECRET", signing_fixture)
    monkeypatch.setenv("YCLOUD_API_KEY", "yc_bridge_" + "fixture_key")
    monkeypatch.setenv("YCLOUD_WHATSAPP_FROM", "+15550109999")
    monkeypatch.delenv("WHATSAPP_CLOUD_ACCESS_TOKEN", raising=False)

    now_epoch = 1795878000  # 2026-11-28T15:00:00Z
    now_dt = datetime(2026, 11, 28, 15, 0, 0, tzinfo=UTC)

    conn = fs.init_db(":memory:")
    try:
        orch = fs.ZeroCostOrchestrator(conn, now_fn=lambda: now_epoch)
        promo = orch.issue_coupon("201012345678", discount_pct=15)

        provider = PipelineProvider(
            ["DRAFT_NO_COUPON", f"VERIFIED_WIN :: تفضلي كود خصم Celia الخاص بك: {promo}"]
        )
        meta = _make_meta([provider])
        with ReflectiveGraphMemory(tmp_path / "rmm_bridge.sqlite3") as mem:
            pipeline = ReflectiveCognitivePipeline(
                meta,
                mem,
                tree_policy=TreeSearchPolicy(max_branching=2, max_depth=1, max_expansions=2),
                evaluator=ExactTokenEvaluator(),
            )
            cog_out = await pipeline.execute(
                "أرسلي كود خصم للعميلة 201012345678",
                mode="tree",
                task_type="coding",
            )
            assert cog_out.verified is True

        orch_reflective = fs.ZeroCostOrchestrator(
            conn,
            now_fn=lambda: now_epoch,
            reply_generator=lambda _ev, _o: cog_out.final_output,
        )
        event = {
            "id": "evt_bridge_01",
            "type": "whatsapp.inbound_message.received",
            "whatsappInboundMessage": {
                "id": "yim_bridge_2027_01",
                "from": "+201012345678",
                "to": "+15550109999",
                "sendTime": "2026-11-28T14:59:50.000Z",
                "type": "text",
                "text": {"body": "ممكن كود خصم؟"},
            },
        }
        raw = json.dumps(event, ensure_ascii=False).encode()
        sig = hmac.new(
            signing_fixture.encode(),
            (f"{now_epoch}.").encode() + raw,
            hashlib.sha256,
        ).hexdigest()
        ingested = orch_reflective.ingest_signed_webhook(
            raw, ycloud_signature=f"t={now_epoch},s={sig}"
        )
        assert ingested["ok"] is True
        assert ingested["stages"] == ["pending_approval"]

        payload = orch_reflective.build_outbound_payload("yim_bridge_2027_01")
        assert payload is not None
        assert promo in payload["message_text"]

        wc = orch_reflective._wc
        ge = orch_reflective._ge
        monkeypatch.setattr(
            wc,
            "_transport",
            lambda m, u, b, k: wc.CloudTransportResult(
                "ok", 200, {"id": "yim_out_bridge_01", "status": "accepted"}
            ),
        )

        class _WS:
            def __init__(self, rows: list[list[str]]) -> None:
                self._rows = rows

            def get_all_values(self) -> list[list[str]]:
                return [list(r) for r in self._rows]

            def batch_update(self, items: list[dict[str, object]]) -> None:
                for it in items:
                    idx = int(str(it["range"]).split(":")[0][1:]) - 1
                    vals = it["values"]
                    assert isinstance(vals, list)
                    self._rows[idx] = [str(c) for c in vals[0]]

        appr = [""] * 22
        appr[0] = "01APPR_BRIDGE"
        appr[1] = "2026-11-28T15:00:00.000Z"
        appr[2] = "2026-11-28T15:00:00.000Z"
        appr[3] = "1"
        appr[14] = "whatsapp_cloud.send_reply"
        appr[15] = ge.compute_sha256(payload)
        appr[16] = "Reflective coupon reply"
        appr[17] = "granted"
        appr[18] = "2026-11-28T15:00:00.000Z"
        appr[19] = "2026-11-29T15:00:00.000Z"
        appr[21] = "true"
        appr[13] = ge.approval_checksum(ge.approval_from_row(appr))

        grant = [""] * 23
        grant[0] = "01GRANT_BRIDGE"
        grant[14] = "agent.write"
        grant[15] = "active"
        grant[17] = json.dumps(["whatsapp_cloud.send_reply"])
        grant[18] = json.dumps([])
        grant[19] = json.dumps({"max_per_day": 10})
        grant[21] = "user"

        tables = {
            "pa_approvals": [["h"] * 22, appr],
            "pa_delegation_grants": [["h"] * 23, grant],
            "pa_audit_logs_202611": [["h"] * 25],
        }

        class _Book:
            def worksheet(self, name: str) -> _WS:
                return _WS(tables[name])

        executor = ge.GovernedExecutor(_Book(), lambda **kw: None, now_fn=lambda: now_dt)
        bridge = fs.GovernedReplyBridge(orch_reflective, executor)
        sent = bridge.dispatch_approved_reply(
            wamid="yim_bridge_2027_01", approval_id="01APPR_BRIDGE"
        )
        assert sent == {
            "ok": True,
            "stage": "replied",
            "outbound_message_id": "yim_out_bridge_01",
        }
    finally:
        conn.close()
