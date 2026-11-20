"""Provider request and response helpers for the isolated edge worker."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from worker_guard import env_get


def _extract_gemini(data: dict[str, Any]) -> str | None:
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        return None
    parts = candidates[0].get("content", {}).get("parts")
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], dict):
        return None
    text = parts[0].get("text")
    return text.strip() if isinstance(text, str) and text.strip() else None


def _extract_openai(data: dict[str, Any]) -> str | None:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    msg = choices[0].get("message")
    text = msg.get("content") if isinstance(msg, dict) else None
    return text.strip() if isinstance(text, str) and text.strip() else None


def _provider_specs(
    env: object, prompt: str, max_tokens: int, traceparent: str
) -> list[
    tuple[
        str,
        str,
        str,
        str,
        dict[str, str],
        dict[str, Any],
        Callable[[dict[str, Any]], str | None],
    ]
]:
    def _oai_payload(model: str) -> dict[str, Any]:
        return {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }

    gem_model = env_get(env, "GEMINI_MODEL", "gemini-2.5-flash")
    nv_model = env_get(env, "NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")
    groq_model = env_get(env, "GROQ_MODEL", "openai/gpt-oss-20b")
    or_model = env_get(env, "OPENROUTER_MODEL", "openrouter/free")

    return [
        (
            "gemini",
            env_get(env, "GEMINI_API_KEY"),
            gem_model,
            f"https://generativelanguage.googleapis.com/v1beta/models/{gem_model}:generateContent",
            {
                "content-type": "application/json",
                "x-goog-api-key": env_get(env, "GEMINI_API_KEY"),
                "traceparent": traceparent,
            },
            {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": max_tokens},
            },
            _extract_gemini,
        ),
        (
            "nvidia",
            env_get(env, "NVIDIA_API_KEY"),
            nv_model,
            "https://integrate.api.nvidia.com/v1/chat/completions",
            {
                "content-type": "application/json",
                "authorization": f"Bearer {env_get(env, 'NVIDIA_API_KEY')}",
                "traceparent": traceparent,
            },
            _oai_payload(nv_model),
            _extract_openai,
        ),
        (
            "groq",
            env_get(env, "GROQ_API_KEY"),
            groq_model,
            "https://api.groq.com/openai/v1/chat/completions",
            {
                "content-type": "application/json",
                "authorization": f"Bearer {env_get(env, 'GROQ_API_KEY')}",
                "traceparent": traceparent,
            },
            _oai_payload(groq_model),
            _extract_openai,
        ),
        (
            "openrouter",
            env_get(env, "OPENROUTER_API_KEY"),
            or_model,
            "https://openrouter.ai/api/v1/chat/completions",
            {
                "content-type": "application/json",
                "authorization": f"Bearer {env_get(env, 'OPENROUTER_API_KEY')}",
                "traceparent": traceparent,
            },
            _oai_payload(or_model),
            _extract_openai,
        ),
    ]
