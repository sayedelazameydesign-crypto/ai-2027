"""Tests for the capability routing profiles (``ai2027_protocol.routing_profiles``).

These complement the dependency-free root suite in
``tests/test_routing_profiles.py``. The added value here is the cross-module
guard: the provider kinds declared by the configuration must stay in sync with
``contracts.ProviderKind``, because a profile naming a kind the router cannot
build is a load error that would otherwise surface only at run time.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai2027_protocol.contracts import ProviderKind
from ai2027_protocol.routing_profiles import (
    CAPABILITY_VOCABULARY,
    DEFAULT_CONFIG_PATH,
    REPO_ROOT,
    SUPPORTED_PROVIDER_KINDS,
    BudgetPolicy,
    RoutingConfigError,
    RoutingDecision,
    RoutingProfile,
    RoutingRegistry,
    UnknownProfileError,
    UnknownTaskTypeError,
    load_routing_registry,
    parse_simple_yaml,
    text_rules_are_well_formed,
)
from ai2027_protocol.yaml_subset import YamlSubsetError


def test_repo_root_and_default_config_path() -> None:
    assert REPO_ROOT == Path(__file__).resolve().parents[2]
    assert DEFAULT_CONFIG_PATH == REPO_ROOT / "config" / "routing_profiles.yaml"
    assert DEFAULT_CONFIG_PATH.is_file()


def test_provider_kinds_stay_in_sync_with_the_contract_enum() -> None:
    """A profile may only name provider kinds the router can actually build."""
    assert SUPPORTED_PROVIDER_KINDS == frozenset(kind.value for kind in ProviderKind)


def test_shipped_registry_loads_and_is_free_first() -> None:
    registry = load_routing_registry()
    assert registry.version == "1.0.0"
    assert registry.source == DEFAULT_CONFIG_PATH
    assert registry.names()
    for name in registry.names():
        assert registry.get(name).budget.is_free_first


def test_shipped_profiles_only_use_known_capabilities_and_providers() -> None:
    registry = load_routing_registry()
    for name in registry.names():
        profile = registry.get(name)
        assert profile.capabilities <= CAPABILITY_VOCABULARY
        assert set(profile.preferred_providers) <= SUPPORTED_PROVIDER_KINDS
        assert profile.preferred_providers


def test_shipped_profiles_do_not_name_concrete_models() -> None:
    registry = load_routing_registry()
    for name in registry.names():
        profile = registry.get(name)
        for value in list(profile.capabilities) + list(profile.preferred_providers):
            assert not any(char.isdigit() for char in value), f"{name}: {value}"


def test_every_shipped_fallback_chain_terminates() -> None:
    registry = load_routing_registry()
    for name in registry.names():
        seen: set[str] = set()
        current = registry.get(name)
        while current.name not in seen:
            seen.add(current.name)
            current = registry.get(current.fallback_profile)


def test_registry_declares_a_default_profile() -> None:
    registry = load_routing_registry()
    assert registry.default_profile.name in registry.names()


def test_registry_capabilities_union() -> None:
    registry = load_routing_registry()
    assert registry.capabilities() == frozenset(CAPABILITY_VOCABULARY)


def test_select_known_and_unknown_task_types() -> None:
    registry = load_routing_registry()
    decision = registry.select("coding")
    assert decision.profile.name == "coding"
    assert decision.chain == ()

    lenient = registry.select("unknown-task")
    assert lenient.profile.name == registry.default_profile.name
    assert "fell back" in lenient.reason


def test_strict_select_refuses_an_unknown_task_type() -> None:
    registry = load_routing_registry()
    with pytest.raises(UnknownTaskTypeError):
        registry.select("unknown-task", strict=True)


def test_get_refuses_an_unknown_profile() -> None:
    registry = load_routing_registry()
    with pytest.raises(UnknownProfileError):
        registry.get("nope")


def test_resolve_from_type_text_and_nothing() -> None:
    registry = load_routing_registry()
    assert registry.resolve(task_type="memory").profile.name == "memory"
    assert registry.resolve(task_text="refactor this module").profile.name == "coding"
    assert registry.resolve().profile.name == registry.default_profile.name


def test_resolve_prefers_an_explicit_task_type_over_text() -> None:
    registry = load_routing_registry()
    decision = registry.resolve(task_type="coding", task_text="audit the ledger")
    assert decision.profile.name == "coding"


def test_text_matching_is_specific_first() -> None:
    registry = load_routing_registry()
    assert registry.match_from_text("debug the failing test").profile.name == "coding"
    assert registry.match_from_text("please recall what we stored").profile.name == "memory"
    assert registry.match_from_text("what is this image").profile.name == "vision"
    assert registry.match_from_text("hello").profile.name == registry.default_profile.name


def test_with_failover_records_the_chain() -> None:
    registry = load_routing_registry()
    direct = registry.with_failover("coding")
    assert direct.chain == ("coding",)
    assert not direct.is_fallback

    fallen = registry.with_failover("coding", unavailable=["coding"])
    assert fallen.chain[0] == "coding"
    assert fallen.profile.name != "coding"
    assert fallen.is_fallback


def test_with_failover_reaches_the_terminal_profile() -> None:
    registry = load_routing_registry()
    decision = registry.with_failover("reason", unavailable={"reason", "ollama-local"})
    assert decision.profile.name == "deterministic"


def test_with_failover_refuses_a_loop() -> None:
    profile = RoutingProfile(
        name="loopy",
        description="loops",
        capabilities=frozenset({"classify"}),
        preferred_providers=("ollama",),
        allow_remote=False,
        max_tokens=64,
        temperature=0.2,
        budget=BudgetPolicy(),
        fallback_profile="loopy",
        required_approvals=frozenset(),
    )
    registry = RoutingRegistry([profile], default_profile="loopy", version="0.0.0")
    with pytest.raises(RoutingConfigError):
        registry.with_failover("loopy", unavailable=["loopy"])


def test_with_failover_refuses_an_unbounded_chain() -> None:
    profiles = [
        RoutingProfile(
            name=f"p{index}",
            description="chain",
            capabilities=frozenset({"classify"}),
            preferred_providers=("ollama",),
            allow_remote=False,
            max_tokens=64,
            temperature=0.2,
            budget=BudgetPolicy(),
            fallback_profile=f"p{index + 1}",
            required_approvals=frozenset(),
        )
        for index in range(6)
    ]
    registry = RoutingRegistry(profiles, default_profile="p0", version="0.0.0")
    with pytest.raises(RoutingConfigError):
        registry.with_failover("p0", unavailable={f"p{index}" for index in range(6)}, max_hops=3)


def _profile(name: str, **overrides: object) -> RoutingProfile:
    """Build a minimal valid profile, overriding fields for error tests."""
    data: dict[str, object] = {
        "name": name,
        "description": "test profile",
        "capabilities": frozenset({"classify"}),
        "preferred_providers": ("ollama",),
        "allow_remote": False,
        "max_tokens": 256,
        "temperature": 0.2,
        "budget": BudgetPolicy(),
        "fallback_profile": name,
        "required_approvals": frozenset(),
    }
    data.update(overrides)
    return RoutingProfile(**data)  # type: ignore[arg-type]


def test_registry_refuses_a_duplicate_profile_name() -> None:
    with pytest.raises(RoutingConfigError):
        RoutingRegistry([_profile("dup"), _profile("dup")], default_profile="dup", version="1.0.0")


def test_registry_refuses_a_paid_profile() -> None:
    paid = _profile("paid", budget=BudgetPolicy(max_spend_usd=1.5))
    with pytest.raises(RoutingConfigError, match="not free-first"):
        RoutingRegistry([paid], default_profile="paid", version="1.0.0")


def test_registry_refuses_unknown_cost() -> None:
    unknown = _profile("u", budget=BudgetPolicy(allow_unknown_cost=True))
    with pytest.raises(RoutingConfigError, match="not free-first"):
        RoutingRegistry([unknown], default_profile="u", version="1.0.0")


def test_registry_refuses_an_empty_set_and_a_bad_default() -> None:
    with pytest.raises(RoutingConfigError):
        RoutingRegistry([], default_profile="x", version="1.0.0")
    with pytest.raises(RoutingConfigError):
        RoutingRegistry([_profile("only")], default_profile="missing", version="1.0.0")


def test_budget_policy_free_first_predicate() -> None:
    assert BudgetPolicy().is_free_first
    assert not BudgetPolicy(max_spend_usd=0.1).is_free_first
    assert not BudgetPolicy(allow_unknown_cost=True).is_free_first


def test_routing_decision_is_a_fallback_only_when_the_chain_grows() -> None:
    direct = RoutingDecision(profile=_profile("a"), task_type="a", reason="direct")
    assert not direct.is_fallback
    fallen = RoutingDecision(
        profile=_profile("b"), task_type="a", reason="fallback", chain=("a", "b")
    )
    assert fallen.is_fallback


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "routing_profiles.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_missing_config_file_raises(tmp_path: Path) -> None:
    with pytest.raises(RoutingConfigError):
        load_routing_registry(tmp_path / "absent.yaml")


def test_config_without_profiles_raises(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "version: 1.0.0\ndefault_profile: p\nprofiles: {}\n")
    with pytest.raises(RoutingConfigError):
        load_routing_registry(path)


def test_config_without_a_version_raises(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        "default_profile: p\nprofiles:\n  p:\n    capabilities:\n      - classify\n"
        "    preferred_providers:\n      - ollama\n    fallback_profile: p\n",
    )
    with pytest.raises(RoutingConfigError):
        load_routing_registry(path)


def test_config_with_an_unknown_key_raises(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        "version: 1.0.0\ndefault_profile: p\nprofiles:\n  p:\n    capabilities:\n      - classify\n"
        "    preferred_providers:\n      - ollama\n    fallback_profile: p\n    surprise: 1\n",
    )
    with pytest.raises(RoutingConfigError, match="unknown keys"):
        load_routing_registry(path)


def test_config_with_a_bad_max_tokens_raises(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        "version: 1.0.0\ndefault_profile: p\nprofiles:\n  p:\n    capabilities:\n      - classify\n"
        "    preferred_providers:\n      - ollama\n    max_tokens: zero\n    fallback_profile: p\n",
    )
    with pytest.raises(RoutingConfigError, match="max_tokens"):
        load_routing_registry(path)


def test_config_with_a_bad_temperature_raises(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        "version: 1.0.0\ndefault_profile: p\nprofiles:\n  p:\n    capabilities:\n      - classify\n"
        "    preferred_providers:\n      - ollama\n    temperature: 9.0\n    fallback_profile: p\n",
    )
    with pytest.raises(RoutingConfigError, match="temperature"):
        load_routing_registry(path)


def test_config_with_a_missing_fallback_raises(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        "version: 1.0.0\ndefault_profile: p\nprofiles:\n  p:\n    capabilities:\n      - classify\n"
        "    preferred_providers:\n      - ollama\n",
    )
    with pytest.raises(RoutingConfigError, match="fallback_profile"):
        load_routing_registry(path)


def test_config_with_a_missing_capabilities_raises(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        "version: 1.0.0\ndefault_profile: p\nprofiles:\n  p:\n"
        "    preferred_providers:\n      - ollama\n"
        "    fallback_profile: p\n",
    )
    with pytest.raises(RoutingConfigError, match="capabilities"):
        load_routing_registry(path)


def test_config_with_empty_providers_raises(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        "version: 1.0.0\ndefault_profile: p\nprofiles:\n  p:\n    capabilities:\n      - classify\n"
        "    preferred_providers: []\n    fallback_profile: p\n",
    )
    with pytest.raises(RoutingConfigError, match="preferred_providers"):
        load_routing_registry(path)


def test_parser_handles_scalars_nesting_and_comments() -> None:
    parsed = parse_simple_yaml(
        "# leading\n"
        "name: demo  # trailing\n"
        "enabled: true\n"
        "ratio: 0.5\n"
        "count: 12\n"
        "nothing: null\n"
        'quoted: "a: b"\n'
        "empty_list: []\n"
        "empty_map: {}\n"
        "top:\n"
        "  nested:\n"
        "    key: value\n"
        "  items:\n"
        "    - one\n"
        "    - two\n"
    )
    assert parsed["name"] == "demo"
    assert parsed["enabled"] is True
    assert parsed["ratio"] == 0.5
    assert parsed["count"] == 12
    assert parsed["nothing"] is None
    assert parsed["quoted"] == "a: b"
    assert parsed["empty_list"] == []
    assert parsed["empty_map"] == {}
    top = parsed["top"]
    assert isinstance(top, dict)
    nested = top["nested"]
    assert isinstance(nested, dict)
    assert nested["key"] == "value"
    assert top["items"] == ["one", "two"]


def test_parser_keeps_hashes_inside_quotes() -> None:
    assert parse_simple_yaml('value: "a # b"\n')["value"] == "a # b"


def test_parser_refuses_flow_collections() -> None:
    with pytest.raises(YamlSubsetError, match="flow collections"):
        parse_simple_yaml("capabilities: [classify]\n")


def test_parser_refuses_tabs() -> None:
    with pytest.raises(YamlSubsetError):
        parse_simple_yaml("a:\n\tb: 1\n")


def test_parser_refuses_a_bad_mapping_line() -> None:
    with pytest.raises(YamlSubsetError):
        parse_simple_yaml("just some text\n")


def test_parser_refuses_unexpected_indentation() -> None:
    with pytest.raises(YamlSubsetError):
        parse_simple_yaml("a: 1\n  b: 2\n")


def test_parser_handles_an_empty_document() -> None:
    assert parse_simple_yaml("# only a comment\n") == {}


def test_text_rules_are_well_formed_and_name_real_profiles() -> None:
    """Every text rule names a profile the shipped configuration declares."""
    assert text_rules_are_well_formed()
