"""LLM-backed agents for the fleet — real model calls, unchanged verification rules.

Design constraints (all of them deliberate):

1. **The core stays dependency-free.** No provider SDK is imported here. A
   backend is any object with a *synchronous* ``complete`` method, so the
   pydantic/httpx providers in ``python-2027`` are injected from the outside via
   :class:`AsyncProviderBridge` rather than imported. ``scripts/fleet_guard.py
   imports`` keeps this honest.
2. **The verifier remains the judge.** An LLM's output is a *claim*. This module
   writes it to disk and returns a relative :class:`ArtifactRef` with the digest
   it actually computed; the existing verifier then re-hashes the file and
   refuses any mismatch. A model that returns nothing, returns prose when code
   was required, or returns a truncated file **fails the step** exactly like any
   other agent — there is no "the model said it worked" path.
3. **Free-First.** The default backend is local Ollama over validated loopback
   HTTP (proven $0). Remote backends require an explicit HTTPS endpoint and a
   credential from the environment, and the credential never reaches a log,
   an event, or an artifact.
4. **Bounded.** Output size, timeout, prompt size and token count all have hard
   caps; a runaway model cannot fill the disk or hang a wave forever.

Typical use::

    from fleet2027.llm import LLMAgent, OllamaBackend

    agent = LLMAgent(backend=OllamaBackend(model="qwen3.5:0.8b"))
    registry.register(agent.spec(), agent.handler)

    step = PlanStep(
        step_id="write-module",
        agent=agent.spec().name,
        action="write app.py from a prompt",
        inputs={"prompt": "write add(a, b) with a docstring", "out": "app.py",
                "language": "python"},
        verification=(EvidenceKind.ARTIFACT_SHA256.value,
                      EvidenceKind.OUTPUT_NON_EMPTY.value),
    )
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .contracts import (
    AgentSpec,
    ArtifactRef,
    ContractError,
    CostTier,
    EvidenceKind,
    StepResult,
    sha256_text,
)
from .registry import HandlerContext
from .runner import StepFailure

#: Hard caps — a model must never be able to hang a wave or fill the disk.
MAX_OUTPUT_BYTES = 256_000
MAX_PROMPT_CHARS = 16_000
#: Sent on every request. Several providers sit behind a WAF that rejects the
#: default urllib signature ("Python-urllib/3.x") before the API key is read,
#: so the agent names the project instead.
USER_AGENT = "ai-2027-fleet/1.0 (+https://github.com/sayedelazameydesign-crypto/ai-2027)"

MAX_TIMEOUT_S = 300.0
MIN_TIMEOUT_S = 0.5

#: Prose that a model emits instead of an answer; refused when code is required.
_REFUSAL_MARKERS = (
    "i cannot",
    "i can't",
    "i'm sorry",
    "as an ai",
    "unable to assist",
)


class LLMError(RuntimeError):
    """Raised when a backend fails. Retryable: the runner treats it as a failure."""


class BackendNotConfigured(LLMError):
    """Raised when a backend has no endpoint/credential to talk to."""


@dataclass(frozen=True)
class LLMRequest:
    """One bounded, inspectable completion request."""

    prompt: str
    system: str = ""
    temperature: float = 0.2
    max_tokens: int = 512
    timeout_s: float = 60.0

    def __post_init__(self) -> None:
        """Enforce the caps at construction time, not inside the transport."""
        if not self.prompt.strip():
            raise ContractError("LLMRequest.prompt must not be blank")
        if len(self.prompt) > MAX_PROMPT_CHARS:
            raise ContractError(f"prompt exceeds {MAX_PROMPT_CHARS} characters")
        if not 0.0 <= self.temperature <= 1.5:
            raise ContractError("temperature must be within 0..1.5")
        if not 1 <= self.max_tokens <= 4096:
            raise ContractError("max_tokens must be within 1..4096")
        if not MIN_TIMEOUT_S <= self.timeout_s <= MAX_TIMEOUT_S:
            raise ContractError(f"timeout_s must be within {MIN_TIMEOUT_S}..{MAX_TIMEOUT_S}")


@dataclass(frozen=True)
class LLMResponse:
    """What a backend hands back: text plus the provenance the audit log needs."""

    text: str
    backend: str
    model: str
    latency_ms: float = 0.0
    prompt_sha256: str = ""
    raw_bytes: int = 0

    def __post_init__(self) -> None:
        """Refuse an empty body early — an empty answer is never a success."""
        if not self.text.strip():
            raise LLMError("backend returned an empty completion")


@runtime_checkable
class LLMBackend(Protocol):
    """Synchronous completion backend. Anything with this shape can be injected."""

    @property
    def name(self) -> str:
        """Short, log-safe identifier such as ``ollama`` or ``openai-compat``."""
        ...

    def is_configured(self) -> bool:
        """Return whether the backend has what it needs to run."""
        ...

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Perform one bounded completion. Raises :class:`LLMError` on failure."""
        ...


# --------------------------------------------------------------------------- #
# Transport helpers (stdlib urllib — no httpx here)
# --------------------------------------------------------------------------- #


def _validate_endpoint(raw_url: str, *, allow_loopback_http: bool) -> str:
    """Allow HTTPS anywhere, plain HTTP only for loopback (mirrors security.py)."""
    candidate = raw_url.strip().rstrip("/")
    if not candidate:
        raise ContractError("endpoint URL must not be empty")
    parsed = urllib.parse.urlparse(candidate)
    if parsed.username or parsed.password:
        raise ContractError("endpoint URL must not embed credentials")
    if parsed.query or parsed.fragment:
        raise ContractError("endpoint URL must not include query strings or fragments")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ContractError("endpoint URL must specify a hostname")
    loopback = host in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme == "http":
        if not (allow_loopback_http and loopback):
            raise ContractError("plain HTTP is allowed only for loopback hosts")
    elif parsed.scheme != "https":
        raise ContractError("endpoint URL must use https or loopback http")
    return candidate


def _post_json(
    url: str, payload: Mapping[str, Any], headers: Mapping[str, str], *, timeout_s: float
) -> Mapping[str, Any]:
    """POST JSON and parse a bounded JSON response. Never follows redirects."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")  # noqa: S310 - validated URL
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    # api.groq.com is behind Cloudflare, which answers the default urllib agent
    # with `403 error code: 1010` before Groq sees the key. The same key works
    # from curl, which is what made this look like a bad credential for a whole
    # debugging round. Callers may override via `headers`.
    request.add_header("User-Agent", USER_AGENT)
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310
            raw = response.read(MAX_OUTPUT_BYTES * 4)
    except urllib.error.HTTPError as exc:  # pragma: no cover - network path
        detail = exc.read(2_000).decode("utf-8", "replace")
        raise LLMError(f"http_{exc.code}: {detail[:300]}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:  # pragma: no cover
        raise LLMError(f"transport_error: {exc}") from exc
    try:
        parsed = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:  # pragma: no cover - network path
        raise LLMError(f"invalid_json: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise LLMError("invalid_payload: expected a JSON object")
    return parsed


def _extract_code(text: str, language: str) -> str:
    """Pull the first fenced block for ``language``; fall back to the whole text.

    Models routinely wrap code in ```python … ```. We extract it rather than
    writing the fences into a ``.py`` file, but we never *invent* content: if
    there is no fence, the text is used verbatim and the verifier decides.
    """
    if not language:
        return text.strip()
    pattern = re.compile(r"```[ \t]*" + re.escape(language) + r"[ \t]*\n(.*?)```", re.S | re.I)
    match = pattern.search(text)
    if match:
        return match.group(1).strip("\n")
    generic = re.compile(r"```[ \t]*[A-Za-z0-9_+-]*[ \t]*\n(.*?)```", re.S)
    match = generic.search(text)
    if match:
        return match.group(1).strip("\n")
    return text.strip()


def _looks_like_refusal(text: str) -> bool:
    """Detect a model refusing the task — that is a failure, not an artifact."""
    lowered = text.strip().lower()
    if len(lowered) > 400:
        return False
    return any(marker in lowered for marker in _REFUSAL_MARKERS)


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #


@dataclass
class OllamaBackend:
    """Local Ollama over validated loopback HTTP — the Free-First default."""

    base_url: str = field(default_factory=lambda: os.environ.get("OLLAMA_BASE_URL", ""))
    model: str = field(default_factory=lambda: os.environ.get("OLLAMA_MODEL", "qwen3.5:0.8b"))
    num_ctx: int = 2048

    @property
    def name(self) -> str:
        """Backend identifier used in events and evidence."""
        return "ollama"

    def is_configured(self) -> bool:
        """Return whether a loopback endpoint is configured."""
        return bool(self.base_url.strip())

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Call ``/api/chat`` and return the message content."""
        if not self.is_configured():
            raise BackendNotConfigured("OLLAMA_BASE_URL is not set (expected loopback http)")
        endpoint = _validate_endpoint(self.base_url, allow_loopback_http=True)
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})
        started = time.perf_counter()
        payload = _post_json(
            f"{endpoint}/api/chat",
            {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": request.temperature,
                    "num_predict": request.max_tokens,
                    "num_ctx": self.num_ctx,
                },
            },
            {},
            timeout_s=request.timeout_s,
        )
        latency = (time.perf_counter() - started) * 1000.0
        message = payload.get("message")
        text = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(text, str):
            raise LLMError("invalid_payload: Ollama response has no message.content")
        return LLMResponse(
            text=text,
            backend=self.name,
            model=self.model,
            latency_ms=latency,
            prompt_sha256=sha256_text(request.prompt),
            raw_bytes=len(text.encode("utf-8")),
        )


@dataclass
class OpenAICompatibleBackend:
    """Any OpenAI-compatible ``/chat/completions`` endpoint (https only, key from env)."""

    endpoint_url: str
    model: str
    api_key_env: str
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    label: str = ""

    @property
    def name(self) -> str:
        """Backend identifier used in events and evidence.

        The label defaults to the wire protocol, but a rotated run needs to
        name the *tap*, not the protocol. Three taps all reporting
        "openai-compat" made a 24-sentence failure unreadable: there was no
        way to tell which provider produced which row.
        """
        return self.label or "openai-compat"

    def _api_key(self) -> str:
        return os.environ.get(self.api_key_env, "").strip()

    def is_configured(self) -> bool:
        """Return whether the credential variable is present."""
        return bool(self._api_key())

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Call the endpoint; the key travels only in a header, never in a log."""
        if not self.is_configured():
            raise BackendNotConfigured(f"{self.api_key_env} is not set")
        endpoint = _validate_endpoint(self.endpoint_url, allow_loopback_http=False)
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})
        headers = {
            "Authorization": f"Bearer {self._api_key()}",
            **dict(self.extra_headers),
        }
        started = time.perf_counter()
        payload = _post_json(
            endpoint,
            {
                "model": self.model,
                "messages": messages,
                "temperature": request.temperature,
                "max_tokens": request.max_tokens,
            },
            headers,
            timeout_s=request.timeout_s,
        )
        latency = (time.perf_counter() - started) * 1000.0
        choices = payload.get("choices")
        text = None
        finish_reason = ""
        detail = "no choices[0].message.content"
        if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
            choice = choices[0]
            finish_reason = str(choice.get("finish_reason") or "")
            message = choice.get("message")
            if isinstance(message, Mapping):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    text = content
                elif isinstance(content, str) and content == "":
                    # Reasoning models can return an empty `content` and put the
                    # tokens elsewhere. `reasoning` is the answer for this run;
                    # an explicit empty string is still a real (bad) answer.
                    reasoning = message.get("reasoning")
                    if isinstance(reasoning, str) and reasoning.strip():
                        text = reasoning
                    else:
                        detail = "choices[0].message.content is empty"
                refusal = message.get("refusal")
                if text is None and isinstance(refusal, str) and refusal.strip():
                    detail = f"model refused: {refusal[:120]}"
        if not isinstance(text, str):
            # State which of the many shapes actually came back. "invalid
            # payload" alone sent a whole debugging round down the wrong path.
            keys = sorted(payload) if isinstance(payload, Mapping) else []
            used = payload.get("usage") if isinstance(payload, Mapping) else None
            if finish_reason == "length":
                # Truncated, not malformed. The caller can raise the budget;
                # calling this "invalid payload" hides an actionable cause.
                raise LLMError(
                    "truncated: finish_reason='length' and no content was written; "
                    f"usage={used}"
                )
            raise LLMError(
                f"invalid_payload: {detail}; finish_reason={finish_reason!r}; "
                f"top_level_keys={keys}; usage={used}"
            )
        return LLMResponse(
            text=text,
            backend=self.name,
            model=self.model,
            latency_ms=latency,
            prompt_sha256=sha256_text(request.prompt),
            raw_bytes=len(text.encode("utf-8")),
        )


@dataclass
class ScriptedBackend:
    """Deterministic backend for tests, CI and the gate — never touches a network.

    ``script`` is consumed in order; a string is returned as the completion and
    an exception instance is raised instead. When the list has exactly **one**
    entry it is treated as a sticky stand-in: retries get the same answer, which
    is what a deterministic fake model must do. With two or more entries the
    list is a queue, and running past its end raises ``LLMError`` so a test
    cannot silently exercise a code path it did not script.
    """

    script: list[Any] = field(default_factory=list)
    model: str = "scripted-v1"
    calls: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        """Backend identifier used in events and evidence."""
        return "scripted"

    def is_configured(self) -> bool:
        """A scripted backend always has what it needs."""
        return True

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Return the next scripted step, or raise the scripted error."""
        self.calls.append(request.prompt)
        if not self.script:
            raise LLMError("scripted backend has no script")
        index = len(self.calls) - 1
        if index >= len(self.script):
            if len(self.script) == 1:
                index = 0  # sticky single answer
            else:
                raise LLMError("scripted backend is exhausted")
        item = self.script[index]
        if isinstance(item, BaseException):
            raise item
        text = str(item)
        return LLMResponse(
            text=text,
            backend=self.name,
            model=self.model,
            latency_ms=0.4,
            prompt_sha256=sha256_text(request.prompt),
            raw_bytes=len(text.encode("utf-8")),
        )


# --------------------------------------------------------------------------- #
# Bridge for python-2027 async providers (injected, never imported)
# --------------------------------------------------------------------------- #


def call_async_sync(factory: Callable[[], Awaitable[Any]]) -> Any:
    """Run an awaitable to completion from a synchronous handler thread.

    Handlers execute on runner worker threads that have no running event loop,
    so a private loop is created per call and always closed — no leaked loops,
    no "coroutine was never awaited" warnings.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # Expected: a runner worker thread has no loop. That is the case we
        # support, so there is nothing to report here.
        running_loop_found = False
    else:  # pragma: no cover - defensive: handlers are never async
        running_loop_found = True
    if running_loop_found:
        raise LLMError("call_async_sync cannot be used inside a running event loop")
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(factory())
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


def _describe_provider_failure(exc: BaseException) -> str:
    """Turn a provider exception into one sentence that names the cause.

    A bare `LLMError()` — raised by a provider that has no message — used to
    reach the report as `gemini-2.5-flash failed: ` and the run looked like a
    model problem. The code, the message and the attempts are all available on
    the exception; a failure that hides them costs a debugging round.
    """
    parts: list[str] = []
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        parts.append(code)
    text = str(exc).strip()
    if text and text not in parts:
        parts.append(text)
    attempts = getattr(exc, "attempts", None)
    if isinstance(attempts, (list, tuple)) and attempts:
        named = []
        for attempt in attempts:
            provider = getattr(attempt, "provider", None) or getattr(attempt, "name", None)
            error = getattr(attempt, "error", None) or getattr(attempt, "code", None)
            named.append(f"{provider}:{error}" if provider else str(attempt)[:80])
        parts.append("attempts=" + ", ".join(str(n) for n in named[:4]))
    if not parts:
        parts.append(type(exc).__name__)
    return ": ".join(parts[:1]) + ((" — " + "; ".join(parts[1:])) if len(parts) > 1 else "")


@dataclass
class AsyncProviderBridge:
    """Adapt a ``python-2027`` ``AIProvider`` (async ``complete``) to a backend.

    The provider is passed in by the caller, so ``fleet2027`` never imports
    pydantic, httpx or the protocol package. ``request_factory`` builds the
    provider-native request object; the default is provided by the caller too.
    """

    provider: Any
    prompt_builder: Callable[[LLMRequest], Any]
    name_hint: str = "python-2027-provider"

    @property
    def name(self) -> str:
        """Backend identifier used in events and evidence."""
        return self.name_hint

    def is_configured(self) -> bool:
        """Delegate readiness to the provider."""
        check = getattr(self.provider, "is_configured", None)
        return bool(check()) if callable(check) else True

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Await the provider's async completion on a private loop."""
        started = time.perf_counter()
        native = self.prompt_builder(request)
        try:
            text = call_async_sync(
                lambda: self.provider.complete(native, timeout_s=request.timeout_s)
            )
        except Exception as exc:  # noqa: BLE001 - provider errors are opaque to the fleet
            # The provider raises its own exception type (ProviderError with a
            # `.code`, AllProvidersFailedError carrying every attempt). Letting
            # it through unchanged produced `gemini-2.5-flash failed:` with
            # nothing after the colon — the class name survived and the reason
            # was thrown away. Both halves are kept here, so a failed sentence
            # says which code failed and why.
            raise LLMError(_describe_provider_failure(exc)) from exc
        latency = (time.perf_counter() - started) * 1000.0
        if not isinstance(text, str):
            raise LLMError("provider returned a non-string completion")
        model = getattr(self.provider, "model", "unknown")
        return LLMResponse(
            text=text,
            backend=self.name,
            model=str(model),
            latency_ms=latency,
            prompt_sha256=sha256_text(request.prompt),
            raw_bytes=len(text.encode("utf-8")),
        )


# --------------------------------------------------------------------------- #
# The agent
# --------------------------------------------------------------------------- #


#: Free tiers that count tokens per minute (not only requests per minute).
#: Groq publishes 8,000 TPM for gpt-oss-120b, and it reserves `max_tokens`
#: before the call: three 1,536-token calls with a ~700-token prompt fit in one
#: minute, four do not. Without pacing the run sends everything at once and the
#: provider answers 429 to sentences that would have succeeded a minute later.
_TPM_LIMIT = {"groq": 8_000}
#: Sliding window of (monotonic time, reserved tokens) per backend name.
_token_window: dict[str, list[tuple[float, int]]] = {}


def _wait_for_token_budget(name: str, reserved: int, *, timeout_s: float = 90.0) -> None:
    """Sleep until `reserved` more tokens fit inside the per-minute window.

    A rate limit is not a permanent failure: the same sentence succeeds after
    the window moves. Failing it instead costs the sentence for the whole run,
    which is why every free tap looked dead while only the pacing was wrong.
    """
    limit = _TPM_LIMIT.get(name)
    if not limit or reserved <= 0:
        return
    import time as _time

    deadline = _time.monotonic() + timeout_s
    while True:
        now = _time.monotonic()
        window = [item for item in _token_window.get(name, []) if now - item[0] < 60.0]
        _token_window[name] = window
        used = sum(tokens for _, tokens in window)
        if used + reserved <= limit:
            window.append((now, reserved))
            return
        oldest = min(window, key=lambda item: item[0])[0]
        sleep_for = max(0.05, 60.0 - (now - oldest) + 0.05)
        if _time.monotonic() + sleep_for > deadline:
            # The window cannot move fast enough inside this call's patience.
            # Returning lets the caller retry rather than block the whole run.
            return
        _time.sleep(sleep_for)


#: Status codes where a retry cannot produce a different answer.
_PERMANENT_HTTP = ("http_400", "http_401", "http_403", "http_404", "http_410", "http_429")


def _is_permanent(exc: LLMError) -> bool:
    """Whether the same request will fail the same way, so a retry is waste.

    429 is included on purpose: the daily free quota does not refill within a
    retry backoff, and spending two more requests to confirm it is how a
    50-request day disappears.

    `truncated` is not permanent — a larger budget can fix it.
    """
    text = str(exc)
    if text.startswith("truncated"):
        return False
    return any(code in text for code in _PERMANENT_HTTP)


@dataclass
class LLMAgent:
    """Wraps one LLM backend as a fleet agent whose output must survive verification."""

    backend: LLMBackend
    agent_name: str = "llm-writer"
    # The criteria the artifact is judged on belong in the instruction. A model
    # told only "write a file" is graded on rules it was never given: the first
    # real run scored usable=29% and the reasons were substance, relevance and
    # honesty, none of which appeared in this prompt. Stating them is not
    # gaming the ruler — a brief that hides its acceptance criteria is a worse
    # brief, and a person asking for work would state them too.
    system_prompt: str = (
        "You are a precise implementation agent. Return ONLY the requested file "
        "content inside a single fenced code block. No commentary, no apologies.\n"
        "The result is judged on these rules, so satisfy them deliberately:\n"
        "- substance: write real content for the reader. Never leave placeholder "
        "text, 'TODO', or an instruction to fill it in later.\n"
        "- completeness: cover the request at the length it deserves; a "
        "one-sentence answer to a large request is a refusal in disguise.\n"
        "- relevance: stay on the subject that was asked for, in its language. "
        "Name the subject in the first lines and use the words a reader would "
        "search for; a document that never says what it is about reads as off-topic.\n"
        "- honesty: do not invent sources, statistics or studies. If you state a "
        "limitation, state it plainly.\n"
        "- structure: a heading and a scannable layout, not a wall of text. For a "
        "report that means titled sections (ملخص / تحليل / توصيات, or Summary / "
        "Analysis / Recommendations) and a short list where items are parallel. "
        "Measured on the same content: paragraphs scored 0.72, the same text with "
        "titled sections and a list scored 0.93.\n"
        "- no filler: no 'in today's world' or 'it is important to note' padding."
    )
    default_language: str = ""
    capability: str = "llm-write"
    #: A reasoning model spends this budget before it writes `content`, so a
    #: budget too small returns finish_reason='length' with no answer. 1,024
    #: was not enough for gpt-oss-120b, and 2,048 made things worse for a
    #: different reason: Groq counts `max_tokens` against its 8,000-tokens-per-
    #: minute limit *before* the call is made, so 2,048 + ~700 prompt = 2,748
    #: tokens per call allows only two calls a minute. 1,536 fits three calls a
    #: minute and still leaves room for a short reasoning trace.
    max_tokens: int = 1536
    temperature: float = 0.2
    timeout_s: float = 120.0
    max_output_bytes: int = MAX_OUTPUT_BYTES
    cost_tier: CostTier = CostTier.LOCAL
    fallbacks: tuple[str, ...] = ()
    calls: int = 0

    def spec(self) -> AgentSpec:
        """Return the registration contract for this agent."""
        return AgentSpec(
            name=self.agent_name,
            role="write a real file from a model completion (verified, not trusted)",
            capabilities=(self.capability,),
            produces=("code", "document"),
            cost_tier=self.cost_tier,
            timeout_s=max(self.timeout_s + 15.0, 30.0),
            max_attempts=2,
            requires_evidence=(
                EvidenceKind.ARTIFACT_SHA256.value,
                EvidenceKind.OUTPUT_NON_EMPTY.value,
            ),
            fallbacks=self.fallbacks,
        )

    # -- handler ---------------------------------------------------------- #

    def handler(self, ctx: HandlerContext) -> StepResult:
        """Call the model, write the file, and report what *actually* happened."""
        prompt = str(ctx.inputs.get("prompt", "")).strip()
        if not prompt:
            raise StepFailure("step inputs.prompt is required for an LLM agent")
        relative_out = str(ctx.inputs.get("out", "")).strip()
        if not relative_out:
            raise StepFailure("step inputs.out (workspace-relative path) is required")
        language = str(ctx.inputs.get("language", self.default_language)).strip()
        require_code = bool(ctx.inputs.get("require_code", bool(language)))
        if not self.backend.is_configured():
            raise StepFailure(f"LLM backend {self.backend.name!r} is not configured")

        request = LLMRequest(
            prompt=prompt,
            system=str(ctx.inputs.get("system", self.system_prompt)),
            temperature=float(ctx.inputs.get("temperature", self.temperature)),
            max_tokens=int(ctx.inputs.get("max_tokens", self.max_tokens)),
            timeout_s=min(float(ctx.inputs.get("timeout_s", self.timeout_s)), MAX_TIMEOUT_S),
        )

        # The same request with headroom for a model that reasons before it
        # writes. `LLMRequest` validates the cap, so the doubling is clamped.
        doubled = LLMRequest(
            prompt=request.prompt,
            system=request.system,
            temperature=request.temperature,
            max_tokens=min(4096, request.max_tokens * 2),
            timeout_s=request.timeout_s,
        )

        target = (ctx.workspace_root / relative_out).resolve()
        root = ctx.workspace_root.resolve()
        if target != root and root not in target.parents:
            raise StepFailure(f"refused to write outside the workspace: {relative_out!r}")

        # The provider counts the reservation before the call, so the wait
        # belongs here, before the request leaves. A tap without a published
        # token limit (or a label that does not name one) is not paced at all.
        _wait_for_token_budget(
            self.backend.name.split(":")[0],
            len(prompt) // 4 + request.max_tokens,
            timeout_s=min(request.timeout_s, 90.0),
        )

        try:
            response = self.backend.complete(request)
        except LLMError as exc:
            # Truncation is the one failure a bigger budget can fix, so it is
            # retried once with one. Retrying it unchanged is what made a
            # reasoning model return the same truncated answer and burn the
            # free-tier calls that A0 exists to protect.
            if str(exc).startswith("truncated") and doubled.max_tokens > request.max_tokens:
                try:
                    response = self.backend.complete(doubled)
                except LLMError as retry_exc:
                    raise StepFailure(
                        f"{self.backend.name} failed: {retry_exc}",
                        permanent=False,
                    ) from retry_exc
            else:
                # A retry is only worth a free-tier request when the next reply
                # could differ. A spent daily quota, a retired model and a
                # rejected credential will answer the same way; retrying them
                # costs three calls where one would do. Measured with a backend
                # that always answers 429: 3 calls for a single sentence.
                raise StepFailure(
                    f"{self.backend.name} failed: {exc}",
                    permanent=_is_permanent(exc),
                ) from exc
        self.calls += 1

        content = _extract_code(response.text, language)
        if not content.strip():
            raise StepFailure("model returned no usable content")
        if require_code and _looks_like_refusal(content):
            raise StepFailure(f"model refused the task instead of producing code: {content[:120]!r}")
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_output_bytes:
            raise StepFailure(
                f"model output {len(encoded)} bytes exceeds cap {self.max_output_bytes}"
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded)
        artifact = ArtifactRef.from_file(
            str(target),
            kind="code" if language else "document",
            record_path=target.relative_to(root).as_posix(),
        )
        return StepResult(
            ok=True,
            summary=f"{response.backend}:{response.model} wrote {relative_out} "
                    f"({len(encoded)} B, {response.latency_ms:.0f} ms)",
            output=content[:1_000],
            artifacts=(artifact,),
            evidence={
                "backend": response.backend,
                "model": response.model,
                "prompt_sha256": response.prompt_sha256,
                "latency_ms": round(response.latency_ms, 2),
                "output_bytes": len(encoded),
                "output_sha256": artifact.sha256,
                "attempt": ctx.attempt,
                "called": True,
                # A2: when the backend is a router, the whole failover trail
                # travels with the result instead of living only in memory.
                "routing": self.routing_evidence(),
            },
            duration_ms=response.latency_ms,
        )

    # -- helpers ---------------------------------------------------------- #

    def verified_call_count(self) -> int:
        """Number of completions this instance actually issued."""
        return self.calls

    def routing_evidence(self) -> dict[str, Any]:
        """Return the backend's routing trail, or a plain "not routed" marker.

        Deliberately a method and not an attribute: a backend that grew routing
        later must not silently break an agent that was written before it.
        """
        provider = getattr(self.backend, "routing_evidence", None)
        if callable(provider):
            evidence = provider()
            if isinstance(evidence, dict):
                return evidence
        return {"routed": False}


def build_llm_registry(
    agent: LLMAgent, *, fallback_agent: LLMAgent | None = None
) -> tuple[Any, LLMAgent]:
    """Register an LLM agent (plus an optional fallback) in a fresh registry.

    Returns ``(registry, primary)``. The fallback is registered first so the
    primary's ``fallbacks`` chain can name it.
    """
    from .registry import AgentRegistry

    registry = AgentRegistry()
    if fallback_agent is not None:
        registry.register(fallback_agent.spec(), fallback_agent.handler)
    registry.register(agent.spec(), agent.handler)
    return registry, agent
