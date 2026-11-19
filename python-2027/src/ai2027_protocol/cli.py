"""Command-line interface for demo, auditing, benchmarking, live keys, and serving."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import uvicorn

from .agent import AgentResumeError, AgentTaskRequest, AutonomousAgent, SessionStore
from .ai_providers import describe_provider_readiness
from .auditor import ProtocolAuditor, format_audit_markdown
from .benchmark import run_benchmark_suite
from .bridges import ConnectorsBridge
from .celia_memory import CeliaMemoryStore, OllamaEmbeddingBackend
from .compliance import build_compliance_registry, format_compliance_markdown
from .contracts import PromptRequest, ProviderKind
from .demo import run_one_command_demo, verify_live_providers
from .model_catalog import (
    DEFAULT_MODELS,
    MODEL_ENV_VARS,
    OPENROUTER_FREE_ROUTER,
    RETIRED_MODELS,
)
from .ollama_layer import OllamaRuntimeClient
from .ollama_readiness import verify_ollama_capabilities
from .router import FreeFirstRouter
from .server import create_app
from .telemetry import ObservabilityRuntime


def _default_workspace_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "catalog.json").is_file() or (parent / "python-2027").is_dir():
            return parent
    return Path.cwd()


_ALLOWED_PROVIDERS = {p.value: p.value for p in ProviderKind}
_ALLOWED_TIERS = {
    "local": "local",
    "free_tier": "free_tier",
    "unknown_cost": "unknown_cost",
    "paid": "paid",
}
_ALLOWED_COST_STATUSES = {
    "proven_zero_local": "proven_zero_local",
    "declared_free_tier_unbilled": "declared_free_tier_unbilled",
    "unknown_cost_blocked": "unknown_cost_blocked",
    "paid_blocked": "paid_blocked",
    "allowed_within_budget": "allowed_within_budget",
}
_ALLOWED_MODES = {"offline_demo": "offline_demo", "live_env_keys": "live_env_keys"}
_PROVIDER_ENV_LABELS = {
    "ollama": "OLLAMA_BASE_URL",
    "gemini": "GEMINI_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "deterministic": "—",
}


async def _run_cli_async(args: argparse.Namespace) -> int:
    root: Path = args.root.resolve()
    if args.command == "demo":
        result = await run_one_command_demo(workspace_root=root, use_live_keys=args.live)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        single = result["single_route"]
        bench = result["benchmark"]
        mode_lbl = _ALLOWED_MODES.get(str(result.get("mode", "")), "offline_demo")
        prov_lbl = _ALLOWED_PROVIDERS.get(str(single.get("provider", "")), "deterministic")
        tier_lbl = _ALLOWED_TIERS.get(str(single.get("billing_tier", "")), "local")
        cost_lbl = _ALLOWED_COST_STATUSES.get(
            str(single.get("cost_status", "")), "proven_zero_local"
        )
        attempts_n = len(single.get("attempts", ()))
        latency_num = float(single.get("latency_ms", 0.0))
        cost_num = float(single.get("cost_usd") or 0.0)
        trace_hex = "".join(
            ch for ch in str(single.get("trace_id", "")) if ch in "0123456789abcdef"
        )
        print("=" * 72)
        print("AI-2027 — Python 2027 Software Excellence Protocol ($0 Workers + Colab)")
        print("=" * 72)
        print(
            f"[Free-First Route] mode={mode_lbl} provider={prov_lbl} "
            f"tier={tier_lbl} cost_status={cost_lbl} "
            f"attempts={attempts_n} latency={latency_num}ms "
            f"cost=${cost_num:.2f} trace={trace_hex}"
        )
        print(f"[TaskGroup Batch]  completed={result['batch_count']} concurrent tasks (0 blocking)")
        for comp in bench["comparisons"]:
            print(
                f"[Benchmark]        {comp['baseline']['name']} ({comp['baseline']['p50_ms']}ms) "
                f"-> {comp['optimized']['name']} ({comp['optimized']['p50_ms']}ms) "
                f"= {comp['speedup_x']}x speedup"
            )
        print(
            f"[Observability]    structlog_events={result['structlog_lines']} "
            f"otel_spans={result['otel_spans_captured']}"
        )
        print()
        print(result["audit_markdown"])
        return 0

    if args.command == "providers":
        statuses = describe_provider_readiness()
        if args.json:
            print(
                json.dumps(
                    [s.model_dump(mode="json") for s in statuses], indent=2, ensure_ascii=False
                )
            )
            return 0
        print(
            "Provider     | Configured | Tier         | $0 Guard | "
            "Key Env            | Credential State"
        )
        print("-" * 96)
        for st in statuses:
            prov_col = _ALLOWED_PROVIDERS.get(st.provider.value, "unknown")
            cfg_col = "True" if st.configured else "False"
            tier_col = _ALLOWED_TIERS.get(st.billing_tier.value, "unknown_cost")
            guard_col = "ALLOW" if st.allowed_under_zero_budget else "BLOCK"
            env_col = _PROVIDER_ENV_LABELS.get(prov_col, "—")
            state_col = (
                "always_ready"
                if prov_col == "deterministic"
                else ("configured" if st.configured else "absent")
            )
            print(
                f"{prov_col:<12} | {cfg_col:<10} | "
                f"{tier_col:<12} | {guard_col:<8} | {env_col:<18} | "
                f"{state_col}"
            )
        return 0

    if args.command == "models":
        catalog: dict[str, Any] = {
            "defaults": [
                {
                    "provider": kind.value,
                    "env_var": MODEL_ENV_VARS[kind],
                    "model": model,
                }
                for kind, model in DEFAULT_MODELS.items()
            ],
            "openrouter_free_router": OPENROUTER_FREE_ROUTER,
            "retired": [
                {
                    "provider": entry.kind.value,
                    "model": entry.model,
                    "retired_on": entry.retired_on,
                    "replacement": entry.replacement,
                    "evidence": entry.evidence,
                }
                for entry in RETIRED_MODELS
            ],
        }
        if args.json:
            print(json.dumps(catalog, indent=2, ensure_ascii=False))
            return 0
        print("Provider defaults (used only when the matching env var is empty):")
        print("-" * 92)
        print(f"{'Provider':<12} | {'Env var':<18} | Model")
        for row in catalog["defaults"]:
            print(f"{row['provider']:<12} | {row['env_var']:<18} | {row['model']}")
        print()
        print(
            "OpenRouter also accepts the dynamic free router "
            f"`{OPENROUTER_FREE_ROUTER}` (no pinned model to go stale)."
        )
        print()
        print("Retired IDs blocked before any request:")
        print("-" * 92)
        for entry in catalog["retired"]:
            print(
                f"{entry['provider']:<9} | {entry['model']:<42} | "
                f"retired {entry['retired_on']} -> {entry['replacement']}"
            )
        return 0

    if args.command == "registry":
        runtime = ObservabilityRuntime(json_logs=True)
        reg = await build_compliance_registry(root, telemetry=runtime)
        runtime.shutdown()
        if args.json:
            print(reg.model_dump_json(indent=2))
        else:
            print(format_compliance_markdown(reg))
        return 0

    if args.command == "live":
        runtime = ObservabilityRuntime(json_logs=False)
        report = await verify_live_providers(
            target=args.provider,
            prompt=args.prompt,
            telemetry=runtime,
        )
        runtime.shutdown()
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(f"[Live Verify] target={report['target']} trace={report['trace_id']}")
            for probe in report["probes"]:
                print(
                    f"  - {probe['provider']:<10} ({probe['credential_state']}) "
                    f"-> {probe['status']} ({probe['latency_ms']}ms)"
                )
            routed = report["routed"]
            print(
                f"[Free-First]  routed={routed['provider']} simulated={routed['simulated']} "
                f"latency={routed['latency_ms']}ms preview={routed['text_preview']!r}"
            )
        if args.require_any and not report["live_succeeded"]:
            return 1
        return 0

    if args.command == "audit":
        runtime = ObservabilityRuntime(json_logs=True)
        auditor = ProtocolAuditor(workspace_root=root, telemetry=runtime)
        audit_report = await auditor.audit_projects(args.paths)
        runtime.shutdown()
        if args.json:
            print(audit_report.model_dump_json(indent=2))
        else:
            print(format_audit_markdown(audit_report))
        return 0

    if args.command == "bench":
        runtime = ObservabilityRuntime(json_logs=True)
        bench_report = await run_benchmark_suite(iterations=args.iterations, telemetry=runtime)
        runtime.shutdown()
        if args.json:
            print(bench_report.model_dump_json(indent=2))
        else:
            for comp in bench_report.comparisons:
                print(
                    f"{comp.baseline.name}: p50={comp.baseline.p50_ms}ms | "
                    f"{comp.optimized.name}: p50={comp.optimized.p50_ms}ms | "
                    f"speedup={comp.speedup_x}x"
                )
            for stat in bench_report.standalone:
                print(
                    f"{stat.name}: p50={stat.p50_ms}ms p95={stat.p95_ms}ms ops/s={stat.ops_per_sec}"
                )
        return 0

    if args.command == "route":
        runtime = ObservabilityRuntime(json_logs=False)
        router = FreeFirstRouter(telemetry=runtime)
        preferred = (
            (ProviderKind(args.provider), ProviderKind.DETERMINISTIC) if args.provider else None
        )
        response = await router.route(
            PromptRequest(
                prompt=args.prompt,
                allow_remote=not args.offline_only,
                preferred_providers=preferred,
            )
        )
        runtime.shutdown()
        print(response.model_dump_json(indent=2))
        return 0

    if args.command == "agent":
        runtime = ObservabilityRuntime(json_logs=False)
        router = FreeFirstRouter(telemetry=runtime)
        studio_dir = (args.studio_dir or (root / ".ai2027_studio")).resolve()
        store = SessionStore(studio_dir)
        agent = AutonomousAgent(
            workspace_root=root,
            session_store=store,
            router=router,
            telemetry=runtime,
        )
        try:
            if args.resume:
                existing = await store.load(args.resume)
                session_id = existing.session_id
                task_text = args.task.strip() or existing.task
            else:
                if not args.task.strip():
                    print(
                        "agent: a task is required unless --resume names a session", file=sys.stderr
                    )
                    runtime.shutdown()
                    return 2
                session_id = store.create_session(title=args.task[:64]).session_id
                task_text = args.task
            req = AgentTaskRequest(
                task=task_text,
                max_steps=args.max_steps,
                preferred_provider=ProviderKind(args.provider) if args.provider else None,
                allow_remote=not args.offline_only,
                resume=bool(args.resume),
            )
            state = await agent.run(session_id, req)
        except (AgentResumeError, FileNotFoundError) as exc:
            print(f"agent: {exc}", file=sys.stderr)
            runtime.shutdown()
            return 2
        runtime.shutdown()
        if args.json:
            print(state.model_dump_json(indent=2))
            return 0
        cost_val = f"${state.cost_usd:.2f}" if state.cost_usd is not None else "unknown"
        print(
            f"[AI-2027 Agent] session={state.session_id} mode={state.mode} "
            f"provider={state.provider} tier={state.billing_tier.value} "
            f"cost={cost_val} trace={state.trace_id}"
        )
        for step_trace in state.steps:
            print(f"  Step {step_trace.step}: {step_trace.thought}")
            for out in step_trace.outcomes:
                print(f"    -> {out.tool} (ok={out.ok}): {out.summary}")
        if state.artifacts:
            print("Artifacts:")
            for art in state.artifacts:
                print(
                    f"  - {art.path} ({art.kind}, verified={art.verified}, "
                    f"sha256={(art.sha256 or '')[:14]})"
                )
        print()
        print(state.final_answer)
        return 0

    if args.command == "approvals":
        studio_dir = (args.studio_dir or (root / ".ai2027_studio")).resolve()
        bridge = ConnectorsBridge(store_path=studio_dir / "approvals.json")
        if args.action == "list":
            items = await bridge.list_approvals()
            payload = [i.model_dump(mode="json") for i in items]
            if args.json:
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                if not items:
                    print("No queued approvals.")
                for i in items:
                    print(f"{i.action_id} | {i.status:<8} | {i.connector:<14} | {i.summary}")
            return 0
        decided = await bridge.decide_approval(
            action_id=args.action_id,
            approve=args.action == "approve",
            note=args.note,
        )
        print(decided.model_dump_json(indent=2))
        return 0

    if args.command == "ollama-check":
        ollama_client = OllamaRuntimeClient(base_url=args.base_url)
        rep = await verify_ollama_capabilities(
            client=ollama_client,
            workspace_root=root,
            run_live_inference=args.live,
        )
        if args.json:
            print(rep.model_dump_json(indent=2))
        else:
            print(
                f"[Ollama Phase-0/Verify] status={rep.verification_status} "
                f"profile={rep.profile.name} chat_model={rep.host.recommended_chat_model} "
                f"ram_gb={rep.host.total_ram_gb} free_disk_gb={rep.host.free_disk_gb}"
            )
            for chk in rep.checks:
                print(
                    f"  - {chk.check_id}: ok={chk.passed} live={chk.live_endpoint} ({chk.evidence})"
                )
            print(f"Note: {rep.honest_note}")
        return 0 if rep.verification_status != "UNREACHABLE" else 1

    if args.command == "celia-memory":
        db_file = (args.db or CeliaMemoryStore.default_db_path(root)).resolve()
        ollama_client = OllamaRuntimeClient()
        backend = OllamaEmbeddingBackend(ollama_client) if ollama_client.is_configured() else None
        mem_store = CeliaMemoryStore(db_file, backend=backend, allow_lexical_fallback=True)
        if args.mem_cmd == "index":
            idx_res = await mem_store.index_paths(root, args.paths)
            print(idx_res.model_dump_json(indent=2))
            return 0
        if args.mem_cmd == "search":
            hits = await mem_store.search(args.query, k=args.k)
            print(
                json.dumps([h.model_dump(mode="json") for h in hits], indent=2, ensure_ascii=False)
            )
            return 0
        stats = await mem_store.stats()
        print(stats.model_dump_json(indent=2))
        return 0

    return 2  # pragma: no cover


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser for all subcommands."""
    parser = argparse.ArgumentParser(
        prog="ai2027-py27",
        description="Python 2027 Software Excellence Protocol CLI ($0 Free-First AI + Auditor).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=_default_workspace_root(),
        help="Workspace root directory for path-contained auditing.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    demo_p = sub.add_parser("demo", help="Run the 1-command protocol demonstration.")
    demo_p.add_argument("--json", action="store_true", help="Emit full JSON output.")
    demo_p.add_argument(
        "--live",
        action="store_true",
        help="Use configured Actions/environment API keys when available.",
    )

    prov_p = sub.add_parser(
        "providers", help="Show sanitized readiness for Actions/environment provider keys."
    )
    prov_p.add_argument("--json", action="store_true", help="Emit JSON output.")

    models_p = sub.add_parser(
        "models",
        help="List default, free-router, and retired remote model IDs (no network).",
    )
    models_p.add_argument("--json", action="store_true", help="Emit JSON output.")

    live_p = sub.add_parser(
        "live",
        help="Invoke configured Actions/environment providers safely without printing secrets.",
    )
    live_p.add_argument(
        "--provider",
        choices=("all", "auto", "ollama", "gemini", "nvidia", "groq", "openrouter"),
        default="all",
        help="Which configured provider(s) to verify.",
    )
    live_p.add_argument(
        "--prompt",
        default="Reply with exactly the word: pong",
        help="Tiny prompt sent to configured live providers.",
    )
    live_p.add_argument(
        "--require-any",
        action="store_true",
        help="Exit non-zero unless at least one real configured provider responds.",
    )
    live_p.add_argument("--json", action="store_true", help="Emit JSON output.")

    audit_p = sub.add_parser("audit", help="Audit projects against the 10 protocol rules.")
    audit_p.add_argument(
        "paths",
        nargs="*",
        default=["python-2027"],
        help="Relative workspace paths to audit.",
    )
    audit_p.add_argument("--json", action="store_true", help="Emit JSON report.")

    reg_p = sub.add_parser(
        "registry",
        help="Audit the 48 catalog projects and core subsystems using 3-tier standards.",
    )
    reg_p.add_argument("--json", action="store_true", help="Emit JSON registry report.")

    bench_p = sub.add_parser("bench", help="Run reproducible latency and throughput benchmarks.")
    bench_p.add_argument("--iterations", type=int, default=20, help="Measurement iterations.")
    bench_p.add_argument("--json", action="store_true", help="Emit JSON report.")

    route_p = sub.add_parser("route", help="Route one prompt across Free-First providers.")
    route_p.add_argument("prompt", help="Prompt text to process.")
    route_p.add_argument(
        "--provider",
        choices=("ollama", "gemini", "nvidia", "groq", "openrouter", "deterministic"),
        default=None,
        help="Prefer a specific configured provider before fallback.",
    )
    route_p.add_argument("--offline-only", action="store_true", help="Disable remote providers.")

    serve_p = sub.add_parser("serve", help="Start the async FastAPI server.")
    serve_p.add_argument("--host", default="0.0.0.0")  # noqa: S104
    serve_p.add_argument("--port", type=int, default=8790)

    studio_p = sub.add_parser("studio", help="Start the AI-2027 Agent Studio web UI & API.")
    studio_p.add_argument("--host", default="0.0.0.0")  # noqa: S104
    studio_p.add_argument("--port", type=int, default=8080)
    studio_p.add_argument("--studio-dir", type=Path, default=None)

    agent_p = sub.add_parser("agent", help="Run a multi-step autonomous task from the CLI.")
    agent_p.add_argument(
        "task",
        nargs="?",
        default="",
        help="Goal or instruction; optional when --resume names a checkpointed session.",
    )
    agent_p.add_argument("--max-steps", type=int, default=5)
    agent_p.add_argument(
        "--provider",
        choices=("ollama", "gemini", "nvidia", "groq", "openrouter", "deterministic"),
        default=None,
    )
    agent_p.add_argument("--offline-only", action="store_true")
    agent_p.add_argument("--studio-dir", type=Path, default=None)
    agent_p.add_argument(
        "--resume",
        metavar="SESSION_ID",
        default="",
        help="Continue an interrupted session from its last checkpointed step.",
    )
    agent_p.add_argument("--json", action="store_true")

    appr_p = sub.add_parser("approvals", help="Inspect or approve/reject queued L5 actions.")
    appr_p.add_argument("action", choices=("list", "approve", "reject"))
    appr_p.add_argument("action_id", nargs="?", default="")
    appr_p.add_argument("--note", default=None)
    appr_p.add_argument("--studio-dir", type=Path, default=None)
    appr_p.add_argument("--json", action="store_true")

    ol_p = sub.add_parser(
        "ollama-check",
        help="Run Phase-0 hardware check and independent Ollama readiness verification.",
    )
    ol_p.add_argument("--base-url", default=None, help="Optional loopback Ollama base URL.")
    ol_p.add_argument("--live", action="store_true", help="Run live structured & tool probes.")
    ol_p.add_argument("--json", action="store_true", help="Emit JSON report.")

    cm_p = sub.add_parser("celia-memory", help="Manage Celia Memory persistent SQLite RAG.")
    cm_p.add_argument("mem_cmd", choices=("index", "search", "stats"))
    cm_p.add_argument("--db", type=Path, default=None, help="SQLite DB file path.")
    cm_p.add_argument("--paths", nargs="+", default=["docs", "README.md"])
    cm_p.add_argument("--query", default="Ollama 4GB profile")
    cm_p.add_argument("--k", type=int, default=5)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Execute the Python 2027 CLI entrypoint and return an exit code."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command in {"serve", "studio"}:  # pragma: no cover - long-running server
        sdir = getattr(args, "studio_dir", None)
        app = create_app(workspace_root=args.root, studio_dir=sdir)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        return 0
    return asyncio.run(_run_cli_async(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
