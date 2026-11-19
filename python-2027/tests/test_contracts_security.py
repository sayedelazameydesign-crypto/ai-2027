from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai2027_protocol.contracts import (
    AIProvider,
    AuditRequest,
    BatchPromptRequest,
    PromptRequest,
    ProviderAttempt,
    ProviderKind,
    TelemetrySink,
)
from ai2027_protocol.router import FreeFirstRouter
from ai2027_protocol.security import (
    SecurityValidationError,
    redact_secrets,
    resolve_safe_path,
    sanitize_prompt_text,
    scrub_mapping,
    validate_provider_url,
)
from ai2027_protocol.telemetry import ObservabilityRuntime


def test_prompt_request_strict_validation() -> None:
    req = PromptRequest(prompt="  Hello AI-2027  ", system_prompt="  Be brief ")
    assert req.prompt == "Hello AI-2027"
    assert req.system_prompt == "Be brief"
    with pytest.raises(ValidationError):
        PromptRequest(prompt="ok", extra_field="forbidden")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        PromptRequest(prompt="   ")
    with pytest.raises(ValidationError):
        PromptRequest(prompt="bad\x00null")
    with pytest.raises(ValidationError):
        PromptRequest(prompt="bad\x07bell")


def test_provider_attempt_scrubs_secrets_in_detail() -> None:
    fake_gemini = "AIza" + "B" * 35
    attempt = ProviderAttempt(
        provider=ProviderKind.GEMINI,
        model="gemini-2.5-flash",
        attempt=1,
        ok=False,
        latency_ms=12.5,
        error_code="http_403",
        detail=f"Denied with key {fake_gemini} and ?key={fake_gemini}",
    )
    assert attempt.detail is not None
    assert fake_gemini not in attempt.detail
    assert "[REDACTED]" in attempt.detail


def test_security_redaction_and_scrub_mapping() -> None:
    fake_sk = "sk-proj-" + "A" * 34
    fake_gh = "ghp_" + "Z" * 36
    fake_nv = "nvapi-" + "N" * 32
    payload = {
        "api_key": "should-hide",
        "max_tokens": 256,
        "credential_state": "present (40 chars)",
        "nested": {"authorization": f"Bearer {fake_sk}", "msg": f"token {fake_gh}"},
        "items": [f"https://example.test?api_key={fake_sk}", {"secret": "hidden"}, 42],
    }
    scrubbed = scrub_mapping(payload)
    assert scrubbed["api_key"] == "[REDACTED]"
    assert scrubbed["max_tokens"] == 256
    assert scrubbed["credential_state"] == "present (40 chars)"
    assert scrubbed["nested"]["authorization"] == "[REDACTED]"
    assert fake_gh not in scrubbed["nested"]["msg"]
    assert fake_sk not in scrubbed["items"][0]
    assert scrubbed["items"][1]["secret"] == "[REDACTED]"  # noqa: S105
    assert scrubbed["items"][2] == 42
    assert fake_nv not in redact_secrets(f"nvidia failure key={fake_nv}")
    assert redact_secrets("normal diagnostic text") == "normal diagnostic text"


def test_validate_provider_url_blocks_ssrf_and_plaintext_remote() -> None:
    assert (
        validate_provider_url("https://api.groq.com/openai/v1") == "https://api.groq.com/openai/v1"
    )
    assert validate_provider_url("http://127.0.0.1:11434/") == "http://127.0.0.1:11434"
    for bad in (
        "",
        "http://api.groq.com/v1",
        "ftp://example.com",
        "https://user:pass@example.com",
        "https://example.com?key=secret",
        "https://169.254.169.254/latest",
        "https://10.0.0.5/api",
        "https:///missing-host",
    ):
        with pytest.raises(SecurityValidationError):
            validate_provider_url(bad)


def test_resolve_safe_path_blocks_cross_platform_escapes(tmp_path: Path) -> None:
    sub = tmp_path / "pkg"
    sub.mkdir()
    assert resolve_safe_path(tmp_path, "pkg") == sub.resolve()
    for escape in (
        "",
        "../outside",
        "pkg/../../outside",
        "..\\outside",
        "%2e%2e/outside",
        "pkg%2f..%2foutside",
        "C:\\Windows\\System32",
        "/etc/passwd",
        "pkg\x00.py",
    ):
        with pytest.raises(SecurityValidationError):
            resolve_safe_path(tmp_path, escape)


def test_sanitize_prompt_text_limits() -> None:
    with pytest.raises(SecurityValidationError):
        sanitize_prompt_text("a" * 50, max_length=20)


def test_protocol_runtime_checkable_contracts() -> None:
    runtime = ObservabilityRuntime()
    router = FreeFirstRouter(telemetry=runtime)
    assert isinstance(runtime, TelemetrySink)
    assert all(isinstance(p, AIProvider) for p in router.providers)
    assert BatchPromptRequest(requests=(PromptRequest(prompt="hi"),)).max_concurrency == 4
    assert AuditRequest().target_path == "python-2027"
    runtime.shutdown()


def test_no_blocking_calls_inside_async_functions() -> None:
    pkg_root = Path(__file__).resolve().parents[1]
    targets = list((pkg_root / "src" / "ai2027_protocol").glob("*.py")) + [
        pkg_root / "workers" / "python-worker" / "src" / "entry.py"
    ]
    violations: list[str] = []
    for path in targets:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                        owner = sub.func.value.id if isinstance(sub.func.value, ast.Name) else ""
                        if (owner == "time" and sub.func.attr == "sleep") or (
                            owner in {"requests", "urllib"}
                        ):
                            violations.append(
                                f"{path.name}:{node.name} calls {owner}.{sub.func.attr}"
                            )
    assert violations == []
