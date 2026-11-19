"""Ollama Local Intelligence Layer: profiles, runtime inspection, JSON schema, tools, RAG."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
from collections.abc import AsyncIterator, Sequence
from typing import Any, Literal, TypeVar

import httpx
from pydantic import BaseModel, Field, ValidationError

from .ai_providers import ProviderError
from .contracts import StrictModel, ToolInvocation, ToolSpec
from .ollama_transport import (
    AsyncOllamaTransport as AsyncOllamaTransport,
)
from .ollama_transport import (
    HttpxOllamaTransport as HttpxOllamaTransport,
)
from .ollama_transport import (
    OllamaCloudProvider as OllamaCloudProvider,
)
from .ollama_transport import (
    OllamaHttpResult as OllamaHttpResult,
)
from .ollama_transport import (
    create_ollama_cloud_provider as create_ollama_cloud_provider,
)
from .security import redact_secrets, validate_provider_url

_TModel = TypeVar("_TModel", bound=BaseModel)
_OLLAMA_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}")

_ALLOWED_PROPOSED_TOOLS: frozenset[str] = frozenset(
    {
        "workspace.list_files",
        "workspace.read_file",
        "workspace.grep",
        "workspace.git_diff",
        "memory.search",
        "memory.rag_search",
        "repo.audit",
        "sandbox.write_artifact",
        "sandbox.edit_file",
        "sandbox.run_python",
        "sandbox.run_tests",
        "sandbox.verify_artifact",
        "connector.inspect",
        "connector.search",
        "connector.read",
        "connector.propose_action",
    }
)

OllamaProfileName = Literal["4gb-laptop", "8gb-standard", "colab-gpu"]


class HardwareProfileSpec(StrictModel):
    """Hardware-bounded Ollama defaults tailored to device RAM capacity."""

    name: OllamaProfileName = "4gb-laptop"
    max_ram_gb: float = Field(default=4.0, ge=1.0, le=512.0)
    chat_model: str = "qwen3.5:0.8b"
    coder_model: str = "qwen2.5-coder:0.5b"
    embed_model: str = "embeddinggemma"
    num_ctx: int = Field(default=2048, ge=256, le=32768)
    max_concurrent_models: int = Field(default=1, ge=1, le=4)
    unload_after_embed: bool = True
    avoid_models_note: str = (
        "Avoid >=3B/7B primary models and concurrent chat+embedding loads on 4GB RAM."
    )


_HARDWARE_PROFILES: dict[str, HardwareProfileSpec] = {
    "4gb-laptop": HardwareProfileSpec(),
    "8gb-standard": HardwareProfileSpec(
        name="8gb-standard",
        max_ram_gb=8.0,
        chat_model="qwen3.5:2b",
        coder_model="qwen2.5-coder:1.5b",
        embed_model="embeddinggemma",
        num_ctx=4096,
        max_concurrent_models=1,
        unload_after_embed=True,
        avoid_models_note="Avoid 7B+ concurrent loads on 8GB RAM hosts.",
    ),
    "colab-gpu": HardwareProfileSpec(
        name="colab-gpu",
        max_ram_gb=16.0,
        chat_model="qwen3.5:4b",
        coder_model="qwen2.5-coder:3b",
        embed_model="embeddinggemma",
        num_ctx=8192,
        max_concurrent_models=2,
        unload_after_embed=False,
        avoid_models_note="Watch Colab ephemeral disconnects; save evidence to Drive/GitHub.",
    ),
}


def resolve_hardware_profile(name: str | None = None) -> HardwareProfileSpec:
    """Resolve a declared Ollama hardware profile, defaulting fail-safe to 4gb-laptop."""
    raw = (name or os.environ.get("AI2027_OLLAMA_PROFILE", "4gb-laptop")).strip().lower()
    return _HARDWARE_PROFILES.get(raw, _HARDWARE_PROFILES["4gb-laptop"])


class OllamaModelTag(StrictModel):
    """Metadata for one locally installed Ollama model from /api/tags."""

    name: str
    size_bytes: int = Field(default=0, ge=0)
    parameter_size: str = ""
    quantization_level: str = ""
    family: str = ""


class OllamaRunningModel(StrictModel):
    """Metadata for one loaded model reported by /api/ps."""

    name: str
    size_bytes: int = Field(default=0, ge=0)
    size_vram_bytes: int = Field(default=0, ge=0)
    context_length: int = Field(default=0, ge=0)


class OllamaMemoryGuardStatus(StrictModel):
    """Runtime assessment against the hardware profile's concurrent model ceiling."""

    profile: OllamaProfileName
    running_count: int = Field(ge=0)
    max_concurrent_models: int = Field(ge=1)
    safe_to_load_another: bool
    total_loaded_mb: float = Field(ge=0.0)
    running_models: tuple[OllamaRunningModel, ...] = ()
    advisory: str


class OllamaToolTurnResult(StrictModel):
    """Parsed assistant turn proposing policy-checked ToolInvocation objects."""

    model: str
    content: str = ""
    proposed_calls: tuple[ToolInvocation, ...] = ()
    rejected_calls: tuple[str, ...] = ()
    all_proposals_safe: bool = True


def _l2_normalize(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(val * val for val in vec))
    if norm <= 0.0:
        return [0.0 for _ in vec]
    return [round(val / norm, 8) for val in vec]


def build_ollama_tool_schemas(specs: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    """Convert AI-2027 ToolSpec records into Ollama /api/chat tool definitions."""
    tools: list[dict[str, Any]] = []
    for spec in specs:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "args_hint": {"type": "string", "description": spec.parameters_hint}
                        },
                        "additionalProperties": True,
                    },
                },
            }
        )
    return tools


class OllamaRuntimeClient:
    """Async client covering Ollama tags, ps, structured chat, tools, and embeddings."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        profile: HardwareProfileSpec | None = None,
        transport: AsyncOllamaTransport | None = None,
    ) -> None:
        """Bind a validated loopback Ollama endpoint, hardware profile, and transport."""
        raw_url = base_url if base_url is not None else os.environ.get("OLLAMA_BASE_URL", "")
        self._base_url = (
            validate_provider_url(raw_url, allow_local_http=True) if raw_url.strip() else ""
        )
        self._profile = profile or resolve_hardware_profile()
        self._transport = transport or HttpxOllamaTransport()

    @property
    def base_url(self) -> str:
        """Return validated Ollama loopback base URL or empty string when unconfigured."""
        return self._base_url

    @property
    def profile(self) -> HardwareProfileSpec:
        """Return active hardware constraint profile."""
        return self._profile

    def is_configured(self) -> bool:
        """Return whether a loopback Ollama base URL has been configured."""
        return bool(self._base_url)

    async def list_tags(self, *, timeout_s: float = 4.0) -> list[OllamaModelTag]:
        """List locally pulled models from GET /api/tags."""
        if not self._base_url:
            raise ProviderError("not_configured", "OLLAMA_BASE_URL is not configured")
        res = await self._transport.get_json(
            f"{self._base_url}/api/tags",
            headers={"Accept": "application/json"},
            timeout_s=timeout_s,
        )
        raw_models = res.payload.get("models")
        if not isinstance(raw_models, list):
            raise ProviderError("invalid_payload", "Ollama /api/tags missing models list")
        tags: list[OllamaModelTag] = []
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("model") or "").strip()
            if not name:
                continue
            raw_details = item.get("details")
            details: dict[str, Any] = raw_details if isinstance(raw_details, dict) else {}
            size_val = item.get("size")
            tags.append(
                OllamaModelTag(
                    name=name,
                    size_bytes=max(0, int(size_val)) if isinstance(size_val, int | float) else 0,
                    parameter_size=str(details.get("parameter_size") or ""),
                    quantization_level=str(details.get("quantization_level") or ""),
                    family=str(details.get("family") or ""),
                )
            )
        return tags

    async def list_running(self, *, timeout_s: float = 4.0) -> list[OllamaRunningModel]:
        """Inspect currently resident models and memory footprint from GET /api/ps."""
        if not self._base_url:
            raise ProviderError("not_configured", "OLLAMA_BASE_URL is not configured")
        res = await self._transport.get_json(
            f"{self._base_url}/api/ps",
            headers={"Accept": "application/json"},
            timeout_s=timeout_s,
        )
        raw_models = res.payload.get("models")
        if not isinstance(raw_models, list):
            raise ProviderError("invalid_payload", "Ollama /api/ps missing models list")
        running: list[OllamaRunningModel] = []
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("model") or "").strip()
            if not name:
                continue
            size_b = item.get("size")
            vram_b = item.get("size_vram")
            ctx_len = item.get("context_length")
            running.append(
                OllamaRunningModel(
                    name=name,
                    size_bytes=max(0, int(size_b)) if isinstance(size_b, int | float) else 0,
                    size_vram_bytes=max(0, int(vram_b)) if isinstance(vram_b, int | float) else 0,
                    context_length=max(0, int(ctx_len)) if isinstance(ctx_len, int | float) else 0,
                )
            )
        return running

    async def check_memory_guard(self, *, timeout_s: float = 4.0) -> OllamaMemoryGuardStatus:
        """Verify whether loading another model respects the 4GB/hardware RAM cap."""
        running = await self.list_running(timeout_s=timeout_s)
        total_mb = round(sum(m.size_bytes for m in running) / (1024 * 1024), 2)
        safe = len(running) < self._profile.max_concurrent_models
        advisory = (
            "OK to load a single compact model."
            if safe
            else (
                f"At concurrent limit ({len(running)}/{self._profile.max_concurrent_models}); "
                "unload active model before loading embeddings or another chat model."
            )
        )
        return OllamaMemoryGuardStatus(
            profile=self._profile.name,
            running_count=len(running),
            max_concurrent_models=self._profile.max_concurrent_models,
            safe_to_load_another=safe,
            total_loaded_mb=total_mb,
            running_models=tuple(running),
            advisory=advisory,
        )

    async def chat_structured(
        self,
        prompt: str,
        schema_model: type[_TModel],
        *,
        model: str | None = None,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        timeout_s: float = 20.0,
    ) -> _TModel:
        """Call /api/chat with JSON Schema format and strictly validate via Pydantic V2."""
        if not self._base_url:
            raise ProviderError("not_configured", "OLLAMA_BASE_URL is not configured")
        chosen_model = (model or self._profile.chat_model).strip()
        if not _OLLAMA_MODEL_RE.fullmatch(chosen_model):
            raise ValueError("Invalid Ollama model identifier")
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        res = await self._transport.post_json(
            f"{self._base_url}/api/chat",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json_body={
                "model": chosen_model,
                "messages": messages,
                "stream": False,
                "format": schema_model.model_json_schema(),
                "options": {
                    "temperature": temperature,
                    "num_ctx": self._profile.num_ctx,
                },
            },
            timeout_s=timeout_s,
        )
        msg = res.payload.get("message")
        raw_content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(raw_content, str) or not raw_content.strip():
            raise ProviderError("invalid_payload", "Structured Ollama reply missing content")
        try:
            return schema_model.model_validate_json(raw_content)
        except ValidationError as exc:
            msg_snippet = redact_secrets(str(exc))[:140]
            raise ProviderError(
                "invalid_structured_output",
                f"Ollama output failed Pydantic schema validation: {msg_snippet}",
            ) from exc

    async def chat_with_tools(
        self,
        prompt: str,
        tool_specs: Sequence[ToolSpec],
        *,
        model: str | None = None,
        system_prompt: str | None = None,
        allowed_tools: frozenset[str] = _ALLOWED_PROPOSED_TOOLS,
        timeout_s: float = 20.0,
    ) -> OllamaToolTurnResult:
        """Ask Ollama for tool proposals and filter them strictly against Policy Gate."""
        if not self._base_url:
            raise ProviderError("not_configured", "OLLAMA_BASE_URL is not configured")
        chosen_model = (model or self._profile.chat_model).strip()
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        res = await self._transport.post_json(
            f"{self._base_url}/api/chat",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json_body={
                "model": chosen_model,
                "messages": messages,
                "stream": False,
                "tools": build_ollama_tool_schemas(tool_specs),
                "options": {"temperature": 0.0, "num_ctx": self._profile.num_ctx},
            },
            timeout_s=timeout_s,
        )
        msg = res.payload.get("message")
        if not isinstance(msg, dict):
            raise ProviderError("invalid_payload", "Ollama tool response missing message object")
        content = str(msg.get("content") or "").strip()
        raw_calls = msg.get("tool_calls") or []
        proposed: list[ToolInvocation] = []
        rejected: list[str] = []
        if isinstance(raw_calls, list):
            for call in raw_calls:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function")
                if not isinstance(fn, dict):
                    continue
                name = str(fn.get("name") or "").strip()
                raw_args = fn.get("arguments")
                if isinstance(raw_args, str):
                    try:
                        parsed_args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        rejected.append(f"{name or 'unknown'}:invalid_json_args")
                        continue
                elif isinstance(raw_args, dict):
                    parsed_args = raw_args
                else:
                    parsed_args = {}
                if name not in allowed_tools or not isinstance(parsed_args, dict):
                    rejected.append(name or "unknown_tool")
                    continue
                proposed.append(ToolInvocation(tool=name, args=parsed_args))
        return OllamaToolTurnResult(
            model=chosen_model,
            content=redact_secrets(content),
            proposed_calls=tuple(proposed),
            rejected_calls=tuple(rejected),
            all_proposals_safe=len(rejected) == 0,
        )

    async def embed(
        self,
        texts: Sequence[str],
        *,
        model: str | None = None,
        keep_alive: str | int | None = None,
        timeout_s: float = 20.0,
    ) -> list[list[float]]:
        """Generate L2-normalized embedding vectors via POST /api/embed."""
        if not texts:
            return []
        if not self._base_url:
            raise ProviderError("not_configured", "OLLAMA_BASE_URL is not configured")
        chosen_model = (model or self._profile.embed_model).strip()
        if not _OLLAMA_MODEL_RE.fullmatch(chosen_model):
            raise ValueError("Invalid Ollama embedding model identifier")
        body: dict[str, Any] = {"model": chosen_model, "input": list(texts)}
        effective_keep_alive = (
            0 if (keep_alive is None and self._profile.unload_after_embed) else keep_alive
        )
        if effective_keep_alive is not None:
            body["keep_alive"] = effective_keep_alive
        res = await self._transport.post_json(
            f"{self._base_url}/api/embed",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json_body=body,
            timeout_s=timeout_s,
        )
        raw_vecs = res.payload.get("embeddings")
        if not isinstance(raw_vecs, list) or len(raw_vecs) != len(texts):
            raise ProviderError("invalid_payload", "Ollama /api/embed returned invalid embeddings")
        vectors: list[list[float]] = []
        for item in raw_vecs:
            if not isinstance(item, list) or not item:
                raise ProviderError("invalid_payload", "Empty embedding vector from Ollama")
            vectors.append(_l2_normalize([float(x) for x in item]))
        return vectors

    async def stream_chat_deltas(
        self,
        prompt: str,
        *,
        model: str | None = None,
        timeout_s: float = 20.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> AsyncIterator[str]:
        """Stream incremental text chunks from /api/chat using non-blocking httpx."""
        if not self._base_url:
            raise ProviderError("not_configured", "OLLAMA_BASE_URL is not configured")
        chosen_model = (model or self._profile.chat_model).strip()
        payload = {
            "model": chosen_model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "options": {"num_ctx": self._profile.num_ctx},
        }
        own_client = http_client is None
        client = http_client or httpx.AsyncClient(follow_redirects=False)
        try:
            async with (
                asyncio.timeout(timeout_s),
                client.stream(
                    "POST",
                    f"{self._base_url}/api/chat",
                    json=payload,
                    timeout=timeout_s,
                ) as response,
            ):
                if response.status_code >= 400:
                    raise ProviderError(
                        f"http_{response.status_code}",
                        f"Ollama stream status {response.status_code}",
                        retryable=response.status_code >= 500,
                        status_code=response.status_code,
                    )
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if isinstance(chunk, dict):
                        msg = chunk.get("message")
                        if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                            text = msg["content"]
                            if text:
                                yield text
                        if chunk.get("done") is True:
                            break
        finally:
            if own_client:
                await client.aclose()
