#!/usr/bin/env python3
"""Adapter: ``python-2027``'s ``FreeFirstRouter`` → a fleet ``LLMBackend``.

This is the only file in the A2 path that touches pydantic or the protocol
package. It lives *outside* ``fleet2027/`` on purpose, so the package keeps its
stdlib-only guarantee (``scripts/fleet_guard.py`` asserts that) while the repo
as a whole can still exercise the real router.

Two modes, both real:

``--mode simulated``
    Build a ``FreeFirstRouter`` whose first provider always fails and whose
    second one answers. Proves the *failover path* end to end: fleet step →
    router → provider A fails → provider B answers → verified file + persisted
    ``step.failover`` event. No network, no credentials, nothing simulated about
    the coordination — only the providers themselves are stubs.

``--mode live``
    Build the real ``FreeFirstRouter`` from ``build_default_providers()`` and run
    one step. If no provider is configured this exits **3** (not 0, not 1):
    "could not be attempted" is a first-class non-pass, exactly like A1's bridge.

``--mode exhausted``
    Every provider fails. The step must dead-letter (or retry) with the full
    trail — never crash the runner and never report success.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "fleet-2027"))

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_NOT_ATTEMPTED = 3


def _protocol_imports():
    """Import the protocol package lazily so ``--help`` works without deps."""
    try:
        from ai2027_protocol import (  # noqa: PLC0415
            FreeFirstRouter,
            PromptRequest,
            ProviderKind,
            RouterConfig,
            build_default_providers,
        )
    except ImportError as exc:  # pragma: no cover - environment guard
        print(
            f"error: python-2027 is not importable ({exc}).\n"
            "install it with: python3 -m pip install -e python-2027",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_USAGE) from exc
    return {
        "FreeFirstRouter": FreeFirstRouter,
        "ProviderKind": ProviderKind,
        "PromptRequest": PromptRequest,
        "RouterConfig": RouterConfig,
        "build_default_providers": build_default_providers,
    }


# --------------------------------------------------------------------------- #
# Stub providers that fail or answer *on purpose*, with real router semantics
# --------------------------------------------------------------------------- #
class _StubProvider:
    """Minimal ``AIProvider`` with controllable failure behaviour."""

    is_remote = False
    billing_tier: Any = None

    def __init__(self, *, kind: Any, model: str, answer: str | None = None,
                 error_code: str | None = None, tier: Any = None) -> None:
        self.kind = kind
        self._model = model
        self._answer = answer
        self._error_code = error_code
        if tier is not None:
            self.billing_tier = tier
        self.calls = 0

    @property
    def model(self) -> str:
        """Return the stub's model tag."""
        return self._model

    def is_configured(self) -> bool:
        """Stubs are always configured: the test is about routing, not credentials."""
        return True

    async def complete(self, request: Any, *, timeout_s: float) -> str:
        """Either raise the configured provider error or return the answer."""
        self.calls += 1
        if self._error_code is not None:
            from ai2027_protocol import ProviderError  # noqa: PLC0415

            raise ProviderError(self._error_code, f"stub {self._model} failed on purpose")
        if self._answer is None:  # pragma: no cover - stub misconfiguration
            raise RuntimeError(f"stub {self._model} has neither an error nor an answer")
        return self._answer


# --------------------------------------------------------------------------- #
# Backend wiring
# --------------------------------------------------------------------------- #
def build_backend(mode: str, *, answer: str) -> tuple[Any, dict[str, Any]]:
    """Return ``(backend, meta)`` for one of the three modes."""
    api = _protocol_imports()
    from fleet2027.routing import RouterBackend  # noqa: PLC0415

    kind = api["ProviderKind"]
    config = api["RouterConfig"]
    router_class = api["FreeFirstRouter"]
    request_class = api["PromptRequest"]

    def request_factory(request):
        return request_class(
            prompt=request.prompt,
            system_prompt=request.system or None,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            allow_remote=True,
        )

    if mode == "live":
        providers = api["build_default_providers"]()
        router = router_class(providers=providers, config=config())
        return (
            RouterBackend(router=router, request_factory=request_factory),
            {
                "mode": mode,
                "providers": [
                    {
                        "kind": getattr(p.kind, "value", str(p.kind)),
                        "model": p.model,
                        "configured": p.is_configured(),
                        "remote": p.is_remote,
                    }
                    for p in providers
                ],
            },
        )

    if mode == "simulated":
        primary = _StubProvider(
            kind=kind.OLLAMA, model="stub-dead-ollama", error_code="upstream_error"
        )
        secondary = _StubProvider(
            kind=kind.DETERMINISTIC, model="stub-live-answer", answer=answer
        )
        router = router_class(
            providers=[primary, secondary],
            config=config(max_retries_per_provider=0, base_backoff_s=0.0),
        )
        return (
            RouterBackend(router=router, request_factory=request_factory),
            {
                "mode": mode,
                "providers": [
                    {"kind": "ollama", "model": "stub-dead-ollama", "fails": True},
                    {"kind": "deterministic", "model": "stub-live-answer", "fails": False},
                ],
            },
        )

    if mode == "exhausted":
        dead = [
            _StubProvider(
                kind=kind.OLLAMA, model="dead-a", error_code="upstream_error"
            ),
            _StubProvider(
                kind=kind.GROQ, model="dead-b", error_code="rate_limited"
            ),
        ]
        router = router_class(
            providers=dead,
            config=config(max_retries_per_provider=0, base_backoff_s=0.0),
        )
        return (
            RouterBackend(router=router, request_factory=request_factory),
            {"mode": mode, "providers": [{"model": "dead-a"}, {"model": "dead-b"}]},
        )

    raise SystemExit(f"unknown mode {mode!r}")


def run_step(*, backend: Any, workspace: Path, task_id: str, out: str,
             prompt: str, language: str) -> dict[str, Any]:
    """Execute one routed step through the real durable engine, then verify it."""
    from fleet2027 import (  # noqa: PLC0415
        DurableTaskStore,
        EvidenceKind,
        FleetRunner,
        PlanStep,
        TaskSpec,
    )
    from fleet2027.llm import LLMAgent, build_llm_registry  # noqa: PLC0415

    agent = LLMAgent(backend=backend, agent_name="routed-writer")
    registry, _ = build_llm_registry(agent)
    spec = TaskSpec(
        task_id=task_id,
        goal="produce a verified file through the Free-First router",
        steps=(
            PlanStep(
                step_id="write",
                agent=agent.spec().name,
                action="generate",
                inputs={"prompt": prompt, "out": out, "language": language},
                verification=(
                    EvidenceKind.ARTIFACT_SHA256.value,
                    EvidenceKind.OUTPUT_NON_EMPTY.value,
                ),
                max_attempts=2,
            ),
        ),
        created_by="fleet-router-check",
    )
    db = workspace / "fleet.db"
    ws = workspace / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    with DurableTaskStore(str(db)) as store:
        runner = FleetRunner(
            store,
            registry,
            workspace_root=ws,
            config=_runner_config(),
        )
        report = runner.submit_and_run(spec)
        snapshot = store.get_task(task_id)
        events = [event for event in store.events(task_id, limit=200)]
        step_result = store.step_result(task_id, "write") or {}
        dead_letters = [dict(row) for row in store.list_dead_letters()]
    target = ws / out
    return {
        "task_id": task_id,
        "status": report.status.value,
        "degraded": snapshot.degraded,
        "step": {
            "status": snapshot.steps[0].status.value,
            "attempts": snapshot.steps[0].attempts,
            "summary": snapshot.steps[0].summary,
            "error": snapshot.steps[0].error,
        },
        "artifact_exists": target.is_file(),
        "artifact_bytes": target.stat().st_size if target.is_file() else 0,
        "evidence": (step_result.get("evidence") or {}),
        "events": [
            {"kind": event["kind"], "message": event["message"]} for event in events
        ],
        "dead_letters": dead_letters,
    }


def _runner_config():
    """Runner config with backoff short enough for a CI probe to observe."""
    from fleet2027 import RunnerConfig  # noqa: PLC0415

    return RunnerConfig(backoff_base_s=0.05, max_worker_threads=2)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["simulated", "live", "exhausted"], required=True)
    parser.add_argument("--prompt", default="write add(a, b) in python")
    parser.add_argument("--language", default="python")
    parser.add_argument("--out", default="app.py")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args(argv)

    answer = "```python\ndef add(a, b):\n    return a + b\n```"
    workspace = Path(args.workspace) if args.workspace else Path(
        tempfile.mkdtemp(prefix="fleet-router-")
    )
    workspace.mkdir(parents=True, exist_ok=True)

    backend, meta = build_backend(args.mode, answer=answer)
    configured = backend.is_configured()
    real_providers = [
        provider
        for provider in meta.get("providers", [])
        if provider.get("kind") not in {None, "deterministic"}
    ]
    real_configured = [
        provider for provider in real_providers if provider.get("configured")
    ]
    payload: dict[str, Any] = {
        "mode": args.mode,
        "backend": backend.name,
        "configured": configured,
        "real_providers_configured": len(real_configured),
        "real_providers_total": len(real_providers),
        **meta,
    }

    if args.mode == "live" and not real_configured:
        # The router ships a deterministic provider that is always "configured".
        # Counting that as a live run would turn a simulation into a claim, so
        # live mode refuses it explicitly.
        payload["reason"] = (
            "no real provider is configured; only the deterministic simulation "
            "provider would answer"
        )
        payload["verification_ok"] = False
        payload["status"] = "not_attempted"
        _write(args.json_out, payload)
        print(json.dumps(payload, indent=2))
        print(
            "\nNOT ATTEMPTED: the router would answer from its deterministic "
            "simulation provider. Set a real provider key (e.g. GEMINI_API_KEY) "
            "or run a local Ollama, then re-run. This is not a pass."
        )
        return EXIT_NOT_ATTEMPTED

    if not configured:
        payload["reason"] = "no provider is configured in this environment"
        payload["verification_ok"] = False
        payload["status"] = "not_attempted"
        _write(args.json_out, payload)
        print(json.dumps(payload, indent=2))
        print("\nNOT ATTEMPTED: no router provider is configured "
              "(set AI2027_* keys or run a local Ollama). This is not a pass.")
        return EXIT_NOT_ATTEMPTED

    result = run_step(
        backend=backend,
        workspace=workspace,
        task_id=f"router-{args.mode}",
        out=args.out,
        prompt=args.prompt,
        language=args.language,
    )
    payload.update(result)
    routing = payload["evidence"].get("routing") or {}
    payload["failovers"] = routing.get("failovers")
    payload["provider"] = routing.get("provider")
    payload["simulated"] = routing.get("simulated")
    payload["verification_ok"] = bool(
        payload["status"] == "succeeded" and payload["artifact_exists"]
    )
    _write(args.json_out, payload)
    print(json.dumps(payload, indent=2))

    if args.mode == "exhausted":
        honest = payload["status"] in {"failed", "dead_letter"} and not payload["artifact_exists"]
        print(
            f"\nEXHAUSTION {'HANDLED HONESTLY' if honest else 'LEAKED AS SUCCESS'} "
            f"(status={payload['status']}, artifact={payload['artifact_exists']})"
        )
        return EXIT_OK if honest else EXIT_FAILED

    if payload["verification_ok"]:
        extra = f", failovers={payload['failovers']}" if payload["failovers"] else ""
        print(f"\nROUTED STEP VERIFIED (provider={payload['provider']}{extra})")
        return EXIT_OK
    print(f"\nROUTED STEP FAILED (status={payload['status']})")
    return EXIT_FAILED


def _write(path: str | None, payload: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    raise SystemExit(main())
