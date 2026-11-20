#!/usr/bin/env python3
"""Run the quality benchmark against a real model and compare it to the baseline.

One command, three outcomes, no ambiguity:

    cp report → real model is BETTER than the scaffold     (exit 0)
    model is WORSE than the scaffold                       (exit 1)
    model is inside the noise band                         (exit 0, says so)

It refuses to run when it cannot produce a trustworthy number — a missing
endpoint, a missing key, an unreachable server — instead of measuring the
scaffold twice and calling the result a model comparison. That refusal is the
point: the most dangerous outcome here is a number that looks like a model
result and is not one.

The free-provider path exists so a run can happen in CI, where no local model
is available but a repository secret is. It refuses any model that is not
explicitly free, so an unattended run can never spend money.

Usage:
    python3 scripts/run_real_model.py --free openrouter
    python3 scripts/run_real_model.py --ollama http://127.0.0.1:11434
    python3 scripts/run_real_model.py --ollama http://127.0.0.1:11434 --model qwen3.5
    python3 scripts/run_real_model.py --openai https://api.example.com/v1 --model gpt-x

The OpenAI-compatible path reads its key from ``LLM_API_KEY`` in the environment
and never accepts it as an argument, because arguments end up in shell history.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "fleet-2027"))

BASELINE = ROOT / "fleet-2027" / "bench" / "baseline.json"

# A CI run must never be able to spend. `$0` is enforced here, in the runner
# that would place the call — not in a document that asks nicely.
FREE_PROVIDERS = {
    "openrouter": {
        "endpoint": "https://openrouter.ai/api/v1/chat/completions",
        "key_env": "OPENROUTER_API_KEY",
        "model_env": "OPENROUTER_MODEL",
        "default_model": "openrouter/free",
    },
}


def is_free_model(model: str) -> bool:
    """Return whether ``model`` is provably zero-cost under the repo policy."""
    name = (model or "").strip()
    return name == "openrouter/free" or name.endswith(":free")
OUTPUT = ROOT / "fleet-2027" / "bench" / "model.json"


def main(argv: list[str] | None = None) -> int:
    """Measure a real model and compare it against the recorded baseline."""
    parser = argparse.ArgumentParser(
        description="Benchmark a real model against the scaffold baseline."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ollama", metavar="URL", help="loopback Ollama base URL")
    source.add_argument("--openai", metavar="URL", help="OpenAI-compatible endpoint")
    source.add_argument(
        "--free",
        metavar="PROVIDER",
        choices=sorted(FREE_PROVIDERS),
        help="zero-cost provider from the allow-list (e.g. openrouter)",
    )
    parser.add_argument("--model", default=None, help="model id (provider default if omitted)")
    parser.add_argument(
        "--out", default=str(OUTPUT), help="where to write the report"
    )
    parser.add_argument(
        "--spend-ok",
        action="store_true",
        help="explicitly allow a model that is not provably free (never in CI)",
    )
    args = parser.parse_args(argv)

    from fleet2027.bench import run_benchmark, write_report
    from fleet2027.llm import (
        LLMError,
        LLMRequest,
        OllamaBackend,
        OpenAICompatibleBackend,
    )

    if args.ollama:
        backend = OllamaBackend(
            base_url=args.ollama,
            model=args.model or os.environ.get("OLLAMA_MODEL", "qwen3.5:0.8b"),
        )
    elif args.free:
        provider = FREE_PROVIDERS[args.free]
        model = args.model or os.environ.get(provider["model_env"]) or provider["default_model"]
        if not is_free_model(model) and not args.spend_ok:
            print(
                f"refusing to run: model {model!r} is not provably free. "
                "Use openrouter/free or a model ending in ':free', or pass "
                "--spend-ok if you have decided to pay.",
                file=sys.stderr,
            )
            return 2
        if not os.environ.get(provider["key_env"]):
            print(
                f"refusing to run: {provider['key_env']} is not set. "
                "Run this where the repository secret is available "
                "(GitHub Actions), not in a bare shell.",
                file=sys.stderr,
            )
            return 2
        backend = OpenAICompatibleBackend(
            endpoint_url=provider["endpoint"],
            model=model,
            api_key_env=provider["key_env"],
        )
        print(f"free-provider policy: model {model!r} is zero-cost")
    else:
        if not os.environ.get("LLM_API_KEY"):
            print(
                "refusing to run: LLM_API_KEY is not set. Passing a key on the "
                "command line would put it in your shell history; export it instead.",
                file=sys.stderr,
            )
            return 2
        backend = OpenAICompatibleBackend(
            endpoint_url=str(args.openai),
            model=args.model or os.environ.get("LLM_MODEL", "gpt-4o-mini"),
            api_key_env="LLM_API_KEY",
        )

    # Prove the connection before measuring. A run that fails halfway leaves a
    # report full of zeros that looks like a bad model rather than a bad setup.
    print(f"checking {backend.name} at {getattr(backend, 'base_url', '')}"
          f"{getattr(backend, 'endpoint_url', '')} ...")
    try:
        probe = backend.complete(
            LLMRequest(prompt="Reply with the single word: ready", temperature=0.0,
                       max_tokens=8, timeout_s=30.0)
        )
    except (LLMError, OSError) as exc:
        print(f"refusing to run: {backend.name} is not reachable: {exc}", file=sys.stderr)
        print(
            "hint: for Ollama, start it with 'ollama serve' and pull a model first "
            "(e.g. 'ollama pull qwen3.5:0.8b').",
            file=sys.stderr,
        )
        return 2
    print(f"connected · model={probe.model} · {probe.latency_ms:.0f} ms")

    report = run_benchmark(backend=backend)
    if not report.model_called:
        # The step evidence says no model ran. Reporting a score now would be a
        # scaffold result wearing a model's name.
        print(
            "refusing to report: no step recorded a model call, so this run "
            "measured the scaffold, not the model.",
            file=sys.stderr,
        )
        return 2

    path = write_report(report, args.out)
    print(
        f"measured {len(report.results)} sentences · mean={report.mean_score:.4f} "
        f"usable={report.usable_rate:.0%} complete={report.completion_rate:.0%}"
    )
    # A caller may write the report anywhere; `relative_to` raises for a path
    # outside the repository, which turned a successful measurement into a
    # traceback after the work was already done.
    try:
        shown = path.relative_to(ROOT)
    except ValueError:
        shown = path
    print(f"report: {shown}")

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    before = baseline["summary"]["mean_score"]
    delta = round(report.mean_score - before, 4)
    print()
    print(f"baseline (scaffold) {before:.4f} -> model {report.mean_score:.4f} ({delta:+.4f})")
    if delta > 0.02:
        print("RESULT: the model beats the scaffold.")
        return 0
    if delta < -0.02:
        print("RESULT: the model is WORSE than the scaffold — investigate before shipping.")
        return 1
    print("RESULT: indistinguishable from the scaffold; do not claim an improvement.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
