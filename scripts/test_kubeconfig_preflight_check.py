#!/usr/bin/env python3
"""Executable adversarial tests for the kubeconfig preflight safety gate.

The gate is the thing that stands between an arbitrary kubeconfig and the
cluster API, so its behaviour is tested *behaviourally*: real kubeconfig
documents are rendered to the JSON shape that ``kubectl config view --raw
-o json`` produces, fed through the checker, and the verdict is asserted.

These tests are the reason the earlier grep-based gate passed review while
being bypassable: it asserted that certain strings appeared in the
workflow text, not that the gate refused a hostile kubeconfig.

Stdlib only, so the suite runs anywhere the workflow runs.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = Path(__file__).resolve().parent.parent

from kubeconfig_preflight_check import (  # noqa: E402
    CONNECTED,
    FAILED,
    check_kubeconfig_json,
    main,
)


def render(
    clusters: list[dict] | None = None,
    users: list[dict] | None = None,
    contexts: list[dict] | None = None,
) -> str:
    """Build the JSON that ``kubectl config view --raw -o json`` emits.

    ``kubectl`` renders the kubeconfig with one object per named entry, so
    the shapes below mirror its output rather than the input YAML.
    """
    document: dict[str, object] = {"apiVersion": "v1", "kind": "Config"}
    if clusters is not None:
        document["clusters"] = clusters
    if users is not None:
        document["users"] = users
    if contexts is not None:
        document["contexts"] = contexts
    return json.dumps(document)


SAFE_CLUSTER = {"name": "c1", "cluster": {"server": "https://k8s.example"}}
SAFE_USER = {"name": "u1", "user": {"token": "REDACTED"}}


class AcceptsSafeKubeconfigTests(unittest.TestCase):
    """A well-formed, static, TLS-verifying kubeconfig must be accepted."""

    def test_01_safe_static_kubeconfig_is_accepted(self):
        document = render(clusters=[SAFE_CLUSTER], users=[SAFE_USER])
        check_kubeconfig_json(document)  # must not raise

    def test_03_minimal_kubeconfig_with_only_clusters_is_accepted(self):
        check_kubeconfig_json(render(clusters=[SAFE_CLUSTER]))

    def test_03b_legitimate_tls_material_is_accepted(self):
        """Only the dangerous keys are refused.

        ``certificate-authority-data`` and the client certificate fields
        are ordinary, correct kubeconfig content; refusing them would
        make the gate unusable without making it any safer.
        """
        document = render(
            clusters=[{"name": "c1", "cluster": {
                "server": "https://k8s.example",
                "certificate-authority-data": "LS0tLS1CRUdJTg==",
                "tls-server-name": "k8s.example",
            }}],
            users=[{"name": "u1", "user": {
                "client-certificate-data": "cert",
                "client-key-data": "key",
            }}],
        )
        check_kubeconfig_json(document)  # must not raise


class RejectsTlsBypassTests(unittest.TestCase):
    """TLS verification must never be disabled, however it is spelled."""

    def test_04_plain_insecure_skip_tls_verify_is_rejected(self):
        document = render(
            clusters=[
                {
                    "name": "c1",
                    "cluster": {"server": "https://k8s.example", "insecure-skip-tls-verify": True},
                }
            ],
            users=[SAFE_USER],
        )
        with self.assertRaises(ValueError):
            check_kubeconfig_json(document)

    def test_05_key_with_surrounding_whitespace_is_rejected(self):
        document = render(
            clusters=[
                {
                    "name": "c1",
                    "cluster": {
                        "server": "https://k8s.example",
                        "  insecure-skip-tls-verify  ": True,
                    },
                }
            ],
            users=[SAFE_USER],
        )
        with self.assertRaises(ValueError):
            check_kubeconfig_json(document)

    def test_08_uppercase_key_is_rejected(self):
        document = render(
            clusters=[
                {
                    "name": "c1",
                    "cluster": {"server": "https://k8s.example", "INSECURE-SKIP-TLS-VERIFY": True},
                }
            ],
            users=[SAFE_USER],
        )
        with self.assertRaises(ValueError):
            check_kubeconfig_json(document)

    def test_09_tls_bypass_nested_at_depth_is_rejected(self):
        """The key is refused wherever it appears, not only at a fixed depth."""
        document = render(
            clusters=[
                {
                    "name": "c1",
                    "cluster": {
                        "server": "https://k8s.example",
                        "nested": {"deeper": {"insecure-skip-tls-verify": True}},
                    },
                }
            ],
            users=[SAFE_USER],
        )
        with self.assertRaises(ValueError):
            check_kubeconfig_json(document)


class RejectsExecPluginTests(unittest.TestCase):
    """Credential plugins must never be present, let alone executed."""

    def test_10_exec_plugin_is_rejected(self):
        document = render(
            clusters=[SAFE_CLUSTER],
            users=[{"name": "u1", "user": {"exec": {"command": "/bin/sh"}}}],
        )
        with self.assertRaises(ValueError):
            check_kubeconfig_json(document)

    def test_12_exec_inside_an_auth_provider_is_rejected(self):
        document = render(
            clusters=[SAFE_CLUSTER],
            users=[
                {
                    "name": "u1",
                    "user": {
                        "auth-provider": {"name": "gcp", "config": {"exec": {"command": "/bin/sh"}}}
                    },
                }
            ],
        )
        with self.assertRaises(ValueError):
            check_kubeconfig_json(document)


class RejectsMalformedInputTests(unittest.TestCase):
    """Unparseable or wrongly shaped input must fail closed."""

    def test_13_empty_input_is_rejected(self):
        with self.assertRaises(ValueError):
            check_kubeconfig_json("")

    def test_14_truncated_json_is_rejected(self):
        with self.assertRaises(ValueError):
            check_kubeconfig_json('{"clusters": [{"cluster": {"server": "htt')

    def test_15_not_json_is_rejected(self):
        with self.assertRaises(ValueError):
            check_kubeconfig_json("apiVersion: v1\nkind: Config\n")

    def test_16_json_array_is_rejected(self):
        with self.assertRaises(ValueError):
            check_kubeconfig_json('[{"apiVersion": "v1"}]')

    def test_17_json_scalar_is_rejected(self):
        with self.assertRaises(ValueError):
            check_kubeconfig_json('"just a string"')

    def test_18_document_without_kubeconfig_sections_is_rejected(self):
        """Parses cleanly but is not a kubeconfig; accepting it would let a
        substituted file through the gate."""
        with self.assertRaises(ValueError):
            check_kubeconfig_json('{"hello": "world"}')

    def test_19_clusters_section_of_wrong_type_is_rejected(self):
        with self.assertRaises(ValueError):
            check_kubeconfig_json('{"clusters": "not-a-list"}')

    def test_20_null_document_is_rejected(self):
        with self.assertRaises(ValueError):
            check_kubeconfig_json("null")


class YamlSourceIsNormalisedTests(unittest.TestCase):
    """The YAML source is re-spelled in many ways; the gate must not care.

    This is the layer where the original bypass lived.  YAML resolves a
    quoted key and a Flow-style mapping to exactly the same key as the
    plain form, so a grep over the raw YAML could be bypassed while
    kubectl still honoured the setting.  The workflow renders the
    kubeconfig to JSON before checking it, and JSON carries no quoting
    distinction, so every spelling below collapses to one structure and
    one verdict.

    The render here uses PyYAML.  Note that Kubernetes itself parses
    kubeconfig with ``sigs.k8s.io/yaml``, which converts YAML to JSON
    and then uses the standard library's JSON decoder - so the JSON
    normalisation this suite relies on is exactly the normalisation
    Kubernetes performs.  The key property under test is that every
    spelling of the key produces one structure, and PyYAML demonstrates
    that property for the YAML syntax the gate must survive.  These cases
    skip cleanly when PyYAML is absent; CI installs it for this suite so
    a skip is never mistaken for a pass on the protection cases.
    """

    def setUp(self):
        try:
            import yaml  # noqa: F401  (optional; CI installs no packages)
        except ImportError:
            self.skipTest("PyYAML not installed — YAML-level tests skip")
        self.yaml = yaml

    def _render_like_kubectl(self, text: str) -> str:
        """Emulate ``kubectl config view --raw -o json``."""
        document = self.yaml.safe_load(text)
        out: dict[str, object] = {
            "apiVersion": document.get("apiVersion", "v1"),
            "kind": document.get("kind", "Config"),
        }
        for section in ("clusters", "users", "contexts"):
            if section not in document:
                continue
            entries = []
            for item in document[section]:
                body = {k: v for k, v in item.items() if k != "name"}
                entries.append({"name": item.get("name"), **body})
            out[section] = entries
        return json.dumps(out)

    def _verdict(self, yaml_text: str) -> str:
        document = self._render_like_kubectl(yaml_text)
        try:
            check_kubeconfig_json(document)
        except ValueError:
            return "REJECT"
        return "ACCEPT"

    def test_25_plain_tls_bypass_is_rejected(self):
        source = (
            "clusters:\n"
            "- name: c1\n"
            "  cluster:\n"
            "    server: https://k8s.example\n"
            "    insecure-skip-tls-verify: true\n"
            "users:\n"
            "- name: u1\n"
            "  user:\n"
            "    token: t\n"
        )
        self.assertEqual(self._verdict(source), "REJECT")

    def test_26_double_quoted_tls_bypass_is_rejected(self):
        """The exact bypass found in review: the old grep missed it."""
        source = (
            "clusters:\n"
            "- name: c1\n"
            "  cluster:\n"
            "    server: https://k8s.example\n"
            '    "insecure-skip-tls-verify": true\n'
            "users:\n"
            "- name: u1\n"
            "  user:\n"
            "    token: t\n"
        )
        self.assertEqual(self._verdict(source), "REJECT")

    def test_27_single_quoted_tls_bypass_is_rejected(self):
        source = (
            "clusters:\n"
            "- name: c1\n"
            "  cluster:\n"
            "    server: https://k8s.example\n"
            "    'insecure-skip-tls-verify': true\n"
            "users:\n"
            "- name: u1\n"
            "  user:\n"
            "    token: t\n"
        )
        self.assertEqual(self._verdict(source), "REJECT")

    def test_28_flow_style_tls_bypass_is_rejected(self):
        source = (
            'clusters: [{name: c1, cluster: {server: "https://k8s.example", '
            "insecure-skip-tls-verify: true}}]\n"
            "users: [{name: u1, user: {token: t}}]\n"
        )
        self.assertEqual(self._verdict(source), "REJECT")

    def test_29_flow_style_quoted_tls_bypass_is_rejected(self):
        source = (
            'clusters: [{name: c1, cluster: {server: "https://k8s.example", '
            '"insecure-skip-tls-verify": true}}]\n'
            "users: [{name: u1, user: {token: t}}]\n"
        )
        self.assertEqual(self._verdict(source), "REJECT")

    def test_30_exec_plugin_is_rejected(self):
        source = (
            "clusters:\n"
            "- name: c1\n"
            "  cluster:\n"
            "    server: https://k8s.example\n"
            "users:\n"
            "- name: u1\n"
            "  user:\n"
            "    exec:\n"
            "      command: /bin/sh\n"
        )
        self.assertEqual(self._verdict(source), "REJECT")

    def test_31_quoted_exec_plugin_is_rejected(self):
        source = (
            "clusters:\n"
            "- name: c1\n"
            "  cluster:\n"
            "    server: https://k8s.example\n"
            "users:\n"
            "- name: u1\n"
            "  user:\n"
            '    "exec":\n'
            "      command: /bin/sh\n"
        )
        self.assertEqual(self._verdict(source), "REJECT")

    def test_32_safe_static_kubeconfig_is_accepted(self):
        source = (
            "clusters:\n"
            "- name: c1\n"
            "  cluster:\n"
            "    server: https://k8s.example\n"
            "users:\n"
            "- name: u1\n"
            "  user:\n"
            "    token: t\n"
        )
        self.assertEqual(self._verdict(source), "ACCEPT")

    def test_33_every_tls_bypass_spelling_renders_to_one_identical_json(self):
        """Why the JSON layer cannot be bypassed by re-spelling the key."""
        spellings = [
            "insecure-skip-tls-verify: true",
            '"insecure-skip-tls-verify": true',
            "'insecure-skip-tls-verify': true",
        ]
        rendered = set()
        for spelling in spellings:
            source = (
                "clusters:\n"
                "- name: c1\n"
                "  cluster:\n"
                "    server: https://k8s.example\n"
                f"    {spelling}\n"
                "users:\n"
                "- name: u1\n"
                "  user:\n"
                "    token: t\n"
            )
            rendered.add(self._render_like_kubectl(source))
        self.assertEqual(len(rendered), 1, "spellings must collapse to one JSON")


class VerdictOutputTests(unittest.TestCase):
    """The gate must print one verdict word and nothing else."""

    def _capture(self, stdin_text: str) -> tuple[int, str]:
        import io
        import contextlib

        original = sys.stdin
        sys.stdin = io.StringIO(stdin_text)
        try:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = main([])
            return code, buffer.getvalue()
        finally:
            sys.stdin = original

    def test_21_safe_input_prints_only_connected(self):
        code, output = self._capture(render(clusters=[SAFE_CLUSTER], users=[SAFE_USER]))
        self.assertEqual(code, 0)
        self.assertEqual(output, CONNECTED + "\n")

    def test_22_rejected_input_prints_only_failed(self):
        document = render(
            clusters=[SAFE_CLUSTER],
            users=[{"name": "u1", "user": {"exec": {"command": "/bin/sh"}}}],
        )
        code, output = self._capture(document)
        self.assertEqual(code, 1)
        self.assertEqual(output, FAILED + "\n")

    def test_23_no_credential_material_is_echoed(self):
        """A rejected kubeconfig must not leak the secret it contained."""
        document = render(
            clusters=[
                {
                    "name": "c1",
                    "cluster": {
                        "server": "https://secret-host.internal:6443",
                        "insecure-skip-tls-verify": True,
                    },
                }
            ],
            users=[{"name": "u1", "user": {"token": "SUPER-SECRET-TOKEN"}}],
        )
        code, output = self._capture(document)
        self.assertEqual(code, 1)
        self.assertNotIn("SUPER-SECRET-TOKEN", output)
        self.assertNotIn("secret-host.internal", output)
        self.assertEqual(output, FAILED + "\n")

    def test_24_malformed_input_prints_only_failed(self):
        code, output = self._capture("not json at all")
        self.assertEqual(code, 1)
        self.assertEqual(output, FAILED + "\n")


class AdversarialCasesMustNotSkipTests(unittest.TestCase):
    """A skipped adversarial case is not a passing adversarial case.

    The YAML-level cases need PyYAML.  When it is absent they skip, and a
    skip reads like a pass in CI output.  These tests make that visible:
    CI installs PyYAML for this suite, so any skip there means the
    protection was not actually exercised.
    """

    def test_25_ci_installs_pyyaml_for_this_suite(self):
        """The CI job must install PyYAML, or the adversarial cases skip."""
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("pip install --quiet --disable-pip-version-check pyyaml", workflow)
        self.assertIn("scripts/test_kubeconfig_preflight_check.py -v", workflow)

    def test_26_yaml_cases_run_when_pyyaml_is_available(self):
        """Sanity check that the YAML-level cases are not silently absent."""
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML not installed here — CI installs it for this suite")
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(YamlSourceIsNormalisedTests)
        self.assertGreater(suite.countTestCases(), 0, "the YAML-level cases must exist")
        result = unittest.TestResult()
        suite.run(result)
        self.assertEqual(result.skipped, [], "no YAML-level adversarial case may skip")
        self.assertEqual(len(result.failures), 0, f"failures: {result.failures}")
        self.assertEqual(len(result.errors), 0, f"errors: {result.errors}")


if __name__ == "__main__":
    unittest.main()
