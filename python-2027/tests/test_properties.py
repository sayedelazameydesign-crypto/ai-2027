from __future__ import annotations

import string
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from ai2027_protocol.ai_providers import DeterministicFallbackProvider, ProviderError
from ai2027_protocol.benchmark import summarize_nanoseconds
from ai2027_protocol.contracts import AIProvider, PromptRequest, ProviderKind
from ai2027_protocol.router import FreeFirstRouter, RouterConfig
from ai2027_protocol.security import (
    SecurityValidationError,
    redact_secrets,
    resolve_safe_path,
    scrub_mapping,
)


@given(
    suffix=st.text(alphabet=string.ascii_letters + string.digits + "_-", min_size=24, max_size=40),
    prefix=st.sampled_from(["AIza", "sk-proj-", "gsk_", "ghp_"]),
)
def test_property_secret_redaction_never_leaks_tokens(prefix: str, suffix: str) -> None:
    token = prefix + suffix
    text = f"error contacting upstream with {token} inside response"
    scrubbed = redact_secrets(text)
    assert token not in scrubbed
    assert "[REDACTED]" in scrubbed

    mapped = scrub_mapping({"api_key": token, "nested": {"note": text}, "max_tokens": 64})
    assert mapped["api_key"] == "[REDACTED]"
    assert token not in mapped["nested"]["note"]
    assert mapped["max_tokens"] == 64


@given(
    segments=st.lists(
        st.sampled_from(["..", "..\\", "%2e%2e", "C:", "/etc", "sub", "nested\x00x"]),
        min_size=1,
        max_size=5,
    )
)
def test_property_safe_path_never_escapes_workspace(segments: list[str]) -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        candidate = "/".join(segments)
        try:
            resolved = resolve_safe_path(root, candidate)
        except SecurityValidationError:
            return
        assert resolved.is_relative_to(root)


@given(samples=st.lists(st.integers(min_value=1, max_value=500_000_000), min_size=1, max_size=50))
def test_property_timing_stats_are_ordered_and_bounded(samples: list[int]) -> None:
    stats = summarize_nanoseconds("prop-timing", samples)
    assert stats.n == len(samples)
    assert 0.0 <= stats.min_ms <= stats.p50_ms <= stats.p95_ms <= stats.max_ms
    assert stats.spread_pct >= 0.0
    assert stats.ops_per_sec > 0.0


@given(
    prompt=st.text(
        alphabet=string.ascii_letters + string.digits + " .,!?\n",
        min_size=1,
        max_size=200,
    ).filter(lambda s: bool(s.strip())),
    temperature=st.floats(min_value=0.0, max_value=1.5, allow_nan=False, allow_infinity=False),
    max_tokens=st.integers(min_value=1, max_value=4096),
)
def test_property_prompt_request_accepts_valid_domain(
    prompt: str, temperature: float, max_tokens: int
) -> None:
    req = PromptRequest(prompt=prompt, temperature=temperature, max_tokens=max_tokens)
    assert req.prompt == prompt.strip()
    assert req.temperature == temperature
    assert req.max_tokens == max_tokens


@given(bad_temp=st.floats(min_value=1.51, max_value=10.0, allow_nan=False, allow_infinity=False))
def test_property_prompt_request_rejects_out_of_range_temperature(bad_temp: float) -> None:
    with pytest.raises(ValidationError):
        PromptRequest(prompt="hello", temperature=bad_temp)


@settings(max_examples=20)
@given(fail_mask=st.lists(st.booleans(), min_size=2, max_size=2))
async def test_property_router_always_resolves_to_first_healthy_backup(
    fail_mask: list[bool],
) -> None:
    class _ToggleProvider:
        def __init__(self, kind: ProviderKind, should_fail: bool) -> None:
            self.kind = kind
            self.model = f"model-{kind.value}"
            self.is_remote = True
            self._should_fail = should_fail

        def is_configured(self) -> bool:
            return True

        async def complete(self, request: PromptRequest, *, timeout_s: float) -> str:
            if self._should_fail:
                raise ProviderError("http_503", "down", retryable=True)
            return f"ok-from-{self.kind.value}"

    providers: list[AIProvider] = [
        _ToggleProvider(ProviderKind.GEMINI, fail_mask[0]),
        _ToggleProvider(ProviderKind.GROQ, fail_mask[1]),
        DeterministicFallbackProvider(),
    ]
    router = FreeFirstRouter(
        providers=providers,
        config=RouterConfig(max_retries_per_provider=0, base_backoff_s=0.0),
    )
    resp = await router.route(PromptRequest(prompt="property fallback"))
    assert resp.cost_usd == 0.0
    if not fail_mask[0]:
        assert resp.provider == ProviderKind.GEMINI
    elif not fail_mask[1]:
        assert resp.provider == ProviderKind.GROQ
    else:
        assert resp.provider == ProviderKind.DETERMINISTIC
        assert resp.simulated is True
