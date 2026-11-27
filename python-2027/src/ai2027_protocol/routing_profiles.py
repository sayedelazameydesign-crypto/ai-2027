"""Capability routing profiles: the Model Router's decision layer.

The measured problem
--------------------
The Free-First router in :mod:`.router` answers one question — *which provider
can serve this call for a proven-zero cost?* It does not answer the question
the cognitive architecture asks first: *which capability does this step
need?* Without that, every call looks identical to the router, so a deep
diagnosis and a one-line classification are served by the same path, and the
free-tier budget is spent on the wrong steps.

What this module provides
-------------------------
A declarative, reviewable mapping from **task type / capability** to a profile
that declares:

* which **capabilities** the profile serves (a closed vocabulary, so a typo
  cannot look like a capability);
* an ordered **provider preference** (kinds only — never concrete model IDs);
* the **budget policy** for the profile, which is free-first by default;
* the **approvals** the profile's execution requires;
* the **fallback profile** used when the primary is unavailable.

Profiles are loaded from ``config/routing_profiles.yaml``.

Dependency honesty
------------------
The configuration layer is stdlib-only. PyYAML is **not** a declared dependency
of this project, and adding it to parse one config file would break the
zero-dependency posture that the root ``requirements.txt`` and the CI AST
gates protect. The YAML **subset** parser therefore lives in
:mod:`.yaml_subset` — a generic, self-contained module — and is re-exported here
as :func:`parse_simple_yaml`. It supports exactly the shapes the shipped config
uses: block mappings, block sequences of scalars, quoted or bare scalars,
inline comments, and ``#``-style comments. It refuses anchors, aliases, tags,
flow collections and multi-line scalars by simply not supporting them, and a
document that needs them fails to parse instead of being silently misread.

Scope of this layer (P1)
------------------------
This module is a pure decision engine: it loads profiles, validates them against
the free-first policy, and answers *"which profile should serve this task?"*.
It does not call providers. Wiring the selected profile into
:class:`.router.FreeFirstRouter` is a later phase, so that the router's
existing budget, timeout, circuit-breaking and failover behaviour is unchanged
by this addition.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .yaml_subset import Jsonish, YamlSubsetError
from .yaml_subset import parse_simple_yaml as parse_simple_yaml  # re-exported for the package API

#: Repository root, derived from this file's location:
#: ``python-2027/src/ai2027_protocol/routing_profiles.py`` -> parents[3].
REPO_ROOT: Path = Path(__file__).resolve().parents[3]

#: Default location of the routing profile configuration.
DEFAULT_CONFIG_PATH: Path = REPO_ROOT / "config" / "routing_profiles.yaml"

#: The closed vocabulary of capability tags. A profile may only declare tags
#: from this set, so a typo in the config is a load error rather than a
#: capability that silently matches nothing.
CAPABILITY_VOCABULARY: frozenset[str] = frozenset(
    {
        "classify",
        "summarize",
        "extract",
        "reason",
        "diagnose",
        "compare",
        "code",
        "repair",
        "refactor",
        "test-author",
        "plan",
        "orchestrate",
        "replan",
        "verify",
        "inspect",
        "audit",
        "embed",
        "retrieve",
        "store",
        "vision",
        "extract-document",
        "caption",
    }
)

#: Provider kinds accepted in ``preferred_providers``. This mirrors
#: ``contracts.ProviderKind``; the package test suite asserts the two stay in
#: sync, because a profile naming a kind the router cannot build is a load
#: error that would otherwise surface as a runtime surprise.
SUPPORTED_PROVIDER_KINDS: frozenset[str] = frozenset(
    {
        "ollama",
        "gemini",
        "nvidia",
        "groq",
        "openrouter",
        "omnirouters",
        "qwen",
        "deterministic",
    }
)

#: Task-text signals mapped to a profile, most specific first. The order is
#: load-bearing: ``coding`` is checked before ``reason`` because "debug this
#: function" is a coding task that also mentions reasoning.
_TEXT_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "coding",
        re.compile(
            r"\b(code|coding|bug|broken|fix|repair|refactor|test|patch|"
            r"debug|implement|function|module|script)\b",
            re.I,
        ),
        "coding signals in the goal",
    ),
    (
        "planning",
        re.compile(
            r"\b(plan|planner|orchestrat|pipeline|steps?|workflow|"
            r"resume|checkpoint)\b",
            re.I,
        ),
        "multi-step orchestration signals",
    ),
    (
        "verification",
        re.compile(
            r"\b(verif|verify|audit|prove|check|validat|lint|regression)\b",
            re.I,
        ),
        "verification signals",
    ),
    (
        "memory",
        re.compile(
            r"\b(memory|recall|remember|retrieve|store|index|embedding)\b",
            re.I,
        ),
        "memory and retrieval signals",
    ),
    (
        "vision",
        re.compile(
            r"\b(image|pdf|ocr|document|screenshot|caption|vision|figure)\b",
            re.I,
        ),
        "document and vision signals",
    ),
    (
        "reason",
        re.compile(
            r"\b(reason|analy[sz]e|analysis|diagnos|compare|decide|why|"
            r"root cause)\b",
            re.I,
        ),
        "reasoning signals",
    ),
)


def text_rules_are_well_formed() -> bool:
    """Return True when the text-matching rules are internally consistent.

    A rule that names a profile the configuration does not declare would fail
    only at match time, deep inside a run, with a profile the operator never
    wrote. This check turns that into a load-time fact: every rule must have a
    profile name, a non-empty pattern, a unique profile name, and a profile
    that the shipped configuration actually declares.
    """
    try:
        known = load_routing_registry().names()
    except RoutingConfigError:
        return False
    seen: set[str] = set()
    for profile_name, pattern, _reason in _TEXT_RULES:
        if not profile_name or not pattern.pattern:
            return False
        if profile_name in seen:
            return False
        seen.add(profile_name)
        if profile_name not in known:
            return False
    return True


class RoutingConfigError(RuntimeError):
    """Raised when the routing configuration is missing or invalid."""


class UnknownProfileError(RoutingConfigError):
    """Raised when a profile name is not present in the registry."""


class UnknownTaskTypeError(RoutingConfigError):
    """Raised when strict selection is asked for an unknown task type."""


@dataclass(frozen=True)
class BudgetPolicy:
    """The spend policy attached to one profile."""

    max_spend_usd: float = 0.0
    allow_unknown_cost: bool = False

    @property
    def is_free_first(self) -> bool:
        """Return True only when the profile promises a proven-zero spend."""
        return self.max_spend_usd == 0.0 and not self.allow_unknown_cost


@dataclass(frozen=True)
class RoutingProfile:
    """One validated capability profile declared in the configuration."""

    name: str
    description: str
    capabilities: frozenset[str]
    preferred_providers: tuple[str, ...]
    allow_remote: bool
    max_tokens: int
    temperature: float
    budget: BudgetPolicy
    fallback_profile: str
    required_approvals: frozenset[str]
    notes: str = ""


@dataclass(frozen=True)
class RoutingDecision:
    """The outcome of a selection: which profile, and why."""

    profile: RoutingProfile
    task_type: str
    reason: str
    chain: tuple[str, ...] = ()

    @property
    def is_fallback(self) -> bool:
        """Return True when the decision was reached through failover."""
        return len(self.chain) > 1


def _require_mapping(value: Jsonish, where: str) -> Mapping[str, Jsonish]:
    """Return ``value`` if it is a mapping, else raise a readable error."""
    if not isinstance(value, dict):
        raise RoutingConfigError(f"{where} must be a mapping, got {type(value).__name__}")
    return value


def _as_string_tuple(value: Jsonish, where: str) -> tuple[str, ...]:
    """Coerce a list of scalars into a tuple of strings."""
    if value is None:
        return ()
    if isinstance(value, (str, int, float, bool)):
        raise RoutingConfigError(f"{where} must be a list of strings, got a scalar")
    if not isinstance(value, list):
        raise RoutingConfigError(f"{where} must be a list of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise RoutingConfigError(f"{where} must contain only strings, got {item!r}")
        result.append(item)
    return tuple(result)


def _build_profile(name: str, raw: Mapping[str, Jsonish], source: Path) -> RoutingProfile:
    """Validate one profile mapping and build a :class:`RoutingProfile`."""
    where = f"profile {name!r} in {source}"
    unknown_keys = set(raw) - {
        "description",
        "capabilities",
        "preferred_providers",
        "allow_remote",
        "max_tokens",
        "temperature",
        "budget",
        "fallback_profile",
        "approvals",
        "notes",
    }
    if unknown_keys:
        raise RoutingConfigError(f"{where}: unknown keys {sorted(unknown_keys)}")

    capabilities_raw = raw.get("capabilities")
    if not isinstance(capabilities_raw, list) or not capabilities_raw:
        raise RoutingConfigError(f"{where}: 'capabilities' must be a non-empty list")
    capabilities: set[str] = set()
    for capability in capabilities_raw:
        if not isinstance(capability, str) or capability not in CAPABILITY_VOCABULARY:
            raise RoutingConfigError(
                f"{where}: capability {capability!r} is not in the closed vocabulary"
            )
        capabilities.add(capability)

    providers = _as_string_tuple(raw.get("preferred_providers"), f"{where}: preferred_providers")
    if not providers:
        raise RoutingConfigError(f"{where}: 'preferred_providers' must not be empty")
    for provider in providers:
        if provider not in SUPPORTED_PROVIDER_KINDS:
            raise RoutingConfigError(
                f"{where}: provider kind {provider!r} is not supported by the router"
            )

    budget_raw = _require_mapping(raw.get("budget", {}), f"{where}: budget")
    max_spend = budget_raw.get("max_spend_usd", 0.0)
    if not isinstance(max_spend, (int, float)) or isinstance(max_spend, bool):
        raise RoutingConfigError(f"{where}: budget.max_spend_usd must be a number")
    if max_spend < 0.0:
        raise RoutingConfigError(f"{where}: budget.max_spend_usd must not be negative")
    allow_unknown = budget_raw.get("allow_unknown_cost", False)
    if not isinstance(allow_unknown, bool):
        raise RoutingConfigError(f"{where}: budget.allow_unknown_cost must be a boolean")
    budget = BudgetPolicy(float(max_spend), allow_unknown)

    max_tokens = raw.get("max_tokens", 1024)
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
        raise RoutingConfigError(f"{where}: max_tokens must be a positive integer")
    temperature = raw.get("temperature", 0.2)
    if not isinstance(temperature, (int, float)) or isinstance(temperature, bool):
        raise RoutingConfigError(f"{where}: temperature must be a number")
    if not 0.0 <= float(temperature) <= 1.5:
        raise RoutingConfigError(f"{where}: temperature must be within 0.0..1.5")

    approvals_raw = raw.get("approvals")
    approvals_map = _require_mapping(
        approvals_raw if approvals_raw is not None else {},
        f"{where}: approvals",
    )
    required_approvals = frozenset(
        _as_string_tuple(approvals_map.get("required"), f"{where}: approvals.required")
    )

    fallback = raw.get("fallback_profile")
    if not isinstance(fallback, str) or not fallback:
        raise RoutingConfigError(f"{where}: fallback_profile must be a non-empty string")

    return RoutingProfile(
        name=name,
        description=str(raw.get("description", "")),
        capabilities=frozenset(capabilities),
        preferred_providers=providers,
        allow_remote=bool(raw.get("allow_remote", False)),
        max_tokens=max_tokens,
        temperature=float(temperature),
        budget=budget,
        fallback_profile=fallback,
        required_approvals=required_approvals,
        notes=str(raw.get("notes", "")),
    )


class RoutingRegistry:
    """Validated set of routing profiles with capability-based selection."""

    def __init__(
        self,
        profiles: Iterable[RoutingProfile],
        *,
        default_profile: str,
        version: str,
        source: Path | None = None,
    ) -> None:
        """Build a registry, checking the free-first policy on every profile."""
        by_name: dict[str, RoutingProfile] = {}
        for profile in profiles:
            if profile.name in by_name:
                raise RoutingConfigError(f"duplicate profile name {profile.name!r}")
            if not profile.budget.is_free_first:
                raise RoutingConfigError(
                    f"profile {profile.name!r} is not free-first "
                    f"(max_spend_usd={profile.budget.max_spend_usd}, "
                    f"allow_unknown_cost={profile.budget.allow_unknown_cost}); "
                    "a paid or unknown-cost profile must not be added without an "
                    "explicit operator authorisation recorded in its notes"
                )
            by_name[profile.name] = profile
        if not by_name:
            raise RoutingConfigError("the routing configuration declares no profiles")
        if default_profile not in by_name:
            raise RoutingConfigError(f"default_profile {default_profile!r} is not declared")
        self._profiles = by_name
        self._default_profile = default_profile
        self._version = version
        self._source = source

    @property
    def version(self) -> str:
        """Return the configuration version the registry was built from."""
        return self._version

    @property
    def source(self) -> Path | None:
        """Return the file the registry was loaded from, if any."""
        return self._source

    @property
    def default_profile(self) -> RoutingProfile:
        """Return the profile used when nothing more specific matches."""
        return self._profiles[self._default_profile]

    def names(self) -> tuple[str, ...]:
        """Return every profile name, sorted for stable reporting."""
        return tuple(sorted(self._profiles))

    def get(self, name: str) -> RoutingProfile:
        """Return profile ``name`` or raise :class:`UnknownProfileError`."""
        try:
            return self._profiles[name]
        except KeyError as exc:
            raise UnknownProfileError(
                f"no routing profile named {name!r} (known: {', '.join(self.names())})"
            ) from exc

    def capabilities(self) -> frozenset[str]:
        """Return every capability tag any profile declares."""
        all_capabilities: set[str] = set()
        for profile in self._profiles.values():
            all_capabilities.update(profile.capabilities)
        return frozenset(all_capabilities)

    def select(self, task_type: str, *, strict: bool = False) -> RoutingDecision:
        """Select the profile for ``task_type``.

        With ``strict=False`` an unknown task type falls back to the default
        profile and says so. With ``strict=True`` it raises
        :class:`UnknownTaskTypeError` instead of guessing.
        """
        if task_type in self._profiles:
            return RoutingDecision(
                profile=self._profiles[task_type],
                task_type=task_type,
                reason=f"direct match on task type {task_type!r}",
            )
        if strict:
            raise UnknownTaskTypeError(
                f"no routing profile for task type {task_type!r} (known: {', '.join(self.names())})"
            )
        return RoutingDecision(
            profile=self._profiles[self._default_profile],
            task_type=task_type,
            reason=f"unknown task type {task_type!r}; fell back to default profile",
        )

    def match_from_text(self, task_text: str) -> RoutingDecision:
        """Infer a profile from goal text using the ordered signal rules."""
        for profile_name, pattern, reason in _TEXT_RULES:
            if pattern.search(task_text):
                profile = self.get(profile_name)
                return RoutingDecision(
                    profile=profile,
                    task_type=profile_name,
                    reason=f"task text matched {reason}",
                )
        return RoutingDecision(
            profile=self.default_profile,
            task_type=self._default_profile,
            reason="task text matched no capability signal; using the default profile",
        )

    def resolve(
        self,
        task_type: str | None = None,
        task_text: str | None = None,
        *,
        strict: bool = False,
    ) -> RoutingDecision:
        """Resolve a task to a profile, from an explicit type or from text."""
        if task_type is not None:
            return self.select(task_type, strict=strict)
        if task_text is not None:
            return self.match_from_text(task_text)
        return RoutingDecision(
            profile=self.default_profile,
            task_type=self._default_profile,
            reason="no task type or text supplied; using the default profile",
        )

    def with_failover(
        self,
        profile_name: str,
        unavailable: Iterable[str] = (),
        *,
        max_hops: int = 5,
    ) -> RoutingDecision:
        """Follow the fallback chain past any unavailable profiles.

        The returned decision's ``chain`` records every profile considered, in
        order, so a failover is auditable: the report can show that the primary
        was unavailable and which profile actually served the step.
        """
        unavailable_set = set(unavailable)
        chain: list[str] = []
        current = self.get(profile_name)
        hops = 0
        while True:
            if current.name in chain:
                raise RoutingConfigError(
                    f"fallback chain for {profile_name!r} loops back to {current.name!r}"
                )
            chain.append(current.name)
            if current.name not in unavailable_set:
                return RoutingDecision(
                    profile=current,
                    task_type=current.name,
                    reason=f"profile {profile_name!r} unavailable; served by {current.name!r}",
                    chain=tuple(chain),
                )
            if hops >= max_hops:
                raise RoutingConfigError(
                    f"fallback chain for {profile_name!r} exceeded {max_hops} hops"
                )
            hops += 1
            current = self.get(current.fallback_profile)


def load_routing_registry(path: Path | None = None) -> RoutingRegistry:
    """Load and validate the routing registry from ``path`` (default: shipped)."""
    source = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not source.is_file():
        raise RoutingConfigError(f"routing configuration not found: {source}")
    try:
        document = parse_simple_yaml(source.read_text(encoding="utf-8"))
    except YamlSubsetError as exc:
        # The parser owns its own error type; callers of the registry see the
        # domain error, so a malformed document is reported the same way as any
        # other invalid configuration.
        raise RoutingConfigError(f"{source}: {exc}") from exc
    profiles_raw = _require_mapping(document.get("profiles"), f"{source}: profiles")
    if not profiles_raw:
        raise RoutingConfigError(f"{source}: 'profiles' must not be empty")
    profiles = [
        _build_profile(name, _require_mapping(raw, f"{source}: profile {name!r}"), source)
        for name, raw in profiles_raw.items()
    ]
    default_profile = document.get("default_profile")
    if not isinstance(default_profile, str) or not default_profile:
        raise RoutingConfigError(f"{source}: 'default_profile' must name a profile")
    version = document.get("version")
    if not isinstance(version, str) or not version:
        raise RoutingConfigError(f"{source}: 'version' must be a non-empty string")
    return RoutingRegistry(
        profiles,
        default_profile=default_profile,
        version=version,
        source=source,
    )
