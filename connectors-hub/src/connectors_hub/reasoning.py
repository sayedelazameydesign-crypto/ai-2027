"""Explicitly selected local or NVIDIA-hosted model inference.

This is a plain text completion endpoint, not a tool runner. It cannot execute
connector actions. Local inference is the default; cloud processing requires an
explicit per-request confirmation.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import ConfigurationError, Settings, validate_lm_studio_url
from .credentials import CredentialVault, VaultError
from .http_api import ProviderError, _raise_for_provider_status
from .models import ReasoningRequest, ReasoningResponse

_NVIDIA_CHAT_COMPLETIONS_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
_MAX_PROVIDER_RESPONSE_BYTES = 2 * 1024 * 1024
_MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,119}")
_DEFAULT_SYSTEM_PROMPT = (
    "Answer clearly and accurately in the user's language. Give a concise explanation "
    "with key assumptions and uncertainty where relevant. Do not claim private hidden "
    "reasoning, and do not claim to have executed tools or external actions."
)
_BIG_BRO_FARES_EDITORIAL_CONTEXT = (
    "Act as an editorial collaborator for the user-provided Big Bro Fares brand: "
    "direct, strategic, practical, and candid without flattery. Use natural Egyptian "
    "Arabic when it matches the user's language or is requested. The creator's supplied "
    "profile mentions 25 years in regional Learning and Development and family life; "
    "treat those as creator-provided context, never as the AI's own biography. Do not "
    "invent personal anecdotes, credentials, game events, or evidence. Draft first-person "
    "copy only as content for the creator, not as a claim about the AI. Preserve the "
    "creator's brand and rights; write original material rather than copying others."
)
_EDITORIAL_MODES = {
    "gaming_case_study": (
        "Treat gameplay and story as a case study: connect the given events to leadership, "
        "management failure, decision-making, and human behavior. Separate observed facts "
        "from interpretation and do not invent scenes."
    ),
    "life_lessons": (
        "Offer practical, realistic lessons about self-development, career decisions, and "
        "well-being under work pressure. Avoid empty motivation or unsupported claims; do "
        "not diagnose mental-health conditions."
    ),
    "tech_as_tool": (
        "Explain technology by its practical value, costs, privacy, security, and trade-offs. "
        "Challenge hype without replacing it with unsupported performance or financial claims."
    ),
}


def _estimate_context_upper_bound(system_prompt: str, prompt: str) -> int:
    """Use UTF-8 bytes as a conservative tokenizer-independent context estimate."""
    # Byte-level tokenizers cannot produce more tokens than input bytes. Reserve
    # extra room for message framing; this is deliberately conservative, not an
    # exact count from the selected model's tokenizer.
    return len(system_prompt.encode("utf-8")) + len(prompt.encode("utf-8")) + 64


def _validate_context_budget(
    request: ReasoningRequest, settings: Settings, system_prompt: str
) -> None:
    context_limit = settings.reasoning_context_tokens
    output_limit = settings.reasoning_max_output_tokens
    if not 1024 <= context_limit <= 65536 or not 1 <= output_limit <= min(
        2048, context_limit
    ):
        raise ProviderError(
            "invalid_context_config", "Configured model context limits are invalid"
        )
    if request.max_tokens > output_limit:
        raise ProviderError(
            "output_limit_exceeded",
            f"max_tokens exceeds the configured {output_limit}-token output cap",
        )
    estimated_input = _estimate_context_upper_bound(system_prompt, request.prompt)
    if estimated_input + request.max_tokens > context_limit:
        raise ProviderError(
            "context_limit_exceeded",
            "Prompt and output allowance exceed the configured model context budget",
        )


def _provider_config(
    request: ReasoningRequest, settings: Settings, vault: CredentialVault
) -> tuple[str, str, dict[str, str]]:
    if request.provider == "local":
        try:
            base = validate_lm_studio_url(settings.lm_studio_base_url)
        except ConfigurationError as exc:
            raise ProviderError(
                "unsafe_endpoint", "LM Studio endpoint is not an approved loopback URL"
            ) from exc
        model = request.model or settings.lm_studio_model
        if not _MODEL_PATTERN.fullmatch(model):
            raise ProviderError(
                "invalid_model", "LM Studio model identifier is invalid"
            )
        return (
            f"{base}/chat/completions",
            model,
            {"Accept": "application/json", "Content-Type": "application/json"},
        )

    if not request.allow_remote_processing:
        raise ProviderError(
            "remote_confirmation_required",
            "Set allow_remote_processing=true to send this prompt to NVIDIA-hosted inference",
        )
    model = request.model or settings.nvidia_model
    if not model:
        raise ProviderError(
            "model_required",
            "Choose a NVIDIA NIM model in the request or CONNECTORS_HUB_NVIDIA_MODEL",
        )
    if not _MODEL_PATTERN.fullmatch(model):
        raise ProviderError("invalid_model", "NVIDIA model identifier is invalid")
    try:
        api_key = vault.require("nvidia_api_key")
    except VaultError as exc:
        raise ProviderError(
            "not_configured", "NVIDIA API key is missing from the native OS keyring"
        ) from exc
    return (
        _NVIDIA_CHAT_COMPLETIONS_URL,
        model,
        {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )


async def reason_one(
    request: ReasoningRequest,
    *,
    client: httpx.AsyncClient,
    settings: Settings,
    vault: CredentialVault,
) -> ReasoningResponse:
    """Run one bounded chat completion without tool calls or provider fallback."""
    system_prompt = _DEFAULT_SYSTEM_PROMPT
    if request.system_prompt:
        system_prompt += "\n\nAdditional task instruction:\n" + request.system_prompt
    if request.mode != "general":
        system_prompt += (
            "\n\nBrand editorial guidance:\n" + _BIG_BRO_FARES_EDITORIAL_CONTEXT
        )
        system_prompt += "\n\nSelected mode:\n" + _EDITORIAL_MODES[request.mode]
    _validate_context_budget(request, settings, system_prompt)
    url, model, headers = _provider_config(request, settings, vault)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": request.prompt},
        ],
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "stream": False,
    }

    try:
        async with client.stream(
            "POST",
            url,
            headers=headers,
            json=payload,
            follow_redirects=False,
        ) as response:
            _raise_for_provider_status(response)
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > _MAX_PROVIDER_RESPONSE_BYTES:
                    raise ProviderError(
                        "response_too_large", "Model response exceeded the 2 MiB limit"
                    )
                chunks.append(chunk)
            raw = b"".join(chunks)
    except httpx.TimeoutException as exc:
        raise ProviderError("reasoning_timeout", "Model inference timed out") from exc
    except httpx.RequestError as exc:
        raise ProviderError(
            "model_unavailable", "The selected model endpoint could not be reached"
        ) from exc

    try:
        data: Any = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProviderError(
            "invalid_model_response", "Model provider returned invalid JSON"
        ) from exc
    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderError(
            "invalid_model_response",
            "Model provider response did not contain a completion",
        )
    message = choices[0].get("message")
    text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(text, str):
        raise ProviderError(
            "invalid_model_response", "Model provider completion was not text"
        )
    usage_data = data.get("usage") if isinstance(data, dict) else None
    usage = (
        {
            key: value
            for key, value in usage_data.items()
            if isinstance(key, str) and isinstance(value, int) and value >= 0
        }
        if isinstance(usage_data, dict)
        else None
    )
    return ReasoningResponse(
        provider=request.provider,
        mode=request.mode,
        model=model,
        text=text,
        usage=usage,
    )
