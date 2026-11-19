"""Command-line interface for demo, auditing, benchmarking, live keys, and serving."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from .ai_providers import describe_provider_readiness
from .auditor import ProtocolAuditor, format_audit_markdown
from .benchmark import run_benchmark_suite
from .contracts import PromptRequest, ProviderKind
from .demo import run_one_command_demo, verify_live_providers
from .router import FreeFirstRouter
from .server import create_app
from .telemetry import ObservabilityRuntime


def _default_workspace_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "catalog.json").is_file() or (parent / "python-2027").is_dir():
            return parent
    return Path.cwd()


async def _run_cli_async(args: argparse.Namespace) -> int:
    root: Path = args.root.resolve()
    if args.command == "demo":
        result = await run_one_command_demo(workspace_root=root, use_live_keys=args.live)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        single = result["single_route"]
        bench = result["benchmark"]
        print("=" * 72)
        print("AI-2027 — Python 2027 Software Excellence Protocol ($0 Workers + Colab)")
        print("=" * 72)
        print(
            f"[Free-First Route] mode={result['mode']} provider={single['provider']} "
            f"attempts={len(single['attempts'])} latency={single['latency_ms']}ms "
            f"cost=${single['cost_usd']:.2f} trace={single['trace_id']}"
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
        statuses = [s.model_dump(mode="json") for s in describe_provider_readiness()]
        if args.json:
            print(json.dumps(statuses, indent=2, ensure_ascii=False))
            return 0
        print("Provider     | Configured | Env Var            | Credential State")
        print("-" * 72)
        for st in statuses:
            print(
                f"{st['provider']:<12} | {str(st['configured']):<10} | "
                f"{st['env_var']:<18} | {st['credential_state']}"
            )
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

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Execute the Python 2027 CLI entrypoint and return an exit code."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "serve":  # pragma: no cover - long-running server
        app = create_app(workspace_root=args.root)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        return 0
    return asyncio.run(_run_cli_async(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
