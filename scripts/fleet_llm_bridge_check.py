#!/usr/bin/env python3
"""Prove fleet-2027 can drive a *real* python-2027 provider without importing it.

The architecture rule is "the core never imports a provider". This script is the
other half of that rule: it shows the *injection* works with the actual
``python-2027`` package on disk — real ``OllamaLocalProvider``, real
``PromptRequest`` (pydantic-validated), real async ``complete`` — while
``fleet2027.llm`` itself contains no pydantic/httpx import.

Two modes:

``offline`` (default, no network)
    Builds the real provider objects, asserts they are *not configured* in a
    clean environment, and confirms fleet-2027 refuses them with a clear reason
    instead of hanging or faking success.

``live``
    Requires ``OLLAMA_BASE_URL``. Sends one real prompt through the real
    provider, writes the artifact, and lets the independent verifier decide.

    python3 scripts/fleet_llm_bridge_check.py --mode live

Exit codes: 0 pass · 1 fail · 3 "offline: environment has no endpoint" (a
first-class outcome, never reported as a success).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "fleet-2027"))
sys.path.insert(0, str(ROOT / "python-2027" / "src"))

from fleet2027 import (  # noqa: E402
    AsyncProviderBridge,
    DurableTaskStore,
    EvidenceKind,
    FleetRunner,
    LLMAgent,
    PlanStep,
    RunnerConfig,
    TaskSpec,
    TaskStatus,
    build_llm_registry,
    utc_now,
)
from fleet2027.llm import LLMError, LLMRequest  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_NO_ENDPOINT = 0, 1, 3


def _build_real_bridge() -> tuple[object, str]:
    """Import the real provider package here — the only place it is imported."""
    from ai2027_protocol.ai_providers import OllamaLocalProvider
    from ai2027_protocol.contracts import PromptRequest

    provider = OllamaLocalProvider()

    def builder(request: LLMRequest) -> object:
        return PromptRequest(
            prompt=request.prompt,
            system_prompt=request.system or None,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )

    bridge = AsyncProviderBridge(
        provider=provider,
        prompt_builder=builder,
        name_hint="python-2027:ollama",
    )
    return bridge, provider.model  # type: ignore[attr-defined]


def main(argv: list[str] | None = None) -> int:
    """Run the bridge check in the requested mode."""
    parser = argparse.ArgumentParser(description="fleet-2027 ↔ python-2027 bridge check")
    parser.add_argument("--mode", choices=["offline", "live"], default="offline")
    parser.add_argument("--model-prompt", default="Write a Python function add(a, b).")
    args = parser.parse_args(argv)

    try:
        bridge, model = _build_real_bridge()
    except Exception as exc:  # noqa: BLE001 - a missing dependency is a clear failure
        print(f"FAIL: could not build the real provider: {type(exc).__name__}: {exc}")
        return EXIT_FAIL

    configured = bridge.is_configured()
    print(f"real provider model : {model}")
    print(f"provider configured : {configured}")

    if args.mode == "offline":
        if configured:
            print("NOTE: an endpoint is configured; `--mode live` would exercise it.")
            return EXIT_OK
        with tempfile.TemporaryDirectory() as tmp:
            agent = LLMAgent(backend=bridge)  # type: ignore[arg-type]
            registry, _ = build_llm_registry(agent)
            spec = TaskSpec(
                task_id=f"bridge-offline-{utc_now()[:19]}",
                goal="an unconfigured provider must fail, not fake success",
                steps=(
                    PlanStep(
                        step_id="write",
                        agent=agent.spec().name,
                        action="generate",
                        inputs={"prompt": "x", "out": "x.py", "language": "python"},
                        verification=(EvidenceKind.ARTIFACT_SHA256.value,),
                    ),
                ),
            )
            with DurableTaskStore(str(Path(tmp) / "fleet.db")) as store:
                report = FleetRunner(
                    store, registry, workspace_root=Path(tmp),
                    config=RunnerConfig(backoff_base_s=0.01),
                ).submit_and_run(spec)
                step = store.get_task(spec.task_id).steps[0]
        refused = report.status is not TaskStatus.SUCCEEDED
        print(f"outcome             : {report.status.value}")
        print(f"reason              : {(step.error or '')[:160]}")
        print(f"artifact written    : {(Path(tmp) / 'x.py').exists()}")
        print("PASS: unconfigured provider refused, verification untouched" if refused
              else "FAIL: an unconfigured provider was reported as success")
        return EXIT_OK if refused else EXIT_FAIL

    # -- live ------------------------------------------------------------- #
    if not configured:
        print("SKIPPED: OLLAMA_BASE_URL is not set — no endpoint to call.")
        print("This is an environment gap, not a pass. Export OLLAMA_BASE_URL to run it.")
        return EXIT_NO_ENDPOINT

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        agent = LLMAgent(backend=bridge, agent_name="bridge-live")  # type: ignore[arg-type]
        registry, _ = build_llm_registry(agent)
        spec = TaskSpec(
            task_id="bridge-live",
            goal="a real provider completion becomes a verified artifact",
            steps=(
                PlanStep(
                    step_id="write",
                    agent="bridge-live",
                    action="generate",
                    inputs={
                        "prompt": args.model_prompt
                        + " Return only the code inside a fenced python block.",
                        "out": "live.py",
                        "language": "python",
                    },
                    verification=(
                        EvidenceKind.ARTIFACT_SHA256.value,
                        EvidenceKind.OUTPUT_NON_EMPTY.value,
                    ),
                ),
            ),
        )
        try:
            with DurableTaskStore(str(workspace / "fleet.db")) as store:
                report = FleetRunner(
                    store, registry, workspace_root=workspace,
                    config=RunnerConfig(backoff_base_s=0.05),
                ).submit_and_run(spec)
                record = store.step_result("bridge-live", "write") or {}
        except LLMError as exc:  # pragma: no cover - live only
            print(f"FAIL: provider error: {exc}")
            return EXIT_FAIL
        evidence = record.get("evidence") or {}
        verified = bool((record.get("verification") or {}).get("ok"))
        payload = {
            "generated_at": utc_now(),
            "model": evidence.get("model"),
            "latency_ms": evidence.get("latency_ms"),
            "output_bytes": evidence.get("output_bytes"),
            "output_sha256": evidence.get("output_sha256"),
            "verification_ok": verified,
            "status": report.status.value,
        }
        print(json.dumps(payload, indent=2))
        out = ROOT / "launch" / "evidence" / "fleet-llm-bridge-live.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"evidence written to {out}")
        ok = verified and report.status is TaskStatus.SUCCEEDED
        return EXIT_OK if ok else EXIT_FAIL


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
