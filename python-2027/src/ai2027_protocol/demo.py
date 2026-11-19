"""One-command demonstration and live Actions-secret verification runner."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .ai_providers import (
    DeterministicFallbackProvider,
    ProviderError,
    build_default_providers,
    describe_provider_readiness,
)
from .auditor import ProtocolAuditor, format_audit_markdown
from .benchmark import run_benchmark_suite
from .contracts import AIProvider, BillingTier, PromptRequest, ProviderKind, TelemetrySink
from .model_catalog import find_retired_model, retirement_reason
from .router import FreeFirstRouter, RouterConfig
from .security import redact_secrets
from .telemetry import ObservabilityRuntime

_DEFAULT_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


class _FlakyPrimaryDemoProvider:
    """Demo provider simulating a 429 rate-limit to prove automatic failover."""

    kind = ProviderKind.GEMINI
    model = "gemini-2.5-flash"
    is_remote = True
    billing_tier = BillingTier.FREE_TIER

    def is_configured(self) -> bool:
        return True

    async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
        async with asyncio.timeout(timeout_s):
            await asyncio.sleep(0.002)
        raise ProviderError(
            "http_429", "Simulated free-tier rate limit", retryable=True, status_code=429
        )


class _HealthyBackupDemoProvider:
    """Demo backup provider proving $0 fallback after primary retries exhaust."""

    kind = ProviderKind.GROQ
    model = "llama-3.3-70b-versatile"
    is_remote = True
    billing_tier = BillingTier.FREE_TIER

    def is_configured(self) -> bool:
        return True

    async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
        async with asyncio.timeout(timeout_s):
            await asyncio.sleep(0.002)
        return f"Free-First backup succeeded for: {request.prompt[:60]}"


def _discover_audit_targets(root: Path) -> list[str]:
    candidates = [
        rel
        for rel in (
            "python-2027",
            "connectors-hub",
            "projects/agi-system",
            "projects/1pro",
            "projects/open_source-",
        )
        if (root / rel).is_dir()
    ]
    return candidates or ["."]


async def run_one_command_demo(
    workspace_root: Path | None = None,
    *,
    use_live_keys: bool = False,
) -> dict[str, Any]:
    """Run the full 10-rule demonstration offline or via configured env keys."""
    root = (workspace_root or _DEFAULT_WORKSPACE_ROOT).resolve()
    runtime = ObservabilityRuntime(json_logs=True)

    live_providers = build_default_providers()
    readiness = [s.model_dump(mode="json") for s in describe_provider_readiness(live_providers)]
    has_live_remote = any(
        p.is_configured() and p.kind != ProviderKind.DETERMINISTIC for p in live_providers
    )

    if use_live_keys and has_live_remote:
        demo_router = FreeFirstRouter(
            providers=live_providers,
            config=RouterConfig(
                per_provider_timeout_s=15.0,
                max_retries_per_provider=1,
                base_backoff_s=0.1,
            ),
            telemetry=runtime,
        )
    else:
        demo_router = FreeFirstRouter(
            providers=[
                _FlakyPrimaryDemoProvider(),
                _HealthyBackupDemoProvider(),
                DeterministicFallbackProvider(),
            ],
            config=RouterConfig(
                per_provider_timeout_s=2.0,
                max_retries_per_provider=1,
                base_backoff_s=0.005,
            ),
            telemetry=runtime,
        )

    single_response = await demo_router.route(
        PromptRequest(prompt="Summarize AI-2027 evidence-first architecture at $0.")
    )

    # 2. asyncio.TaskGroup Concurrent Batch (offline safe in demo even when live keys exist)
    batch_router = (
        demo_router
        if not (use_live_keys and has_live_remote)
        else FreeFirstRouter(
            providers=[_HealthyBackupDemoProvider(), DeterministicFallbackProvider()],
            telemetry=runtime,
        )
    )
    batch_requests = [
        PromptRequest(prompt=f"Concurrent TaskGroup prompt #{idx}") for idx in range(1, 5)
    ]
    batch_responses = await batch_router.route_batch(batch_requests, max_concurrency=4)

    # 3. Measured Benchmark (Baseline vs Optimized)
    bench_report = await run_benchmark_suite(iterations=10, telemetry=runtime)

    # 4. Repo Protocol Compliance Audit (python-2027 + existing packages if present)
    auditor = ProtocolAuditor(workspace_root=root, telemetry=runtime)
    candidates = await asyncio.to_thread(_discover_audit_targets, root)
    audit_report = await auditor.audit_projects(candidates)

    spans = runtime.exported_spans()
    logs = runtime.log_text()
    runtime.shutdown()

    return {
        "mode": "live_env_keys" if (use_live_keys and has_live_remote) else "offline_demo",
        "provider_readiness": readiness,
        "budget": demo_router.budget_snapshot().model_dump(mode="json"),
        "single_route": single_response.model_dump(mode="json"),
        "batch_count": len(batch_responses),
        "benchmark": bench_report.model_dump(mode="json"),
        "audit": audit_report.model_dump(mode="json"),
        "audit_markdown": format_audit_markdown(audit_report),
        "otel_spans_captured": len(spans),
        "structlog_lines": len([line for line in logs.splitlines() if line.strip()]),
    }


async def verify_live_providers(
    *,
    providers: Sequence[AIProvider] | None = None,
    target: str = "all",
    prompt: str = "Reply with exactly the word: pong",
    timeout_s: float = 15.0,
    telemetry: TelemetrySink | None = None,
) -> dict[str, Any]:
    """Probe configured Actions/env providers safely without ever printing secret material."""
    runtime = telemetry or ObservabilityRuntime(json_logs=True)
    chain = tuple(providers if providers is not None else build_default_providers())
    statuses = {s.provider.value: s for s in describe_provider_readiness(chain)}

    non_fallback = [p for p in chain if p.kind != ProviderKind.DETERMINISTIC]
    if target not in {"all", "auto"}:
        non_fallback = [p for p in non_fallback if p.kind.value == target]

    req = PromptRequest(prompt=prompt, temperature=0.0, max_tokens=24, allow_remote=True)
    probe_results: list[dict[str, Any] | None] = [None] * len(non_fallback)

    async def _probe_one(idx: int, provider: AIProvider) -> None:
        st = statuses.get(provider.kind.value)
        cred_state = (
            st.credential_state if st else ("present" if provider.is_configured() else "absent")
        )
        if not provider.is_configured():
            probe_results[idx] = {
                "provider": provider.kind.value,
                "model": provider.model,
                "credential_state": cred_state,
                "configured": False,
                "ok": False,
                "status": "skipped_no_key",
                "latency_ms": 0.0,
            }
            return

        retired = find_retired_model(provider.kind, provider.model)
        if retired is not None:
            probe_results[idx] = {
                "provider": provider.kind.value,
                "model": provider.model,
                "credential_state": cred_state,
                "configured": True,
                "ok": False,
                "status": "model_retired",
                "latency_ms": 0.0,
                "error": retirement_reason(retired),
            }
            return

        if st is not None and not st.allowed_under_zero_budget:
            probe_results[idx] = {
                "provider": provider.kind.value,
                "model": provider.model,
                "credential_state": cred_state,
                "configured": True,
                "ok": False,
                "status": "budget_blocked",
                "latency_ms": 0.0,
                "error": st.budget_reason,
            }
            return

        start_ns = time.perf_counter_ns()
        with runtime.start_span(
            "live_provider.probe",
            {"provider": provider.kind.value, "model": provider.model},
        ):
            try:
                text = await provider.complete(req, timeout_s=timeout_s)
                latency_ms = round((time.perf_counter_ns() - start_ns) / 1e6, 2)
                probe_results[idx] = {
                    "provider": provider.kind.value,
                    "model": provider.model,
                    "credential_state": cred_state,
                    "configured": True,
                    "ok": True,
                    "status": "ok",
                    "latency_ms": latency_ms,
                    "response_chars": len(text),
                    "response_preview": redact_secrets(text.strip()[:80]),
                }
            except ProviderError as exc:
                latency_ms = round((time.perf_counter_ns() - start_ns) / 1e6, 2)
                probe_results[idx] = {
                    "provider": provider.kind.value,
                    "model": provider.model,
                    "credential_state": cred_state,
                    "configured": True,
                    "ok": False,
                    "status": exc.code,
                    "latency_ms": latency_ms,
                    "error": redact_secrets(str(exc)),
                }

    with runtime.start_span("live_providers.verify", {"target": target}):
        trace_id = runtime.current_trace_id()
        if target != "auto":
            async with asyncio.TaskGroup() as group:
                for idx, item in enumerate(non_fallback):
                    group.create_task(_probe_one(idx, item))

    router = FreeFirstRouter(
        providers=chain,
        config=RouterConfig(
            per_provider_timeout_s=timeout_s,
            max_retries_per_provider=1,
            base_backoff_s=0.1,
        ),
        telemetry=runtime,
    )
    preferred = (
        (ProviderKind(target), ProviderKind.DETERMINISTIC)
        if target not in {"all", "auto"}
        else None
    )
    routed = await router.route(
        PromptRequest(
            prompt=prompt,
            temperature=0.0,
            max_tokens=24,
            allow_remote=True,
            preferred_providers=preferred,
        )
    )

    completed_probes = [r for r in probe_results if r is not None]
    configured_count = sum(1 for r in completed_probes if r["configured"])
    if target == "auto":
        configured_count = sum(1 for p in non_fallback if p.is_configured())
    live_succeeded = (
        not routed.simulated if target == "auto" else any(bool(r["ok"]) for r in completed_probes)
    )

    runtime.log(
        "info",
        "live_providers_verified",
        target=target,
        configured_count=configured_count,
        live_succeeded=live_succeeded,
        routed_provider=routed.provider.value,
        simulated=routed.simulated,
    )
    return {
        "target": target,
        "trace_id": trace_id,
        "configured_count": configured_count,
        "live_succeeded": live_succeeded,
        "probes": completed_probes,
        "budget": router.budget_snapshot().model_dump(mode="json"),
        "routed": {
            "provider": routed.provider.value,
            "model": routed.model,
            "simulated": routed.simulated,
            "cost_usd": routed.cost_usd,
            "billing_tier": routed.billing_tier.value,
            "cost_status": routed.cost_status.value,
            "cost_known": routed.cost_known,
            "latency_ms": routed.latency_ms,
            "text_preview": redact_secrets(routed.text[:120]),
        },
        "readiness": [s.model_dump(mode="json") for s in statuses.values()],
    }
