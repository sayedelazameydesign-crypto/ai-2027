from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from connectors_hub.app import create_app
from connectors_hub.config import Settings
from connectors_hub.credentials import CredentialVault
from connectors_hub.http_api import ProviderError
from connectors_hub.models import ReasoningRequest
from connectors_hub.reasoning import reason_one


class MemoryKeyring:
    priority = 1.0

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


class ReasoningProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            data_dir=Path(self.temp.name),
            lm_studio_model="lmstudio-community/Qwen3.5-4B",
        )
        self.vault = CredentialVault(MemoryKeyring())

    def tearDown(self) -> None:
        self.temp.cleanup()

    async def test_local_inference_uses_loopback_without_credentials(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            self.assertEqual(
                str(request.url),
                "http://127.0.0.1:1234/v1/chat/completions",
            )
            self.assertIsNone(request.headers.get("authorization"))
            body = json.loads(request.content)
            self.assertEqual(body["model"], "lmstudio-community/Qwen3.5-4B")
            self.assertEqual(body["messages"][-1]["content"], "Summarize these notes.")
            self.assertEqual(body["max_tokens"], 128)
            self.assertFalse(body["stream"])
            self.assertNotIn("tools", body)
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "A short summary."}}],
                    "usage": {"prompt_tokens": 15, "completion_tokens": 6},
                },
            )

        request = ReasoningRequest(
            prompt="Summarize these notes.",
            max_tokens=128,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await reason_one(
                request,
                client=client,
                settings=self.settings,
                vault=self.vault,
            )

        self.assertEqual(len(requests), 1)
        self.assertEqual(result.provider, "local")
        self.assertEqual(result.mode, "general")
        self.assertEqual(result.model, "lmstudio-community/Qwen3.5-4B")
        self.assertEqual(result.text, "A short summary.")
        self.assertEqual(result.usage, {"prompt_tokens": 15, "completion_tokens": 6})

    async def test_gaming_mode_uses_brand_voice_without_fabricating_biography(
        self,
    ) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            body = json.loads(request.content)
            system_prompt = body["messages"][0]["content"]
            self.assertIn("Big Bro Fares", system_prompt)
            self.assertIn("case study", system_prompt)
            self.assertIn("leadership", system_prompt)
            self.assertIn("Do not invent personal anecdotes", system_prompt)
            self.assertIn("never as the AI's own biography", system_prompt)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "Case-study analysis."}}]},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await reason_one(
                ReasoningRequest(
                    mode="gaming_case_study",
                    prompt="Analyze the gameplay scene I provide.",
                    max_tokens=128,
                ),
                client=client,
                settings=self.settings,
                vault=self.vault,
            )

        self.assertEqual(len(requests), 1)
        self.assertEqual(result.mode, "gaming_case_study")
        self.assertEqual(result.text, "Case-study analysis.")

    async def test_unsafe_local_endpoint_is_rejected_before_network_request(
        self,
    ) -> None:
        requests: list[httpx.Request] = []
        unsafe_settings = Settings(
            data_dir=Path(self.temp.name),
            lm_studio_base_url="https://inference.example/v1",
        )

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ProviderError) as caught:
                await reason_one(
                    ReasoningRequest(prompt="Keep this local."),
                    client=client,
                    settings=unsafe_settings,
                    vault=self.vault,
                )

        self.assertEqual(caught.exception.code, "unsafe_endpoint")
        self.assertEqual(requests, [])

    async def test_nvidia_requires_per_request_remote_confirmation(self) -> None:
        requests: list[httpx.Request] = []
        self.vault.set("nvidia_api_key", "unit-test-key")

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ProviderError) as caught:
                await reason_one(
                    ReasoningRequest(
                        provider="nvidia",
                        model="nvidia/test-model",
                        prompt="This must not be sent.",
                    ),
                    client=client,
                    settings=self.settings,
                    vault=self.vault,
                )

        self.assertEqual(caught.exception.code, "remote_confirmation_required")
        self.assertEqual(requests, [])

    async def test_nvidia_missing_key_fails_without_network_request(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ProviderError) as caught:
                await reason_one(
                    ReasoningRequest(
                        provider="nvidia",
                        model="nvidia/test-model",
                        prompt="Prompt text.",
                        allow_remote_processing=True,
                    ),
                    client=client,
                    settings=self.settings,
                    vault=self.vault,
                )

        self.assertEqual(caught.exception.code, "not_configured")
        self.assertEqual(requests, [])

    async def test_confirmed_nvidia_request_uses_fixed_endpoint_and_keyring_key(
        self,
    ) -> None:
        requests: list[httpx.Request] = []
        self.vault.set("nvidia_api_key", "unit-test-key")

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            self.assertEqual(
                str(request.url),
                "https://integrate.api.nvidia.com/v1/chat/completions",
            )
            self.assertEqual(request.headers["authorization"], "Bearer unit-test-key")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "nvidia/test-model")
            self.assertEqual(
                body["messages"][-1]["content"], "Send only after consent."
            )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "Remote result."}}]},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await reason_one(
                ReasoningRequest(
                    provider="nvidia",
                    model="nvidia/test-model",
                    prompt="Send only after consent.",
                    allow_remote_processing=True,
                ),
                client=client,
                settings=self.settings,
                vault=self.vault,
            )

        self.assertEqual(len(requests), 1)
        self.assertEqual(result.provider, "nvidia")
        self.assertEqual(result.text, "Remote result.")

    async def test_context_budget_rejects_large_prompt_before_key_or_network(
        self,
    ) -> None:
        requests: list[httpx.Request] = []
        constrained_settings = Settings(
            data_dir=Path(self.temp.name),
            reasoning_context_tokens=1024,
            reasoning_max_output_tokens=512,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ProviderError) as caught:
                await reason_one(
                    ReasoningRequest(
                        provider="nvidia",
                        prompt="x" * 1000,
                        model="nvidia/test-model",
                        max_tokens=128,
                        allow_remote_processing=True,
                    ),
                    client=client,
                    settings=constrained_settings,
                    vault=self.vault,
                )

        self.assertEqual(caught.exception.code, "context_limit_exceeded")
        self.assertEqual(requests, [])


class ReasoningApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_reasoning_route_is_authenticated_and_remote_is_never_implicit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = CredentialVault(MemoryKeyring())
            vault.set("nvidia_api_key", "unit-test-key")
            requests: list[httpx.Request] = []

            def provider_handler(request: httpx.Request) -> httpx.Response:
                requests.append(request)
                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": "Mock answer."}}]},
                )

            def client_factory() -> httpx.AsyncClient:
                return httpx.AsyncClient(
                    transport=httpx.MockTransport(provider_handler)
                )

            app = create_app(
                settings=Settings(data_dir=root),
                vault=vault,
                api_token="local-test-token",
                async_client_factory=client_factory,
            )
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://hub.test"
            ) as client:
                route = "/v1/reasoning"
                unauthorized = await client.post(
                    route, json={"prompt": "Do not send this."}
                )
                self.assertEqual(unauthorized.status_code, 401)
                self.assertEqual(requests, [])

                headers = {"Authorization": "Bearer local-test-token"}
                local = await client.post(
                    route,
                    headers=headers,
                    json={"prompt": "Use local inference."},
                )
                self.assertEqual(local.status_code, 200)
                self.assertEqual(local.json()["provider"], "local")
                self.assertEqual(len(requests), 1)

                remote_default = await client.post(
                    route,
                    headers=headers,
                    json={
                        "provider": "nvidia",
                        "model": "nvidia/test-model",
                        "prompt": "Keep this on device.",
                    },
                )
                self.assertEqual(remote_default.status_code, 409)
                self.assertEqual(len(requests), 1)

                remote_confirmed = await client.post(
                    route,
                    headers=headers,
                    json={
                        "provider": "nvidia",
                        "model": "nvidia/test-model",
                        "prompt": "This may go to NVIDIA.",
                        "allow_remote_processing": True,
                    },
                )
                self.assertEqual(remote_confirmed.status_code, 200)
                self.assertEqual(remote_confirmed.json()["provider"], "nvidia")
                self.assertEqual(len(requests), 2)
                self.assertTrue(
                    str(requests[-1].url).startswith(
                        "https://integrate.api.nvidia.com/"
                    )
                )


if __name__ == "__main__":
    unittest.main()
