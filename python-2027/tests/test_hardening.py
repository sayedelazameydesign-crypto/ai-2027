from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from ai2027_protocol.ai_providers import (
    DeterministicFallbackProvider,
    OpenAICompatibleFreeProvider,
    create_openrouter_free_provider,
)
from ai2027_protocol.budget import BudgetGuard, BudgetPolicy, classify_billing_tier
from ai2027_protocol.cli import main
from ai2027_protocol.compliance import (
    ProjectTier,
    build_compliance_registry,
    format_compliance_markdown,
    verify_adr_0002_preserved,
)
from ai2027_protocol.contracts import (
    BillingTier,
    CostStatus,
    PromptRequest,
    ProviderKind,
)
from ai2027_protocol.router import FreeFirstRouter
from ai2027_protocol.server import create_app
from ai2027_protocol.telemetry import (
    HttpOtlpSpanExporter,
    ObservabilityConfig,
    ObservabilityMode,
    ObservabilityRuntime,
    extract_trace_context,
    inject_trace_headers,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


async def test_budget_guard_blocks_unknown_and_paid_at_max_spend_zero() -> None:
    paid_openrouter = OpenAICompatibleFreeProvider(
        kind=ProviderKind.OPENROUTER,
        endpoint_url="https://openrouter.ai/api/v1/chat/completions",
        api_key="sk-or-live-looking-key",
        model="openai/gpt-4o",  # Missing :free -> must be UNKNOWN_COST!
    )
    tier_u, _ = classify_billing_tier(
        kind=paid_openrouter.kind,
        model=paid_openrouter.model,
        is_remote=paid_openrouter.is_remote,
    )
    assert tier_u == BillingTier.UNKNOWN_COST

    free_openrouter = create_openrouter_free_provider(
        api_key="sk-or-free-key",
        model="meta-llama/llama-3.3-70b-instruct:free",
    )
    tier_f, _ = classify_billing_tier(
        kind=free_openrouter.kind,
        model=free_openrouter.model,
        is_remote=free_openrouter.is_remote,
    )
    assert tier_f == BillingTier.FREE_TIER

    guard = BudgetGuard(BudgetPolicy(max_spend_usd=0.0))
    dec_unknown = guard.evaluate_provider(paid_openrouter)
    assert dec_unknown.allowed is False
    assert dec_unknown.cost_status == CostStatus.UNKNOWN_COST_BLOCKED
    assert dec_unknown.cost_known is False

    dec_free = guard.evaluate_provider(free_openrouter)
    assert dec_free.allowed is True
    assert dec_free.cost_status == CostStatus.DECLARED_FREE_TIER_UNBILLED
    assert dec_free.cost_known is False

    # Even with an API key present, router MUST block unknown-cost OpenRouter and fall back locally
    router = FreeFirstRouter(
        providers=[paid_openrouter, DeterministicFallbackProvider()],
        budget_guard=guard,
    )
    resp = await router.route(PromptRequest(prompt="Verify $0 execution guard"))
    assert resp.provider == ProviderKind.DETERMINISTIC
    assert resp.billing_tier == BillingTier.LOCAL
    assert resp.cost_known is True
    assert resp.cost_status == CostStatus.PROVEN_ZERO_LOCAL
    assert resp.attempts[0].error_code == "budget_blocked"

    snap = router.budget_snapshot()
    assert snap.unknown_cost_blocked == 1
    assert snap.known_zero_local_calls == 1
    assert snap.declared_free_tier_calls == 0


def test_observability_persistent_distributed_and_export(tmp_path: Path) -> None:
    exported_payloads: list[dict[str, object]] = []

    def _otlp_handler(request: httpx.Request) -> httpx.Response:
        exported_payloads.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"accepted": True})

    with httpx.Client(transport=httpx.MockTransport(_otlp_handler)) as client:
        exporter = HttpOtlpSpanExporter(
            endpoint_url="https://otlp.example.test/v1/spans",
            client=client,
        )
        runtime = ObservabilityRuntime(
            config=ObservabilityConfig(
                mode=ObservabilityMode.EXPORT,
                persist_dir=tmp_path / "telemetry",
                otlp_endpoint=None,
            ),
            extra_exporters=(exporter,),
        )
        incoming_tid = "0123456789abcdef0123456789abcdef"
        parent_ctx = extract_trace_context(
            {"traceparent": f"00-{incoming_tid}-1111222233334444-01"}
        )

        with runtime.start_span("hardening.span", {"tier": "free_tier"}, parent_context=parent_ctx):
            runtime.log("info", "distributed_event", cost_known=False)
            propagated = inject_trace_headers()
            assert propagated["x-trace-id"] == incoming_tid

        persisted = runtime.persisted_spans()
        assert len(persisted) == 1
        assert persisted[0].trace_id == incoming_tid
        assert (tmp_path / "telemetry" / "logs.jsonl").is_file()

        exported_counts = runtime.export_now()
        assert exported_counts[exporter.name] == 1
        assert len(exported_payloads) == 1
        spans_list = exported_payloads[0]["spans"]
        assert isinstance(spans_list, list)
        assert spans_list[0]["trace_id"] == incoming_tid
        summary = runtime.status_summary()
        assert summary["mode"] == "export"
        assert summary["persistent_enabled"] is True
        runtime.shutdown()


def test_server_budget_and_distributed_trace_endpoints(tmp_path: Path) -> None:
    runtime = ObservabilityRuntime(
        config=ObservabilityConfig(
            mode=ObservabilityMode.PERSISTENT,
            persist_dir=tmp_path / "srv_traces",
        )
    )
    app = create_app(telemetry=runtime, read_only=True)
    with TestClient(app) as client:
        tid = "abcdefabcdefabcdefabcdefabcdefab"
        budget_res = client.get(
            "/v1/budget",
            headers={"traceparent": f"00-{tid}-9999888877776666-01"},
        )
        assert budget_res.status_code == 200
        assert budget_res.headers["x-trace-id"] == tid
        assert budget_res.headers["traceparent"].startswith(f"00-{tid}-")
        assert budget_res.json()["max_spend_usd"] == 0.0

        traces_status = client.get("/v1/traces/status")
        assert traces_status.status_code == 200
        assert traces_status.json()["persistent_enabled"] is True

        persisted = client.get("/v1/traces?persisted=true")
        assert persisted.status_code == 200
        assert isinstance(persisted.json(), list)
        assert len(persisted.json()) >= 1

        export_ro = client.post("/v1/traces/export")
        assert export_ro.status_code == 403

        compliance_res = client.get("/v1/studio/compliance")
        assert compliance_res.status_code == 200
        comp = compliance_res.json()
        assert comp["catalog_total_projects"] == 48
        assert comp["adr_0002_preserved"] is True

        studio_st = client.get("/v1/studio/status").json()
        assert "budget" in studio_st
        assert "observability" in studio_st

    rw_app = create_app(telemetry=runtime, read_only=False)
    with TestClient(rw_app) as rw_client:
        export_res = rw_client.post("/v1/traces/export")
        assert export_res.status_code == 200
        assert export_res.json()["exported"] == {}


async def test_compliance_registry_48_projects_and_adr_0002(tmp_path: Path) -> None:
    report = await build_compliance_registry(_REPO_ROOT)
    assert report.catalog_total_projects == 48
    assert report.entries_count >= 50
    assert report.adr_0002_preserved is True
    assert ProjectTier.TIER_1_PRODUCTION.value in report.tier_counts
    assert ProjectTier.TIER_2_EDGE_LIGHTWEIGHT.value in report.tier_counts
    assert ProjectTier.TIER_3_LEGACY_OR_KERNEL.value in report.tier_counts
    md = format_compliance_markdown(report)
    assert "AI-2027 Three-Tier Compliance Registry" in md
    assert "ADR-0002 zero-dep kernel preserved: **True**" in md

    # Synthetic ADR-0002 breach check must catch forbidden non-stdlib imports in kernel
    bad_kernel = tmp_path / "projects" / "agi-system" / "core"
    bad_kernel.mkdir(parents=True)
    (bad_kernel / "bad.py").write_text("import pydantic\n", encoding="utf-8")
    assert verify_adr_0002_preserved(tmp_path) is False


def test_cli_registry_command() -> None:
    assert main(["--root", str(_REPO_ROOT), "registry", "--json"]) == 0


def test_studio_html_exposes_hardening_panels() -> None:
    html_path = _REPO_ROOT / "python-2027" / "src" / "ai2027_protocol" / "static" / "studio.html"
    html = html_path.read_text(encoding="utf-8")
    assert "loadComplianceRegistry" in html
    assert "loadBudgetAndObs" in html
    assert "renderEvidencePanel" in html
    assert "MAX_SPEND $0.00" in html
    assert "48-Project Registry" in html
