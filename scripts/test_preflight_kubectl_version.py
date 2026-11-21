#!/usr/bin/env python3
"""Executable tests for the preflight's kubectl version handling.

The client version is an operator choice, because the Kubernetes
version-skew policy supports kubectl only within one minor version of
kube-apiserver.  There is therefore no default: a guessed version could
sit outside the supported window and turn a reachable cluster into a
FAILED result, which reads as "the cluster is unreachable" when it is not.

These tests cover:

* the input is required and has no default;
* the client minor version is parsed from the input;
* the skew window is one minor version in either direction;
* the boundary cases (exactly one minor apart) are accepted and two are not;
* the server version is read from ``kubectl version -o json`` and nothing
  else about the cluster is printed;
* a version that cannot be parsed fails closed.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "agent-substrate-preflight.yml"

sys.path.insert(0, str(ROOT / "scripts"))

from kubeconfig_preflight_check import (  # noqa: E402
    parse_minor,
    server_minor_from_version_json,
    versions_are_compatible,
)


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def step_body(text: str, marker: str) -> str:
    """Extract the ``run:`` block of the step whose name contains ``marker``."""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if marker in line:
            start = index
            break
    if start is None:
        raise AssertionError(f"step containing {marker!r} not found")

    body_start = None
    for index in range(start, len(lines)):
        if lines[index].strip() == "run: |":
            body_start = index + 1
            break
    if body_start is None:
        raise AssertionError(f"no `run: |` block after {marker!r}")

    body = []
    for line in lines[body_start:]:
        if line.strip() == "":
            body.append("")
            continue
        if not line.startswith("          "):
            break
        body.append(line[10:])
    return "\n".join(body)


def input_block(text: str) -> list[str]:
    """Return the lines of the ``kubectl_version`` input mapping.

    The mapping is read line by line rather than with a regular
    expression: a pattern with nested quantifiers over an indented YAML
    block backtracks exponentially on a long input.
    """
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == "kubectl_version:":
            start = index
            break
    if start is None:
        raise AssertionError("kubectl_version input not found")

    block = []
    for line in lines[start + 1:]:
        if line.strip() == "":
            continue
        # The mapping ends at the first line that is not indented further
        # than the key itself.
        if not line.startswith("        "):
            break
        block.append(line)
    return block


class VersionInputTests(unittest.TestCase):
    """The client version is an explicit operator choice."""

    def test_01_kubectl_version_is_a_dispatch_input(self):
        self.assertIn("workflow_dispatch:", workflow_text())
        self.assertIn("kubectl_version:", workflow_text())

    def test_02_kubectl_version_is_required(self):
        block = input_block(workflow_text())
        required = [line for line in block if line.strip().startswith("required:")]
        self.assertEqual(len(required), 1, f"expected one required: line, got {required}")
        self.assertEqual(required[0].split(":", 1)[1].strip(), "true")

    def test_03_kubectl_version_has_no_default(self):
        """A default would be a guess, and a guess can be outside the
        supported skew window."""
        block = input_block(workflow_text())
        defaults = [line for line in block if line.strip().startswith("default:")]
        self.assertEqual(defaults, [], "the input must not carry a default")

    def test_04_the_workflow_uses_the_input_not_a_literal(self):
        text = workflow_text()
        self.assertIn("version: ${{ inputs.kubectl_version }}", text)
        self.assertNotIn("version: v1.29.0", text)

    def test_05_the_input_documentation_states_the_skew_rule(self):
        block = "\n".join(input_block(workflow_text()))
        self.assertIn("minor version", block)
        self.assertIn("kube-apiserver", block)


class SkewEvaluationTests(unittest.TestCase):
    """kubectl must be within one minor version of kube-apiserver."""

    def _skew_is_accepted(self, client: str, server: str) -> bool:
        """Ask the shipped checker whether the pair is compatible."""
        return versions_are_compatible(client, server)

    def test_06_same_minor_version_is_accepted(self):
        self.assertTrue(self._skew_is_accepted("v1.37.0", "37"))

    def test_07_one_minor_newer_client_is_accepted(self):
        """kubectl may be one minor newer than kube-apiserver."""
        self.assertTrue(self._skew_is_accepted("v1.38.0", "37"))

    def test_08_one_minor_older_client_is_accepted(self):
        """kubectl may be one minor older than kube-apiserver."""
        self.assertTrue(self._skew_is_accepted("v1.36.0", "37"))

    def test_09_two_minors_older_client_is_refused(self):
        self.assertFalse(self._skew_is_accepted("v1.35.0", "37"))

    def test_10_two_minors_newer_client_is_refused(self):
        self.assertFalse(self._skew_is_accepted("v1.39.0", "37"))

    def test_11_the_checker_uses_the_one_minor_window(self):
        """The rule itself lives in the shipped checker, not in the shell."""
        self.assertTrue(versions_are_compatible("v1.37.0", "37"))
        self.assertFalse(versions_are_compatible("v1.35.0", "37"))
        body = step_body(workflow_text(), "Check read-only API access")
        self.assertIn("--check-version", body)


class RunBehaviourTests(unittest.TestCase):
    """Execute the workflow's shell body with a stubbed kubectl."""

    SAFE_KUBECONFIG = (
        "apiVersion: v1\n"
        "kind: Config\n"
        "clusters:\n"
        "- name: c1\n"
        "  cluster:\n"
        "    server: https://k8s.example\n"
        "users:\n"
        "- name: u1\n"
        "  user:\n"
        "    token: PLACEHOLDER-NOT-A-REAL-TOKEN\n"
    )

    def _run_step(self, *, client_version: str, server_minor: str,
                  server_reports_version: bool = True) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "workspace"
            (workspace / "scripts").mkdir(parents=True)
            (workspace / "scripts" / "kubeconfig_preflight_check.py").write_text(
                (ROOT / "scripts" / "kubeconfig_preflight_check.py").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )

            bindir = tmp_path / "bin"
            bindir.mkdir()
            version_payload = json.dumps(
                {"serverVersion": {"major": "1", "minor": f"{server_minor}+"}}
            )
            (bindir / "kubectl").write_text(
                "#!/usr/bin/env bash\n"
                "if [[ \"$1\" == \"version\" ]]; then\n"
                + (f"  printf '%s' '{version_payload}'\n" if server_reports_version
                   else "  printf 'not json'\n")
                + "  exit 0\n"
                "fi\n"
                "if [[ \"$1\" == \"config\" && \"$2\" == \"view\" ]]; then\n"
                "  printf '%s' '{\"apiVersion\":\"v1\",\"kind\":\"Config\","
                "\"clusters\":[{\"name\":\"c\",\"cluster\":{\"server\":\"https://x\"}}]}'\n"
                "  exit 0\n"
                "fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            (bindir / "kubectl").chmod(0o755)

            body = step_body(workflow_text(), "Check read-only API access")
            script = tmp_path / "step.sh"
            script.write_text(body, encoding="utf-8")
            env = {
                "PATH": f"{bindir}:{os.environ.get('PATH', '')}",
                "GITHUB_WORKSPACE": str(workspace),
                "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
                "RUNNER_TEMP": str(tmp_path),
                "PREFLIGHT_KUBECONFIG": self.SAFE_KUBECONFIG,
                "PREFLIGHT_KUBECTL_VERSION": client_version,
                "HOME": str(tmp_path),
            }
            (tmp_path / "summary.md").write_text("", encoding="utf-8")
            result = subprocess.run(
                ["bash", str(script)], env=env, capture_output=True, text=True,
                timeout=60, check=False,
            )
            return result.returncode, result.stdout + result.stderr

    def test_12_matching_versions_reach_the_cluster_check(self):
        code, output = self._run_step(client_version="v1.37.0", server_minor="37")
        self.assertEqual(code, 0, output)
        self.assertIn("CONNECTED", output)

    def test_13_one_minor_apart_is_accepted(self):
        code, output = self._run_step(client_version="v1.36.0", server_minor="37")
        self.assertEqual(code, 0, output)
        self.assertIn("CONNECTED", output)

    def test_14_two_minors_apart_is_refused(self):
        """A client outside the skew window must not be used."""
        code, output = self._run_step(client_version="v1.35.0", server_minor="37")
        self.assertNotEqual(code, 0, "a client two minors away must be refused")
        self.assertNotIn("CONNECTED", output)

    def test_15_an_unparseable_client_version_is_refused(self):
        code, output = self._run_step(client_version="latest", server_minor="37")
        self.assertNotEqual(code, 0, "an unparseable client version must be refused")
        self.assertNotIn("CONNECTED", output)

    def test_16_an_unreadable_server_version_is_refused(self):
        """If the server version cannot be read, the skew cannot be proven."""
        code, output = self._run_step(
            client_version="v1.37.0", server_minor="37", server_reports_version=False
        )
        self.assertNotEqual(code, 0, "an unreadable server version must be refused")
        self.assertNotIn("CONNECTED", output)

    def test_17_no_version_numbers_are_printed(self):
        """The check must not leak version detail alongside the verdict."""
        code, output = self._run_step(client_version="v1.35.0", server_minor="37")
        self.assertNotEqual(code, 0)
        self.assertNotIn("1.35", output)
        self.assertNotIn("37", output)


if __name__ == "__main__":
    unittest.main()
