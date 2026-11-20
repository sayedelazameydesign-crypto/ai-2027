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
    python3 scripts/run_real_model.py --free gemini      # GEMINI_API_KEY
    python3 scripts/run_real_model.py --free nvidia      # NVIDIA_API_KEY
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
        # `free_models` is the proof used by `is_free_model`: these three
        # providers declare their model free, and there is no paid variant of
        # the id. OpenRouter instead marks free ids with a `:free` suffix.
        "free_models": (),
    },
    "gemini": {
        # Google's official OpenAI-compatibility shim, same key as the native
        # API. Free tier, declared in docs/MODEL-CATALOG.md.
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "key_env": "GEMINI_API_KEY",
        "model_env": "GEMINI_MODEL",
        "default_model": "gemini-2.5-flash",
        "free_models": ("gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"),
    },
    "groq": {
        # 30 requests/min, 1,000/day, 200,000 tokens/day on the free plan
        # (checked 2026-11-19). That is 20x OpenRouter's 50/day, which makes it
        # the tap that can carry a full 24-sentence run on its own.
        "endpoint": "https://api.groq.com/openai/v1/chat/completions",
        "key_env": "GROQ_API_KEY",
        "model_env": "GROQ_MODEL",
        "default_model": "openai/gpt-oss-120b",
        "free_models": ("openai/gpt-oss-120b", "openai/gpt-oss-20b"),
    },
    "nvidia": {
        # NIM is OpenAI-compatible at /v1. Free tier, declared in
        # docs/MODEL-CATALOG.md.
        "endpoint": "https://integrate.api.nvidia.com/v1/chat/completions",
        "key_env": "NVIDIA_API_KEY",
        "model_env": "NVIDIA_MODEL",
        # 3.3-70b was retired by NVIDIA on 2026-08-26 (HTTP 410 Gone, seen by
        # this repo's own smoke step). glm-5.3-flash is on NVIDIA's published
        # free-endpoint list; a probe still has to confirm it answers here.
        "default_model": "z-ai/glm-5.3-flash",
        "free_models": ("z-ai/glm-5.3-flash",),
    },
}


def is_free_model(model: str, provider: str = "openrouter") -> bool:
    """Return whether ``model`` is provably zero-cost under the repo policy.

    Two proofs are accepted: an OpenRouter id that is the free router or ends
    in ``:free``, and a provider whose declared free list contains the id —
    those ids have no paid variant, so there is no bill to run up.
    """
    name = (model or "").strip()
    spec = FREE_PROVIDERS.get(provider, {})
    if name in spec.get("free_models", ()):
        return True
    if provider != "openrouter":
        return False
    return name == "openrouter/free" or name.endswith(":free")
OUTPUT = ROOT / "fleet-2027" / "bench" / "model.json"


def _write_report(report, out: str):
    """Import lazily: fleet2027 lives in a sibling directory, not on sys.path."""
    from fleet2027.bench import write_report

    return write_report(report, out)


#: Enough for a provider error code and its message, without a wall of text.
_CAUSE_WIDTH = 160


def _short_cause(text: str) -> str:
    """Keep a failure cause readable: the prefix is the part that names it.

    Truncating from the left was tried and is wrong — `no artifact at
    document.md; step said: ...` is the useful shape, so the cause lives at
    the end and the whole line is kept up to a width that fits a report.
    """
    flat = " ".join(str(text).split())
    if len(flat) <= _CAUSE_WIDTH:
        return flat
    return flat[: _CAUSE_WIDTH - 1] + "…"


def _report(report, out: str) -> int:
    """Write the report and state the verdict. One place, three outcomes."""
    if not report.model_called:
        # The step evidence says no model ran. Reporting a score now would be
        # a scaffold result wearing a model's name.
        print(
            "refusing to report: no step recorded a model call, so this run "
            "measured the scaffold, not the model.",
            file=sys.stderr,
        )
        return 2

    path = _write_report(report, out)
    # Name the backend and model in the same line as the score: a number
    # without its source cannot be checked or reproduced later.
    print(
        f"measured {len(report.results)} sentences · backend={report.backend} "
        f"model_called={report.model_called} · mean={report.mean_score:.4f} "
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

    # Failures are the most useful part of a bad run, so they are summarised
    # by cause instead of being left inside the JSON report. A sentence can be
    # incomplete without raising: `status` may be unfinished, or the artifact
    # may be missing, or the quality gate may have rejected it. All three are
    # causes, and reading only `.error` printed an empty summary three runs in
    # a row.
    incomplete = [r for r in report.results if r.status != "succeeded" or not r.usable]
    if incomplete:
        print()
        print(f"failures: {len(incomplete)} of {len(report.results)} sentences")
        causes: dict[str, int] = {}
        for item in incomplete:
            # A fixed cut at 70 characters landed mid-word in the part that
            # names the cause: the line read "groq:... failed: ht" and the
            # whole run became undiagnosable. The provider's reason is the
            # most useful text in a failed benchmark, so it is kept whole.
            if item.error:
                cause = _short_cause(item.error)
            elif item.status != "succeeded":
                cause = f"status={item.status}"
            else:
                got = ", ".join(item.failures[:2]) if item.failures else "scored below floor"
                cause = _short_cause(f"unusable: {got}")
            causes[cause] = causes.get(cause, 0) + 1
        for cause, count in sorted(causes.items(), key=lambda kv: -kv[1])[:5]:
            print(f"  {count:02d}x {cause}")
        # When everything fails, a tally is not enough: quote one row whole so
        # the next run can be diagnosed instead of guessed at.
        if len(incomplete) == len(report.results):
            first = incomplete[0]
            print(f"  first failure: {first.sentence_id} status={first.status} "
                  f"model_called={first.model_called} error={first.error!r}")
            print(f"  its criteria: {list(first.failures)[:6]}")

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


def main(argv: list[str] | None = None) -> int:
    """Measure a real model and compare it against the recorded baseline."""
    parser = argparse.ArgumentParser(
        description="Benchmark a real model against the scaffold baseline."
    )
    source = parser.add_mutually_exclusive_group()
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
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="measure only the first N sentences (free quotas may not cover all 24)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        metavar="N",
        help="measure N sentences at once (waits on the network, spends no extra "
        "model calls; 4 is safe on free tiers)",
    )
    parser.add_argument(
        "--taps",
        default="",
        help="comma-separated free providers to rotate over, in order (default: all)",
    )
    parser.add_argument(
        "--rotate",
        action="store_true",
        help="rotate every free provider with a key, one per sentence "
        "(spreads the corpus across independent quotas)",
    )
    parser.add_argument(
        "--spend-ok",
        action="store_true",
        help="explicitly allow a model that is not provably free (never in CI)",
    )
    args = parser.parse_args(argv)
    sources = [bool(args.ollama), bool(args.openai), bool(args.free), bool(args.rotate)]
    if sum(sources) != 1:
        parser.error("choose exactly one of --ollama, --openai, --free, --rotate")

    from fleet2027.bench import run_benchmark, write_report  # noqa: F401 - used in _report
    from fleet2027.llm import (
        LLMError,
        LLMRequest,
        OllamaBackend,
        OpenAICompatibleBackend,
    )

    if args.rotate:
        # Each free provider has its own daily cap; the full corpus costs more
        # than any single one allows. Rotation keeps every sentence measured by
        # a real model instead of truncating the corpus.
        backends = []
        # `--taps a,b` limits and orders the rotation. The workflow fills it
        # from the smoke step, so a tap that cannot answer is never handed a
        # sentence — its dependants would fail with "blocked by a failed
        # dependency" and the run would blame the model for a dead provider.
        wanted = [t.strip() for t in (args.taps or "").split(",") if t.strip()]
        unknown = [t for t in wanted if t not in FREE_PROVIDERS]
        if unknown:
            parser.error(f"unknown tap(s) {unknown}; choose from {sorted(FREE_PROVIDERS)}")
        # Iterate in the caller's order, not the dict's: the whole point of
        # --taps is to put a tap that just answered at the front. Iterating
        # FREE_PROVIDERS.items() silently ignored that order, which my own
        # check caught only because I printed `order=`.
        order = wanted or list(FREE_PROVIDERS)
        for name in order:
            spec = FREE_PROVIDERS[name]
            if wanted and name not in wanted:
                print(f"rotation: {name} skipped (not in --taps)")
                continue
            key = os.environ.get(spec["key_env"], "").strip()
            if not key:
                print(f"rotation: {name} skipped (no {spec['key_env']})")
                continue
            model = os.environ.get(spec["model_env"]) or spec["default_model"]
            if not is_free_model(model, name) and not args.spend_ok:
                print(f"rotation: {name} skipped ({model!r} is not free)")
                continue
            backends.append(
                OpenAICompatibleBackend(
                    endpoint_url=spec["endpoint"],
                    model=model,
                    api_key_env=spec["key_env"],
                    label=f"{name}:{model}",
                )
            )
            print(f"rotation: {name} -> {model}")
        # Order matters. A tap with an exhausted quota does not just fail its
        # own sentence: its dependants die with "blocked by a failed
        # dependency", so one dead tap at the front of the list costs far more
        # than one sentence. The taps are ordered by the last thing we actually
        # observed — a provider that answered a probe goes first. The rotation
        # also drops a tap the moment it fails, so this only decides the start.
        print(f"rotation: order={[b.name for b in backends]}")
        if not backends:
            print(
                "refusing to run: no free provider has a usable credential.",
                file=sys.stderr,
            )
            return 2
        if args.model:
            print("note: --model is ignored with --rotate; each provider uses its default")
        report = run_benchmark(
        backend=backends[0], backends=backends, limit=args.limit, workers=args.workers
    )
        return _report(report, args.out)

    if args.ollama:
        backend = OllamaBackend(
            base_url=args.ollama,
            model=args.model or os.environ.get("OLLAMA_MODEL", "qwen3.5:0.8b"),
        )
    elif args.free:
        provider = FREE_PROVIDERS[args.free]
        model = args.model or os.environ.get(provider["model_env"]) or provider["default_model"]
        if not is_free_model(model, args.free) and not args.spend_ok:
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
            # 8 tokens was too few for a reasoning model: it spent the whole
            # allowance before writing `content`, returning finish_reason
            # 'length'. The probe asks for one word, not one token.
            LLMRequest(prompt="Reply with the single word: ready", temperature=0.0,
                       max_tokens=512, timeout_s=60.0)
        )
    except (LLMError, OSError) as exc:
        # "not reachable" was printed for every failure, including a reply
        # that arrived and could not be parsed. The two need different fixes,
        # so they get different sentences.
        text = str(exc)
        if "truncated" in text:
            kind = "replied but ran out of tokens before answering"
        elif "invalid_payload" in text:
            kind = "replied but the payload was unusable"
        elif "http_429" in text:
            # A quota is not an outage: the endpoint is healthy and the
            # credential is valid, there is simply nothing left to spend today.
            kind = "is reachable but the free quota is exhausted"
        elif "http_401" in text or "http_403" in text:
            kind = "rejected the credential"
        elif "http_" in text:
            kind = "replied with an error status"
        else:
            kind = "is not reachable"
        print(f"refusing to run: {backend.name} {kind}: {exc}", file=sys.stderr)
        if kind == "is not reachable":
            print(
                "hint: for Ollama, start it with 'ollama serve' and pull a model first "
                "(e.g. 'ollama pull qwen3.5:0.8b').",
                file=sys.stderr,
            )
        return 2
    print(f"connected · model={probe.model} · {probe.latency_ms:.0f} ms")

    report = run_benchmark(backend=backend, limit=args.limit, workers=args.workers)
    if not report.model_called:
        # The step evidence says no model ran. Reporting a score now would be a
        # scaffold result wearing a model's name.
        print(
            "refusing to report: no step recorded a model call, so this run "
            "measured the scaffold, not the model.",
            file=sys.stderr,
        )
        return 2

    return _report(report, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
