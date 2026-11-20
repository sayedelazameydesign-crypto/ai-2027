"""A1 tests: an LLM-backed agent whose output still has to survive the verifier.

No test here touches the internet. The transport is exercised against a **real
local HTTP server** bound to 127.0.0.1 (which is exactly what ``OllamaBackend``
is allowed to talk to), and every other case uses a scripted backend.

The four acceptance cases from the plan map to:

* ``ScriptedBackend`` returning valid code  → step verified and succeeded
* ``ScriptedBackend`` returning nothing     → step refused, then dead-lettered
* backend raising / unconfigured            → ``StepFailure`` → retry → fallback
* zero real network calls in the unit suite → the loopback stub only
"""

from __future__ import annotations

import http.server
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from fleet2027 import (  # noqa: E402
    AgentRegistry,
    BackendNotConfigured,
    DurableTaskStore,
    EvidenceKind,
    FleetRunner,
    LLMAgent,
    LLMError,
    LLMRequest,
    LLMResponse,
    OllamaBackend,
    OpenAICompatibleBackend,
    PlanStep,
    RunnerConfig,
    ScriptedBackend,
    TaskSpec,
    TaskStatus,
)
from fleet2027.contracts import ContractError  # noqa: E402
from fleet2027.llm import _extract_code, _looks_like_refusal  # noqa: E402

# --------------------------------------------------------------------------- #
# A real loopback HTTP server that speaks the Ollama wire format
# --------------------------------------------------------------------------- #


class _OllamaStub(http.server.BaseHTTPRequestHandler):
    """Minimal /api/chat implementation; records what it was asked."""

    reply_text = "```python\ndef add(a, b):\n    return a + b\n```"
    status = 200
    seen: list[dict] = []
    fail_times = 0

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).seen.append(body)
        if type(self).fail_times > 0:
            type(self).fail_times -= 1
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"error":"model not loaded"}')
            return
        payload = json.dumps({"message": {"role": "assistant", "content": type(self).reply_text}})
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload.encode())

    def log_message(self, *args: object) -> None:  # noqa: D102 - silence stderr access logs
        return


class LoopbackServer:
    """Context manager that runs the stub on an ephemeral loopback port."""

    def __init__(self) -> None:
        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), _OllamaStub)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


# --------------------------------------------------------------------------- #
# Request / response contracts
# --------------------------------------------------------------------------- #


class RequestContractTests(unittest.TestCase):
    def test_caps_are_enforced_at_construction(self) -> None:
        with self.assertRaises(ContractError):
            LLMRequest(prompt="   ")
        with self.assertRaises(ContractError):
            LLMRequest(prompt="x" * 16_001)
        with self.assertRaises(ContractError):
            LLMRequest(prompt="hi", temperature=2.0)
        with self.assertRaises(ContractError):
            LLMRequest(prompt="hi", max_tokens=0)
        with self.assertRaises(ContractError):
            LLMRequest(prompt="hi", timeout_s=10_000)

    def test_an_empty_completion_is_never_a_success(self) -> None:
        with self.assertRaises(LLMError):
            LLMResponse(text="  \n ", backend="x", model="y")


class ExtractionTests(unittest.TestCase):
    def test_language_fence_is_extracted(self) -> None:
        text = "Here you go:\n```python\nprint('hi')\n```\nHope it helps."
        self.assertEqual(_extract_code(text, "python"), "print('hi')")

    def test_unlabelled_fence_still_works(self) -> None:
        self.assertEqual(_extract_code("```\nvalue = 1\n```", "python"), "value = 1")

    def test_no_fence_means_verbatim_text(self) -> None:
        self.assertEqual(_extract_code("value = 1\n", "python"), "value = 1")

    def test_refusal_detection_is_narrow(self) -> None:
        self.assertTrue(_looks_like_refusal("I'm sorry, but I cannot help with that."))
        self.assertFalse(_looks_like_refusal("def add(a, b):\n    return a + b"))
        long_code = "x = 1\n" * 200
        self.assertFalse(_looks_like_refusal(long_code))


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #


class BackendTests(unittest.TestCase):
    def setUp(self) -> None:
        _OllamaStub.seen = []
        _OllamaStub.fail_times = 0
        _OllamaStub.status = 200
        _OllamaStub.reply_text = "```python\ndef add(a, b):\n    return a + b\n```"

    def test_ollama_backend_talks_to_a_real_loopback_server(self) -> None:
        with LoopbackServer() as url:
            backend = OllamaBackend(base_url=url, model="stub:1b")
            response = backend.complete(LLMRequest(prompt="write add", max_tokens=64))
        self.assertIn("def add", response.text)
        self.assertEqual(response.backend, "ollama")
        self.assertEqual(response.model, "stub:1b")
        self.assertEqual(len(_OllamaStub.seen), 1, "exactly one request reached the server")
        self.assertEqual(_OllamaStub.seen[0]["model"], "stub:1b")
        self.assertFalse(_OllamaStub.seen[0]["stream"])

    def test_http_500_becomes_a_retryable_llm_error(self) -> None:
        _OllamaStub.fail_times = 1
        with LoopbackServer() as url:
            backend = OllamaBackend(base_url=url)
            with self.assertRaises(LLMError) as ctx:
                backend.complete(LLMRequest(prompt="write add"))
        self.assertIn("http_500", str(ctx.exception))

    def test_unconfigured_ollama_is_refused_before_any_socket(self) -> None:
        backend = OllamaBackend(base_url="", model="m")
        self.assertFalse(backend.is_configured())
        with self.assertRaises(BackendNotConfigured):
            backend.complete(LLMRequest(prompt="x"))

    def test_plain_http_to_a_remote_host_is_refused(self) -> None:
        backend = OllamaBackend(base_url="http://example.com:11434")
        with self.assertRaises(ContractError):
            backend.complete(LLMRequest(prompt="x"))

    def test_openai_compatible_requires_its_env_var(self) -> None:
        backend = OpenAICompatibleBackend(
            endpoint_url="https://api.example.com/v1/chat/completions",
            model="m",
            api_key_env="FLEET_TEST_KEY_NOT_SET",
        )
        self.assertFalse(backend.is_configured())
        with self.assertRaises(BackendNotConfigured):
            backend.complete(LLMRequest(prompt="x"))

    def test_openai_compatible_sends_the_key_in_a_header_only(self) -> None:
        import os

        os.environ["FLEET_TEST_KEY"] = "sk-not-a-real-key"
        captured: dict = {}

        def fake_post(url, payload, headers, *, timeout_s):  # noqa: ANN001, ANN202
            captured.update({"url": url, "payload": payload, "headers": dict(headers)})
            return {"choices": [{"message": {"content": "hello"}}]}

        import fleet2027.llm as llm_mod

        original = llm_mod._post_json
        llm_mod._post_json = fake_post  # type: ignore[assignment]
        try:
            backend = OpenAICompatibleBackend(
                endpoint_url="https://api.example.com/v1/chat/completions",
                model="m",
                api_key_env="FLEET_TEST_KEY",
            )
            response = backend.complete(LLMRequest(prompt="hi"))
        finally:
            llm_mod._post_json = original  # type: ignore[assignment]
            del os.environ["FLEET_TEST_KEY"]
        self.assertEqual(response.text, "hello")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer sk-not-a-real-key")
        self.assertNotIn("sk-not-a-real-key", json.dumps(captured["payload"]))
        self.assertNotIn("sk-not-a-real-key", response.text)


    def test_openai_compatible_uses_reasoning_when_content_is_empty(self) -> None:
        """Reasoning models answer in `reasoning`; an empty string is not no answer."""
        import os

        os.environ["FLEET_TEST_KEY"] = "sk-not-a-real-key"

        def fake_post(url, payload, headers, *, timeout_s):  # noqa: ANN001, ANN202
            return {"choices": [{"message": {"content": "", "reasoning": "the answer"},
                                 "finish_reason": "length"}]}

        import fleet2027.llm as llm_mod

        original = llm_mod._post_json
        llm_mod._post_json = fake_post  # type: ignore[assignment]
        try:
            backend = OpenAICompatibleBackend(
                endpoint_url="https://api.example.com/v1/chat/completions",
                model="m",
                api_key_env="FLEET_TEST_KEY",
            )
            response = backend.complete(LLMRequest(prompt="hi"))
        finally:
            llm_mod._post_json = original  # type: ignore[assignment]
            del os.environ["FLEET_TEST_KEY"]
        self.assertEqual(response.text, "the answer")

    def test_openai_compatible_says_which_payload_shape_was_wrong(self) -> None:
        """The message must name the failure; 'invalid payload' cost a round trip."""
        import os

        os.environ["FLEET_TEST_KEY"] = "sk-not-a-real-key"

        def fake_post(url, payload, headers, *, timeout_s):  # noqa: ANN001, ANN202
            return {"choices": [{"message": {"content": None, "refusal": "I cannot help"},
                                 "finish_reason": "stop"}], "id": "x"}

        import fleet2027.llm as llm_mod

        original = llm_mod._post_json
        llm_mod._post_json = fake_post  # type: ignore[assignment]
        try:
            backend = OpenAICompatibleBackend(
                endpoint_url="https://api.example.com/v1/chat/completions",
                model="m",
                api_key_env="FLEET_TEST_KEY",
            )
            with self.assertRaises(LLMError) as raised:
                backend.complete(LLMRequest(prompt="hi"))
        finally:
            llm_mod._post_json = original  # type: ignore[assignment]
            del os.environ["FLEET_TEST_KEY"]
        message = str(raised.exception)
        self.assertIn("refused", message)
        self.assertIn("finish_reason", message)
        self.assertIn("top_level_keys", message)

    def test_openai_compatible_empty_content_without_reasoning_is_an_error(self) -> None:
        import os

        os.environ["FLEET_TEST_KEY"] = "sk-not-a-real-key"

        def fake_post(url, payload, headers, *, timeout_s):  # noqa: ANN001, ANN202
            return {"choices": [{"message": {"content": "   "}, "finish_reason": "stop"}]}

        import fleet2027.llm as llm_mod

        original = llm_mod._post_json
        llm_mod._post_json = fake_post  # type: ignore[assignment]
        try:
            backend = OpenAICompatibleBackend(
                endpoint_url="https://api.example.com/v1/chat/completions",
                model="m",
                api_key_env="FLEET_TEST_KEY",
            )
            with self.assertRaises(LLMError):
                backend.complete(LLMRequest(prompt="hi"))
        finally:
            llm_mod._post_json = original  # type: ignore[assignment]
            del os.environ["FLEET_TEST_KEY"]


    def test_truncation_is_named_truncation_not_invalid_payload(self) -> None:
        """finish_reason='length' has a fix (raise the budget); a shape error does not."""
        import os

        os.environ["FLEET_TEST_KEY"] = "sk-not-a-real-key"

        def fake_post(url, payload, headers, *, timeout_s):  # noqa: ANN001, ANN202
            return {
                "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                    "usage": {"completion_tokens": 8}}

        import fleet2027.llm as llm_mod

        original = llm_mod._post_json
        llm_mod._post_json = fake_post  # type: ignore[assignment]
        try:
            backend = OpenAICompatibleBackend(
                endpoint_url="https://api.example.com/v1/chat/completions",
                model="m",
                api_key_env="FLEET_TEST_KEY",
            )
            with self.assertRaises(LLMError) as raised:
                backend.complete(LLMRequest(prompt="hi", max_tokens=8))
        finally:
            llm_mod._post_json = original  # type: ignore[assignment]
            del os.environ["FLEET_TEST_KEY"]
        message = str(raised.exception)
        self.assertIn("truncated", message)
        self.assertNotIn("invalid_payload", message)
        self.assertIn("completion_tokens", message)


    def test_quota_exhaustion_is_distinguishable_from_an_outage(self) -> None:
        """A 429 has a different fix (wait or pay) than a dead endpoint.

        This patches ``urlopen``, not ``_post_json``: replacing the helper
        would skip the very translation being tested.
        """
        import io
        import os
        import urllib.error

        os.environ["FLEET_TEST_KEY"] = "sk-not-a-real-key"

        def fake_urlopen(request, timeout):  # noqa: ANN001, ANN202
            raise urllib.error.HTTPError(
                "https://api.example.com/v1/chat/completions", 429,
                "Too Many Requests", {},
                io.BytesIO(b'{"error":{"message":"Rate limit exceeded"}}'),
            )

        import urllib.request

        import fleet2027.llm as llm_mod

        original = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            backend = OpenAICompatibleBackend(
                endpoint_url="https://api.example.com/v1/chat/completions",
                model="m",
                api_key_env="FLEET_TEST_KEY",
            )
            with self.assertRaises(LLMError) as raised:
                backend.complete(LLMRequest(prompt="hi"))
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]
            del os.environ["FLEET_TEST_KEY"]
        self.assertIn("http_429", str(raised.exception))

    def test_scripted_backend_replays_and_records_calls(self) -> None:
        backend = ScriptedBackend(script=["one", "two"])
        self.assertEqual(backend.complete(LLMRequest(prompt="a")).text, "one")
        self.assertEqual(backend.complete(LLMRequest(prompt="b")).text, "two")
        self.assertEqual(backend.calls, ["a", "b"])
        with self.assertRaises(LLMError):
            backend.complete(LLMRequest(prompt="c"))

    def test_a_single_scripted_answer_is_sticky_across_retries(self) -> None:
        backend = ScriptedBackend(script=["same every time"])
        for _ in range(3):
            self.assertEqual(backend.complete(LLMRequest(prompt="x")).text, "same every time")
        self.assertEqual(len(backend.calls), 3)


# --------------------------------------------------------------------------- #
# End-to-end through the runner
# --------------------------------------------------------------------------- #


class LLMThroughRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.ws = base / "ws"
        self.ws.mkdir()
        self.store = DurableTaskStore(str(base / "fleet.db"))

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def _spec(self, *, agent: str, out: str = "app.py", **inputs) -> TaskSpec:
        return TaskSpec(
            task_id="llm-task",
            goal="write a verified artifact from a model",
            steps=(
                PlanStep(
                    step_id="write",
                    agent=agent,
                    action="generate",
                    inputs={"prompt": "write add(a, b)", "out": out, "language": "python", **inputs},
                    verification=(
                        EvidenceKind.ARTIFACT_SHA256.value,
                        EvidenceKind.OUTPUT_NON_EMPTY.value,
                    ),
                ),
            ),
        )

    def _run(self, registry: AgentRegistry, spec: TaskSpec):
        runner = FleetRunner(
            self.store, registry, workspace_root=self.ws,
            config=RunnerConfig(backoff_base_s=0.01),
        )
        return runner.submit_and_run(spec)

    def test_valid_model_output_is_written_and_verified(self) -> None:
        backend = ScriptedBackend(
            script=["```python\ndef add(a, b):\n    return a + b\n```"]
        )
        agent = LLMAgent(backend=backend)
        registry, _ = __import__("fleet2027").build_llm_registry(agent)
        report = self._run(registry, self._spec(agent=agent.spec().name))

        self.assertEqual(report.status, TaskStatus.SUCCEEDED)
        self.assertEqual(report.verified, 1)
        written = self.ws / "app.py"
        self.assertTrue(written.is_file())
        self.assertIn("def add", written.read_text())
        self.assertNotIn("```", written.read_text(), "fences must not reach the file")
        record = self.store.step_result("llm-task", "write")
        self.assertTrue(record["verification"]["ok"])
        self.assertEqual(record["evidence"]["backend"], "scripted")
        self.assertEqual(record["evidence"]["called"], True)
        self.assertEqual(backend.calls, ["write add(a, b)"])

    def test_prose_when_code_is_required_is_refused(self) -> None:
        backend = ScriptedBackend(script=["I'm sorry, but I cannot write that file."])
        agent = LLMAgent(backend=backend)
        registry, _ = __import__("fleet2027").build_llm_registry(agent)
        report = self._run(registry, self._spec(agent=agent.spec().name))
        self.assertEqual(report.status, TaskStatus.DEAD_LETTER)
        self.assertEqual(report.verified, 0)
        step = self.store.get_task("llm-task").steps[0]
        self.assertIn("refused", step.error or "")
        self.assertFalse((self.ws / "app.py").exists(), "a refusal must not create a file")

    def test_a_backend_failure_falls_over_to_the_declared_backup(self) -> None:
        broken = ScriptedBackend(script=[LLMError("provider is down"), LLMError("still down")])
        good = ScriptedBackend(script=["```python\nx = 1\n```"])
        primary = LLMAgent(backend=broken, agent_name="llm-primary", fallbacks=("llm-backup",))
        # A distinct capability: fallbacks are addressed by name, never by tag,
        # so two writers can coexist without making routing ambiguous.
        backup = LLMAgent(backend=good, agent_name="llm-backup", capability="llm-write-backup")
        registry = AgentRegistry()
        registry.register(backup.spec(), backup.handler)
        registry.register(primary.spec(), primary.handler)

        report = self._run(registry, self._spec(agent="llm-primary"))
        self.assertEqual(report.status, TaskStatus.SUCCEEDED)
        self.assertIn("llm-backup", report.agents_used)
        self.assertEqual(good.calls, ["write add(a, b)"], "the backup served the request")
        events = [e["message"] for e in self.store.events("llm-task")]
        self.assertTrue(any("fallback" in message for message in events))

    def test_a_lying_model_cannot_pass_even_if_it_claims_success(self) -> None:
        """The agent writes the file, then we corrupt it: the verifier must refuse."""

        class CorruptingBackend(ScriptedBackend):
            def complete(self, request: LLMRequest) -> LLMResponse:
                return LLMResponse(text="```python\nvalue = 1\n```", backend="x", model="m")

        agent = LLMAgent(backend=CorruptingBackend())
        original_handler = agent.handler

        def sabotaged(ctx):  # noqa: ANN001, ANN202
            result = original_handler(ctx)
            # Tamper *after* the digest was computed — exactly the attack the
            # verifier exists to catch.
            (ctx.workspace_root / "app.py").write_text("value = 999\n", encoding="utf-8")
            return result

        registry = AgentRegistry()
        registry.register(agent.spec(), sabotaged)
        report = self._run(registry, self._spec(agent=agent.spec().name))
        self.assertEqual(report.status, TaskStatus.DEAD_LETTER)
        self.assertEqual(report.verified, 0)
        step = self.store.get_task("llm-task").steps[0]
        self.assertIn("verification failed", step.error or "")

    def test_an_unconfigured_backend_fails_with_a_clear_reason(self) -> None:
        agent = LLMAgent(backend=OllamaBackend(base_url="", model="m"))
        registry, _ = __import__("fleet2027").build_llm_registry(agent)
        report = self._run(registry, self._spec(agent=agent.spec().name))
        self.assertEqual(report.status, TaskStatus.DEAD_LETTER)
        step = self.store.get_task("llm-task").steps[0]
        self.assertIn("not configured", step.error or "")

    def test_writing_outside_the_workspace_is_refused(self) -> None:
        backend = ScriptedBackend(script=["```python\nx = 1\n```"])
        agent = LLMAgent(backend=backend)
        registry, _ = __import__("fleet2027").build_llm_registry(agent)
        report = self._run(registry, self._spec(agent=agent.spec().name, out="../escape.py"))
        self.assertEqual(report.status, TaskStatus.DEAD_LETTER)
        self.assertFalse((self.ws.parent / "escape.py").exists())
        self.assertEqual(backend.calls, [], "the model is never called for an unsafe path")

    def test_a_missing_prompt_is_a_step_failure_not_a_crash(self) -> None:
        agent = LLMAgent(backend=ScriptedBackend(script=["x"]))
        registry, _ = __import__("fleet2027").build_llm_registry(agent)
        spec = TaskSpec(
            task_id="llm-task", goal="no prompt",
            steps=(PlanStep(step_id="write", agent=agent.spec().name, action="generate"),),
        )
        report = self._run(registry, spec)
        self.assertEqual(report.status, TaskStatus.DEAD_LETTER)
        self.assertIn("prompt", self.store.get_task("llm-task").steps[0].error or "")

    def test_oversized_output_is_refused_instead_of_written(self) -> None:
        big = "```python\n" + "x = 1\n" * 20_000 + "```"
        agent = LLMAgent(backend=ScriptedBackend(script=[big]), max_output_bytes=1_000)
        registry, _ = __import__("fleet2027").build_llm_registry(agent)
        report = self._run(registry, self._spec(agent=agent.spec().name))
        self.assertEqual(report.status, TaskStatus.DEAD_LETTER)
        self.assertIn("exceeds cap", self.store.get_task("llm-task").steps[0].error or "")
        self.assertFalse((self.ws / "app.py").exists())

    def test_real_loopback_model_call_end_to_end(self) -> None:
        """The only network in this suite: a stub bound to 127.0.0.1."""
        _OllamaStub.seen = []
        _OllamaStub.fail_times = 0
        _OllamaStub.reply_text = "```python\ndef add(a, b):\n    return a + b\n```"
        with LoopbackServer() as url:
            agent = LLMAgent(backend=OllamaBackend(base_url=url, model="stub:1b"))
            registry, _ = __import__("fleet2027").build_llm_registry(agent)
            report = self._run(registry, self._spec(agent=agent.spec().name))
        self.assertEqual(report.status, TaskStatus.SUCCEEDED)
        self.assertEqual(len(_OllamaStub.seen), 1)
        record = self.store.step_result("llm-task", "write")
        self.assertEqual(record["evidence"]["backend"], "ollama")
        self.assertEqual(record["evidence"]["model"], "stub:1b")
        self.assertTrue(record["evidence"]["called"])


class ProviderBridgeTests(unittest.TestCase):
    def test_async_provider_is_bridged_without_importing_it(self) -> None:
        from fleet2027 import AsyncProviderBridge

        class FakeAsyncProvider:
            model = "fake-async-1"

            def is_configured(self) -> bool:
                return True

            async def complete(self, request, *, timeout_s):  # noqa: ANN001, ANN202, ARG002
                return f"echo:{request['prompt']}"

        bridge = AsyncProviderBridge(
            provider=FakeAsyncProvider(),
            prompt_builder=lambda req: {"prompt": req.prompt},
            name_hint="bridge",
        )
        response = bridge.complete(LLMRequest(prompt="hello"))
        self.assertEqual(response.text, "echo:hello")
        self.assertEqual(response.model, "fake-async-1")
        self.assertEqual(response.backend, "bridge")

    def test_bridge_refuses_to_run_inside_a_running_loop(self) -> None:
        import asyncio

        from fleet2027 import AsyncProviderBridge

        class FakeAsyncProvider:
            model = "m"

            async def complete(self, request, *, timeout_s):  # noqa: ANN001, ANN202, ARG002
                return "x"

        bridge = AsyncProviderBridge(provider=FakeAsyncProvider(), prompt_builder=lambda r: r)

        async def inner() -> None:
            bridge.complete(LLMRequest(prompt="hi"))

        with self.assertRaises(LLMError):
            asyncio.run(inner())


class SpecTests(unittest.TestCase):
    def test_spec_is_a_valid_registration_contract(self) -> None:
        agent = LLMAgent(backend=ScriptedBackend())
        spec = agent.spec()
        self.assertEqual(spec.name, "llm-writer")
        self.assertIn("llm-write", spec.capabilities)
        self.assertEqual(spec.max_attempts, 2)
        self.assertIn(EvidenceKind.ARTIFACT_SHA256.value, spec.requires_evidence)

    def test_two_agents_cannot_both_claim_the_same_capability(self) -> None:
        from fleet2027 import AgentRegistrationError

        registry = AgentRegistry()
        first = LLMAgent(backend=ScriptedBackend(), agent_name="a1")
        second = LLMAgent(backend=ScriptedBackend(), agent_name="a2")
        registry.register(first.spec(), first.handler)
        with self.assertRaises(AgentRegistrationError):
            registry.register(second.spec(), second.handler)


if __name__ == "__main__":
    unittest.main(verbosity=2)

class InstructionCarriesCriteriaTests(unittest.TestCase):
    """A model graded on rules must be given those rules.

    The first real run scored usable=29% and failed on substance, relevance
    and honesty — none of which appeared in the instruction it received. A
    brief that hides its acceptance criteria is a defective brief, not a
    stricter test.
    """

    def test_the_default_prompt_states_every_graded_criterion(self) -> None:
        prompt = LLMAgent.__dataclass_fields__["system_prompt"].default
        for criterion in ("substance", "completeness", "relevance", "honesty"):
            self.assertIn(criterion, prompt, f"{criterion} is graded but not stated")

    def test_the_prompt_forbids_the_placeholder_the_scorer_rejects(self) -> None:
        prompt = LLMAgent.__dataclass_fields__["system_prompt"].default
        self.assertIn("placeholder", prompt)
        self.assertIn("TODO", prompt)


class UserAgentTests(unittest.TestCase):
    """A Cloudflare-fronted provider rejects the default urllib signature.

    api.groq.com answers `403 error code: 1010` to "Python-urllib/3.x" before
    Groq reads the key, while the same key succeeds from curl. Without an
    explicit agent the transport cannot reach Groq at all, and the failure
    looks like a bad credential.
    """

    def test_every_request_names_the_project(self) -> None:
        sent: dict[str, str] = {}

        class _Resp:
            status = 200

            def read(self, n: int = -1) -> bytes:
                return b'{"ok":true}'

            def __enter__(self) -> "UserAgentTests._Resp":
                return self

            def __exit__(self, *exc: object) -> None:
                return None

        def fake_urlopen(request: object, timeout: float = 0) -> object:
            sent.update({k.lower(): v for k, v in request.header_items()})
            return _Resp()

        import urllib.request

        original = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            import fleet2027.llm as llm_mod

            llm_mod._post_json(
                "https://api.example.com/v1/chat/completions",
                {"model": "m"},
                {"Authorization": "Bearer x"},
                timeout_s=5.0,
            )
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        agent = sent.get("user-agent", "")
        self.assertTrue(agent, "no User-Agent was sent; a WAF will reject urllib")
        self.assertNotIn("Python-urllib", agent)
        self.assertIn("ai-2027", agent)


class ProviderFailureMessageTests(unittest.TestCase):
    """A failed sentence must name the cause, not just the provider.

    The live run produced `gemini-2.5-flash failed:` with nothing after the
    colon because the bridge let the provider's exception through unchanged and
    the provider raised a bare error with no message. The report then looked
    like a model problem while the reason was available and discarded.
    """

    def test_a_message_less_provider_error_still_says_something(self) -> None:
        class Silent:
            name = "silent"
            model = "silent"

            def is_configured(self) -> bool:
                return True

            async def complete(self, request, *, timeout_s: float) -> str:
                raise RuntimeError()

        from fleet2027.llm import AsyncProviderBridge, LLMError, LLMRequest

        bridge = AsyncProviderBridge(
            provider=Silent(), prompt_builder=lambda req: {"prompt": req.prompt}
        )
        with self.assertRaises(LLMError) as caught:
            bridge.complete(LLMRequest(prompt="hi", timeout_s=5.0))
        text = str(caught.exception)
        self.assertTrue(text.strip(), "the failure message is empty again")
        self.assertIn("RuntimeError", text)

    def test_a_coded_provider_error_keeps_its_code_and_message(self) -> None:
        class Coded:
            name = "coded"
            model = "coded"

            def is_configured(self) -> bool:
                return True

            async def complete(self, request, *, timeout_s: float) -> str:
                exc = RuntimeError("quota exhausted for the day")
                exc.code = "quota"  # type: ignore[attr-defined]
                raise exc

        from fleet2027.llm import AsyncProviderBridge, LLMError, LLMRequest

        bridge = AsyncProviderBridge(
            provider=Coded(), prompt_builder=lambda req: {"prompt": req.prompt}
        )
        with self.assertRaises(LLMError) as caught:
            bridge.complete(LLMRequest(prompt="hi", timeout_s=5.0))
        text = str(caught.exception)
        self.assertIn("quota", text)
        self.assertIn("exhausted", text)
