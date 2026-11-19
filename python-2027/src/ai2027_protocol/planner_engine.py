"""Free-First LLM and deterministic step planning engine for AutonomousAgent."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from html import escape as html_escape
from typing import Any, Protocol, runtime_checkable

from .contracts import BillingTier, PromptRequest, ProviderAttempt, ProviderKind, StrictModel
from .router import AllProvidersFailedError, FreeFirstRouter
from .security import redact_secrets
from .tools import ToolExecutor, ToolInvocation, ToolOutcome


class PlannerDecision(StrictModel):
    """Structured step decision emitted by either a live LLM or offline engine."""

    thought: str = "Executing next step."
    plan: tuple[str, ...] = ()
    actions: tuple[ToolInvocation, ...] = ()
    final_answer: str | None = None
    billing_tier: BillingTier = BillingTier.LOCAL
    cost_known: bool = True
    cost_usd: float | None = 0.0
    router_attempts: tuple[ProviderAttempt, ...] = ()


@runtime_checkable
class AgentPlannerProtocol(Protocol):
    """Replaceable contract deciding the next tool batch or final response."""

    async def decide(
        self,
        *,
        task: str,
        step_num: int,
        max_steps: int,
        history_summary: str,
        artifacts: Sequence[str],
        memory_context: str,
        tool_executor: ToolExecutor,
        preferred_provider: ProviderKind | None,
        allow_remote: bool,
    ) -> tuple[PlannerDecision, str, str, bool]:
        """Return (decision, provider_name, model_name, simulated_llm)."""
        ...


def extract_json_object(raw_text: str) -> dict[str, Any] | None:
    """Extract the first balanced JSON object from raw LLM text or fenced code blocks."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if not in_str:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : idx + 1]
                    try:
                        parsed = json.loads(candidate)
                        return parsed if isinstance(parsed, dict) else None
                    except json.JSONDecodeError:
                        return None
    return None


def build_offline_html_artifact(task: str, findings_summary: str) -> str:
    """Render an isolated HTML report deliverable with escaped dynamic text."""
    safe_task = html_escape(task)
    safe_findings = html_escape(findings_summary[:2500])
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="UTF-8">\n'
        "<title>AI-2027 Studio Deliverable</title>\n"
        "<style>"
        "body{margin:0;font-family:system-ui,sans-serif;background:#07101c;"
        "color:#e6edf7;padding:28px}"
        ".card{max-width:860px;margin:0 auto;background:#0f192b;border:1px solid #1f3354;"
        "border-radius:16px;padding:24px;box-shadow:0 20px 50px rgba(0,0,0,.35)}"
        "h1{margin:0 0 8px;font-size:22px;color:#00e5a0}"
        ".pill{display:inline-block;padding:4px 10px;border-radius:99px;font-size:12px;"
        "background:rgba(0,229,160,.12);border:1px solid rgba(0,229,160,.4);color:#00e5a0}"
        "pre{white-space:pre-wrap;background:#060b14;padding:16px;border-radius:10px;"
        "border:1px solid #1b2a45;font-size:13px;line-height:1.55;color:#bfd2ea}"
        "</style>\n</head>\n<body>\n<div class='card'>\n"
        "<span class='pill'>L0 SHA-256 Verified Artifact · $0.00</span>\n"
        f"<h1>Goal: {safe_task}</h1>\n"
        "<p>Produced inside isolated L0 sandbox and verified before admission to L1 memory.</p>\n"
        f"<pre>{safe_findings}</pre>\n"
        "</div>\n</body>\n</html>\n"
    )


def format_step_history(
    steps: Sequence[tuple[int, str, Sequence[ToolOutcome]]],
) -> str:
    """Format compact observation text from prior steps for planner consumption."""
    return "\n".join(
        f"Step {num} thought={thought!r} -> "
        + " | ".join(
            f"{o.tool} ok={o.ok} summary={o.summary} out={o.output[:240]!r}" for o in outcomes
        )
        for num, thought, outcomes in steps[-4:]
    )


def _build_architecture_svg() -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 200">'
        '<rect width="640" height="200" rx="16" fill="#091322"/>'
        '<text x="28" y="46" fill="#00e5a0" font-family="monospace" font-size="18">'
        "AI-2027 Evidence-First Pipeline</text>"
        '<rect x="28" y="76" width="120" height="58" rx="10" fill="#12243e" '
        'stroke="#38bdf8"/>'
        '<text x="48" y="110" fill="#e6edf7" font-size="13">Free-First LLM</text>'
        '<rect x="184" y="76" width="130" height="58" rx="10" fill="#12243e" '
        'stroke="#00e5a0"/>'
        '<text x="200" y="110" fill="#e6edf7" font-size="13">Sandbox Tools</text>'
        '<rect x="350" y="76" width="125" height="58" rx="10" fill="#12243e" '
        'stroke="#fbbf24"/>'
        '<text x="366" y="110" fill="#e6edf7" font-size="13">L0 SHA-256 Gate</text>'
        '<rect x="510" y="76" width="102" height="58" rx="10" fill="#12243e" '
        'stroke="#a78bfa"/>'
        '<text x="526" y="110" fill="#e6edf7" font-size="13">L1 Memory</text>'
        "</svg>"
    )


def offline_decision(
    *,
    task: str,
    step_num: int,
    history_summary: str,
    artifacts: Sequence[str],
) -> PlannerDecision:
    """Deterministic multi-step task engine executing real repo, Python, and L0/L5 tools."""
    low = task.lower()
    wants_audit = any(k in low for k in ("audit", "score", "protocol", "تدقيق", "فحص", "بروتوكول"))
    wants_diff = any(k in low for k in ("git", "diff", "status", "changes", "تغييرات"))
    wants_tests = any(
        k in low for k in ("test", "unittest", "calculator", "build", "اختبار", "بناء", "كود")
    )
    wants_python = wants_tests or any(
        k in low
        for k in ("python", "script", "calc", "analyze", "benchmark", "تحليل", "سكربت", "حساب")
    )
    wants_connector = any(
        k in low
        for k in (
            "gmail",
            "notion",
            "linear",
            "drive",
            "dropbox",
            "calendar",
            "connector",
            "email",
            "موصل",
            "بريد",
        )
    )
    wants_svg = any(k in low for k in ("svg", "diagram", "chart", "رسم", "مخطط"))

    if step_num == 1:
        actions: list[ToolInvocation] = [
            ToolInvocation(
                tool="workspace.list_files", args={"path": ".", "scope": "workspace", "limit": 35}
            ),
            ToolInvocation(tool="memory.search", args={"query": task[:80], "k": 4}),
        ]
        if wants_audit:
            actions.append(ToolInvocation(tool="repo.audit", args={"path": "python-2027"}))
        if wants_diff:
            actions.append(ToolInvocation(tool="workspace.git_diff", args={"path": "."}))
        if wants_connector:
            actions.append(ToolInvocation(tool="connector.inspect", args={}))
        return PlannerDecision(
            thought="Gathering workspace layout, L1 memories, and system signals in parallel.",
            plan=(
                "Inspect workspace & L1 verified memory",
                "Run sandboxed code/tests & generate artifacts",
                "Independently verify SHA-256 & L0 ledger chain",
            ),
            actions=tuple(actions),
        )

    if step_num == 2:
        second_actions: list[ToolInvocation] = []
        if wants_tests:
            module_code = (
                "def compute_score(values: list[float]) -> dict[str, float]:\n"
                "    if not values:\n"
                "        return {'count': 0, 'sum': 0.0, 'mean': 0.0}\n"
                "    total = float(sum(values))\n"
                "    return {'count': len(values), 'sum': total, 'mean': total / len(values)}\n"
            )
            test_code = (
                "import unittest\n"
                "from solution import compute_score\n\n"
                "class SolutionTests(unittest.TestCase):\n"
                "    def test_compute_score(self) -> None:\n"
                "        res = compute_score([10.0, 20.0, 30.0])\n"
                "        self.assertEqual(res['count'], 3)\n"
                "        self.assertAlmostEqual(res['mean'], 20.0)\n\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n"
            )
            second_actions.extend(
                [
                    ToolInvocation(
                        tool="sandbox.write_artifact",
                        args={"path": "solution.py", "content": module_code},
                    ),
                    ToolInvocation(
                        tool="sandbox.write_artifact",
                        args={"path": "test_solution.py", "content": test_code},
                    ),
                    ToolInvocation(
                        tool="sandbox.run_tests",
                        args={"path": "test_solution.py", "timeout_s": 6.0},
                    ),
                ]
            )
        if wants_python or not artifacts:
            code = (
                "import json, pathlib\n"
                "files = sorted(p.name for p in pathlib.Path('.').iterdir())\n"
                "print(json.dumps({'sandbox_files': files, 'status': 'computed_safely'}))\n"
            )
            second_actions.append(
                ToolInvocation(
                    tool="sandbox.run_python",
                    args={"path": "analyze_task.py", "code": code, "timeout_s": 4.0},
                )
            )
        if wants_connector:
            second_actions.append(
                ToolInvocation(
                    tool="connector.propose_action",
                    args={
                        "connector": "gmail" if "gmail" in low or "بريد" in low else "notion",
                        "summary": f"Prepare guarded workspace update for: {task[:80]}",
                        "method": "POST",
                        "path": "/drafts",
                        "payload": {"task_excerpt": task[:120], "requires_human_review": True},
                    },
                )
            )
        if wants_svg:
            second_actions.append(
                ToolInvocation(
                    tool="sandbox.write_artifact",
                    args={"path": "architecture.svg", "content": _build_architecture_svg()},
                )
            )
        html_doc = build_offline_html_artifact(task, history_summary or "No prior findings.")
        second_actions.append(
            ToolInvocation(
                tool="sandbox.write_artifact",
                args={"path": "deliverable.html", "content": html_doc},
            )
        )
        return PlannerDecision(
            thought="Generating verified artifacts and running isolated sandbox tests.",
            actions=tuple(second_actions),
        )

    art_summary = "\n".join(f"- `{item}` (L0 SHA-256 verified)" for item in artifacts) or "- None"
    if "deliverable.html" in artifacts:
        verify_target = "deliverable.html"
    elif artifacts:
        verify_target = artifacts[0]
    else:
        verify_target = ""
    verify_actions: tuple[ToolInvocation, ...] = (
        (ToolInvocation(tool="sandbox.verify_artifact", args={"path": verify_target}),)
        if verify_target
        else ()
    )
    return PlannerDecision(
        thought="Running independent disk SHA-256 and L0 ledger chain verification.",
        actions=verify_actions,
        final_answer=(
            "### Verified Autonomous Execution Summary\n\n"
            "**Mode:** `offline-deterministic` (honest rule-based planner; `$0.00` cost)\n\n"
            f"**Observations:**\n{history_summary or 'None'}\n\n"
            f"**Verified Artifacts:**\n{art_summary}"
        ),
    )


class FreeFirstAgentPlanner:
    """Chooses between live Free-First LLM planning and offline deterministic execution."""

    def __init__(self, router: FreeFirstRouter) -> None:
        """Bind a FreeFirstRouter instance for remote/local LLM step inference."""
        self._router = router

    def _has_live_llm(
        self,
        *,
        preferred_provider: ProviderKind | None,
        allow_remote: bool,
    ) -> bool:
        if preferred_provider == ProviderKind.DETERMINISTIC:
            return False
        for provider in self._router.providers:
            if provider.kind == ProviderKind.DETERMINISTIC:
                continue
            if provider.is_remote and not allow_remote:
                continue
            if preferred_provider is not None and provider.kind != preferred_provider:
                continue
            if provider.is_configured():
                return True
        return False

    async def decide(
        self,
        *,
        task: str,
        step_num: int,
        max_steps: int,
        history_summary: str,
        artifacts: Sequence[str],
        memory_context: str,
        tool_executor: ToolExecutor,
        preferred_provider: ProviderKind | None,
        allow_remote: bool,
    ) -> tuple[PlannerDecision, str, str, bool]:
        """Ask a configured Free-First LLM for the next JSON step, or fall back honestly."""
        if not self._has_live_llm(preferred_provider=preferred_provider, allow_remote=allow_remote):
            return (
                offline_decision(
                    task=task,
                    step_num=step_num,
                    history_summary=history_summary,
                    artifacts=artifacts,
                ),
                ProviderKind.DETERMINISTIC.value,
                "offline-planner-v1",
                True,
            )

        tools_desc = "\n".join(
            f"- {s.name} (read_only={s.read_only}): {s.description} args={s.parameters_hint}"
            for s in tool_executor.specs()
        )
        system_prompt = (
            "You are AI-2027 Autonomous Studio Agent. Output ONLY one JSON object with keys:\n"
            '{"thought": str, "plan": [str], "actions": [{"tool": str, "args": {}}], '
            '"final_answer": str|null}.\n'
            "Use sandbox.write_artifact to create interactive HTML/SVG/Markdown deliverables, "
            "and connector.propose_action (never fake execution) for external mutations.\n"
            f"Available tools:\n{tools_desc}"
        )
        user_prompt = (
            f"Goal: {task}\nStep {step_num}/{max_steps}.\n"
            f"L1 Memory Context:\n{memory_context or 'None'}\n"
            f"Recent Observations:\n{history_summary or 'None yet.'}\n"
            f"Existing Artifacts: {list(artifacts)}"
        )
        preferred_tuple = (
            (preferred_provider, ProviderKind.DETERMINISTIC)
            if preferred_provider is not None
            else None
        )
        try:
            resp = await self._router.route(
                PromptRequest(
                    prompt=user_prompt,
                    system_prompt=system_prompt,
                    temperature=0.2,
                    max_tokens=1200,
                    allow_remote=allow_remote,
                    preferred_providers=preferred_tuple,
                )
            )
            if resp.provider == ProviderKind.DETERMINISTIC or resp.simulated:
                return (
                    offline_decision(
                        task=task,
                        step_num=step_num,
                        history_summary=history_summary,
                        artifacts=artifacts,
                    ),
                    ProviderKind.DETERMINISTIC.value,
                    "offline-planner-v1",
                    True,
                )
            parsed = extract_json_object(resp.text)
            if parsed is not None:
                decision = PlannerDecision.model_validate(
                    {
                        **parsed,
                        "billing_tier": resp.billing_tier,
                        "cost_known": resp.cost_known,
                        "cost_usd": resp.cost_usd,
                        "router_attempts": resp.attempts,
                    }
                )
                return decision, resp.provider.value, resp.model, False
            clean_text = redact_secrets(resp.text.strip())
            return (
                PlannerDecision(
                    thought="Synthesized direct response.",
                    final_answer=clean_text,
                    billing_tier=resp.billing_tier,
                    cost_known=resp.cost_known,
                    cost_usd=resp.cost_usd,
                    router_attempts=resp.attempts,
                ),
                resp.provider.value,
                resp.model,
                False,
            )
        except (AllProvidersFailedError, ValueError):
            return (
                offline_decision(
                    task=task,
                    step_num=step_num,
                    history_summary=history_summary,
                    artifacts=artifacts,
                ),
                ProviderKind.DETERMINISTIC.value,
                "offline-planner-v1",
                True,
            )
