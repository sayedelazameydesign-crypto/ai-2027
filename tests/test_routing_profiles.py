#!/usr/bin/env python3
"""Dependency-free tests for the capability routing profiles.

These tests run with the standard library only (``python -m unittest``). They
cover the three claims the configuration layer makes:

* the shipped ``config/routing_profiles.yaml`` parses and validates;
* every profile is free-first, declares only known capabilities and provider
  kinds, and never names a concrete model;
* selection, text matching, strict refusal and bounded failover behave
  fail-closed.
"""

from __future__ import annotations

import ast
import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "routing_profiles.yaml"
PACKAGE_DIR = ROOT / "python-2027" / "src" / "ai2027_protocol"
MODULE_PATH = PACKAGE_DIR / "routing_profiles.py"

# A synthetic package name: the routing module uses a relative import for its
# parser, so it must be imported as a member of a package. The real
# ``ai2027_protocol`` package cannot be imported here (its __init__ pulls in
# third-party packages that are not installed), so a bare package shell with the
# right __path__ is registered instead — submodules load from disk without
# running __init__.py.
_PACKAGE_NAME = "_ai2027_routing_pkg"


def _load_routing_module() -> object:
    """Import ``routing_profiles.py`` as a package member, without third-party deps."""
    if _PACKAGE_NAME not in sys.modules:
        package = types.ModuleType(_PACKAGE_NAME)
        package.__path__ = [str(PACKAGE_DIR)]  # type: ignore[attr-defined]
        sys.modules[_PACKAGE_NAME] = package
    importlib.import_module(f"{_PACKAGE_NAME}.yaml_subset")
    return importlib.import_module(f"{_PACKAGE_NAME}.routing_profiles")


routing = _load_routing_module()


class ShippedConfigurationTests(unittest.TestCase):
    """The shipped config parses and every profile validates."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = routing.load_routing_registry()

    def test_config_file_exists(self) -> None:
        self.assertTrue(CONFIG_PATH.is_file(), f"missing {CONFIG_PATH}")

    def test_registry_loads_with_version_and_source(self) -> None:
        self.assertEqual(self.registry.version, "1.0.0")
        self.assertEqual(self.registry.source, CONFIG_PATH)

    def test_default_profile_is_declared(self) -> None:
        self.assertIn(self.registry.default_profile.name, self.registry.names())

    def test_every_profile_declares_a_description_and_notes(self) -> None:
        for name in self.registry.names():
            profile = self.registry.get(name)
            self.assertTrue(profile.description, f"{name} has no description")
            self.assertTrue(profile.notes, f"{name} has no notes")

    def test_capabilities_are_within_the_closed_vocabulary(self) -> None:
        for name in self.registry.names():
            for capability in self.registry.get(name).capabilities:
                self.assertIn(capability, routing.CAPABILITY_VOCABULARY, f"{name}: {capability}")

    def test_provider_kinds_are_supported(self) -> None:
        for name in self.registry.names():
            for provider in self.registry.get(name).preferred_providers:
                self.assertIn(provider, routing.SUPPORTED_PROVIDER_KINDS, f"{name}: {provider}")

    def test_every_profile_is_free_first(self) -> None:
        for name in self.registry.names():
            self.assertTrue(
                self.registry.get(name).budget.is_free_first,
                f"{name} is not free-first",
            )

    def test_profiles_do_not_name_concrete_models(self) -> None:
        """A profile must describe a capability, never a catalogue model id."""
        for name in self.registry.names():
            profile = self.registry.get(name)
            for capability in profile.capabilities:
                self.assertNotRegex(capability, r"\d", f"{name}: {capability} looks like a model id")
            for provider in profile.preferred_providers:
                self.assertNotRegex(provider, r"\d", f"{name}: {provider} looks like a model id")
            self.assertNotRegex(profile.description, r"[a-z]+-\d", f"{name}: {profile.description}")

    def test_fallback_profiles_are_declared(self) -> None:
        for name in self.registry.names():
            profile = self.registry.get(name)
            self.assertIn(profile.fallback_profile, self.registry.names(), f"{name} fallback")

    def test_every_fallback_chain_terminates(self) -> None:
        for name in self.registry.names():
            seen: set[str] = set()
            current = self.registry.get(name)
            while current.name not in seen:
                seen.add(current.name)
                current = self.registry.get(current.fallback_profile)
            self.assertIn(current.name, seen, f"{name} fallback chain loops")


class SelectionTests(unittest.TestCase):
    """Explicit task types map to profiles, and unknown ones fail closed."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = routing.load_routing_registry()

    def test_known_task_type_selects_that_profile(self) -> None:
        decision = self.registry.select("coding")
        self.assertEqual(decision.profile.name, "coding")
        self.assertFalse(decision.is_fallback)

    def test_unknown_task_type_falls_back_to_default(self) -> None:
        decision = self.registry.select("not-a-real-task")
        self.assertEqual(decision.profile.name, self.registry.default_profile.name)
        self.assertIn("fell back", decision.reason)

    def test_strict_selection_refuses_an_unknown_task_type(self) -> None:
        with self.assertRaises(routing.UnknownTaskTypeError):
            self.registry.select("not-a-real-task", strict=True)

    def test_unknown_profile_raises(self) -> None:
        with self.assertRaises(routing.UnknownProfileError):
            self.registry.get("does-not-exist")

    def test_resolve_prefers_an_explicit_task_type(self) -> None:
        decision = self.registry.resolve(task_type="memory", task_text="debug this code")
        self.assertEqual(decision.profile.name, "memory")

    def test_resolve_from_text_only(self) -> None:
        decision = self.registry.resolve(task_text="please refactor this module and add tests")
        self.assertEqual(decision.profile.name, "coding")

    def test_resolve_with_no_input_uses_the_default(self) -> None:
        decision = self.registry.resolve()
        self.assertEqual(decision.profile.name, self.registry.default_profile.name)

    def test_text_matching_prefers_the_specific_signal(self) -> None:
        decision = self.registry.match_from_text("diagnose why this function fails")
        self.assertEqual(decision.profile.name, "coding")

    def test_text_with_no_signal_uses_the_default(self) -> None:
        decision = self.registry.match_from_text("hello there")
        self.assertEqual(decision.profile.name, self.registry.default_profile.name)


class FailoverTests(unittest.TestCase):
    """Failover follows the chain, records it, and refuses loops."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = routing.load_routing_registry()

    def test_available_profile_is_served_directly(self) -> None:
        decision = self.registry.with_failover("coding")
        self.assertEqual(decision.profile.name, "coding")
        self.assertEqual(decision.chain, ("coding",))
        self.assertFalse(decision.is_fallback)

    def test_unavailable_profile_falls_back_and_records_the_chain(self) -> None:
        decision = self.registry.with_failover("coding", unavailable=["coding"])
        self.assertEqual(decision.chain[0], "coding")
        self.assertNotEqual(decision.profile.name, "coding")
        self.assertTrue(decision.is_fallback)
        self.assertNotIn(decision.profile.name, {"coding"})

    def test_failover_stops_at_the_terminal_profile(self) -> None:
        decision = self.registry.with_failover("reason", unavailable={"reason", "ollama-local"})
        self.assertEqual(decision.profile.name, "deterministic")

    def test_failover_refuses_a_looping_chain(self) -> None:
        profile = routing.RoutingProfile(
            name="loopy",
            description="loops",
            capabilities=frozenset({"classify"}),
            preferred_providers=("ollama",),
            allow_remote=False,
            max_tokens=64,
            temperature=0.2,
            budget=routing.BudgetPolicy(),
            fallback_profile="loopy",
            required_approvals=frozenset(),
        )
        registry = routing.RoutingRegistry(
            [profile], default_profile="loopy", version="0.0.0", source=None
        )
        # The loop is only traversed once the profile is unavailable, so it must
        # be listed as unavailable for the guard to be exercised.
        with self.assertRaises(routing.RoutingConfigError):
            registry.with_failover("loopy", unavailable=["loopy"])


class ConfigurationValidationTests(unittest.TestCase):
    """Malformed configuration is refused at load time."""

    def _write(self, text: str) -> Path:
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "routing_profiles.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_missing_config_file_raises(self) -> None:
        with self.assertRaises(routing.RoutingConfigError):
            routing.load_routing_registry(Path("/nonexistent/routing_profiles.yaml"))

    def test_paid_profile_is_refused(self) -> None:
        path = self._write(
            "version: 1.0.0\ndefault_profile: p\nprofiles:\n"
            "  p:\n    capabilities:\n      - classify\n"
            "    preferred_providers:\n      - ollama\n"
            "    budget:\n      max_spend_usd: 5.0\n    fallback_profile: p\n"
        )
        with self.assertRaises(routing.RoutingConfigError) as ctx:
            routing.load_routing_registry(path)
        self.assertIn("not free-first", str(ctx.exception))

    def test_unknown_capability_is_refused(self) -> None:
        path = self._write(
            "version: 1.0.0\ndefault_profile: p\nprofiles:\n"
            "  p:\n    capabilities:\n      - not-a-capability\n"
            "    preferred_providers:\n      - ollama\n    fallback_profile: p\n"
        )
        with self.assertRaises(routing.RoutingConfigError) as ctx:
            routing.load_routing_registry(path)
        self.assertIn("closed vocabulary", str(ctx.exception))

    def test_unknown_provider_kind_is_refused(self) -> None:
        path = self._write(
            "version: 1.0.0\ndefault_profile: p\nprofiles:\n"
            "  p:\n    capabilities:\n      - classify\n"
            "    preferred_providers:\n      - some-other-vendor\n    fallback_profile: p\n"
        )
        with self.assertRaises(routing.RoutingConfigError) as ctx:
            routing.load_routing_registry(path)
        self.assertIn("not supported", str(ctx.exception))

    def test_missing_default_profile_is_refused(self) -> None:
        path = self._write(
            "version: 1.0.0\nprofiles:\n"
            "  p:\n    capabilities:\n      - classify\n"
            "    preferred_providers:\n      - ollama\n    fallback_profile: p\n"
        )
        with self.assertRaises(routing.RoutingConfigError):
            routing.load_routing_registry(path)

    def test_flow_style_is_refused_not_misread(self) -> None:
        path = self._write(
            "version: 1.0.0\ndefault_profile: p\nprofiles:\n"
            "  p:\n    capabilities: [classify]\n"
            "    preferred_providers:\n      - ollama\n    fallback_profile: p\n"
        )
        with self.assertRaises(routing.RoutingConfigError):
            routing.load_routing_registry(path)


class ParserTests(unittest.TestCase):
    """The strict YAML subset parser handles the shipped shapes."""

    def test_scalars_and_comments(self) -> None:
        parsed = routing.parse_simple_yaml(
            "# leading comment\n"
            "name: demo  # trailing comment\n"
            "enabled: true\n"
            "ratio: 0.5\n"
            "count: 12\n"
            "nothing: null\n"
            "quoted: \"a: b\"\n"
        )
        self.assertEqual(parsed["name"], "demo")
        self.assertIs(parsed["enabled"], True)
        self.assertEqual(parsed["ratio"], 0.5)
        self.assertEqual(parsed["count"], 12)
        self.assertIsNone(parsed["nothing"])
        self.assertEqual(parsed["quoted"], "a: b")

    def test_nested_mappings_and_sequences(self) -> None:
        parsed = routing.parse_simple_yaml(
            "top:\n"
            "  nested:\n"
            "    key: value\n"
            "  items:\n"
            "    - one\n"
            "    - two\n"
        )
        self.assertEqual(parsed["top"]["nested"]["key"], "value")
        self.assertEqual(parsed["top"]["items"], ["one", "two"])

    def test_comment_inside_quotes_is_kept(self) -> None:
        parsed = routing.parse_simple_yaml('value: "a # b"\n')
        self.assertEqual(parsed["value"], "a # b")

    def test_empty_document(self) -> None:
        self.assertEqual(routing.parse_simple_yaml("# only a comment\n"), {})


class LoaderDependencyHonestyTests(unittest.TestCase):
    """The configuration layer must stay importable without third-party packages."""

    def test_routing_module_imports_only_the_standard_library(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        stdlib = set(sys.stdlib_module_names)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        non_stdlib = imported - stdlib
        self.assertEqual(non_stdlib, set(), f"routing_profiles.py imports non-stdlib: {sorted(non_stdlib)}")


if __name__ == "__main__":
    unittest.main()
