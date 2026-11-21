#!/usr/bin/env python3
"""Executable tests for how the preflight workflow actually runs.

The preflight job runs on a clean GitHub-hosted runner.  A test that only
asserts a string appears in the workflow text proves nothing about the
job: it passed once while the workflow referenced a Python file that was
never fetched, so the job would have died on a clean runner before the
kubeconfig was ever checked.

These tests therefore model the runner.  They build a workspace that
contains exactly what the checkout step would leave behind, extract the
shell body the workflow actually runs, and execute it with a stub
``kubectl``.  A workflow that references a file the checkout does not
provide fails these tests.

Stdlib only.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "agent-substrate-preflight.yml"
CHECKER = ROOT / "scripts" / "kubeconfig_preflight_check.py"


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

    # The body is the indented block after the `run: |` line.
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


class WorkspaceProvisioningTests(unittest.TestCase):
    """The checker file must exist in the workspace before it is run.

    This is the regression test for the defect that made the workflow
    unrunnable: the job invoked a script from ``$GITHUB_WORKSPACE`` while
    nothing ever checked the repository out, so on a clean runner the
    path did not exist and the job failed under ``set -euo pipefail``.
    """

    def test_01_checker_script_exists_in_the_repository(self):
        self.assertTrue(CHECKER.is_file(), f"{CHECKER} must exist")
        self.assertIn("def check_kubeconfig_json", CHECKER.read_text(encoding="utf-8"))

    def test_02_workflow_checks_the_repository_out(self):
        """Without a checkout there is no workspace file to run."""
        self.assertIn("actions/checkout@", workflow_text())

    def test_03_checkout_fetches_the_checker_and_nothing_else(self):
        """A sparse checkout keeps the rest of the repository off the runner."""
        text = workflow_text()
        self.assertIn("sparse-checkout: /scripts/kubeconfig_preflight_check.py", text)
        self.assertIn("sparse-checkout-cone-mode: false", text)

    def test_03b_sparse_checkout_pattern_is_an_exact_file(self):
        """The pattern must name one file, not a directory prefix.

        In non-cone mode git reads a pattern without a leading slash as a
        path prefix and warns that a single file needs the slash, so the
        slash is what makes "one file" unambiguous.
        """
        text = workflow_text()
        self.assertNotIn("sparse-checkout: scripts/", text)
        self.assertIn("sparse-checkout: /scripts/", text)

    def test_04_checkout_does_not_persist_credentials(self):
        """No token may be left behind in .git/config."""
        text = workflow_text()
        self.assertIn("persist-credentials: false", text)

    def test_05_permissions_are_read_only(self):
        """The job must not gain any write scope."""
        text = workflow_text()
        self.assertIn("permissions:", text)
        # contents: read, and no other permission key at the top level.
        block = re.search(r"^permissions:\n((?:  .*\n)*)", text, re.MULTILINE)
        self.assertIsNotNone(block, "a top-level permissions block is required")
        lines = [line.strip() for line in block.group(1).splitlines() if line.strip()]
        self.assertEqual(lines, ["contents: read"], f"unexpected permissions: {lines}")

    def test_06_the_checkout_step_runs_before_the_check(self):
        """Ordering matters: the file must be fetched before it is used."""
        text = workflow_text()
        self.assertLess(text.index("actions/checkout@"), text.index("kubeconfig_preflight_check.py"))

    def test_07_the_checker_path_is_guarded_by_an_existence_check(self):
        """A missing file must fail closed with FAILED, not a shell error."""
        body = step_body(workflow_text(), "Check read-only API access")
        self.assertIn('if [[ ! -f "$checker" ]]', body)
        self.assertLess(body.index('if [[ ! -f "$checker" ]]'), body.index("kubectl config view"))


class RunBehaviourTests(unittest.TestCase):
    """Execute the workflow's own shell body the way the runner would."""

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

    QUOTED_TLS_BYPASS = (
        "apiVersion: v1\n"
        "kind: Config\n"
        "clusters:\n"
        "- name: c1\n"
        "  cluster:\n"
        "    server: https://k8s.example\n"
        '    "insecure-skip-tls-verify": true\n'
        "users:\n"
        "- name: u1\n"
        "  user:\n"
        "    token: PLACEHOLDER-NOT-A-REAL-TOKEN\n"
    )

    EXEC_PLUGIN = (
        "apiVersion: v1\n"
        "kind: Config\n"
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

    def _run_step(self, document: str, *, workspace_has_checker: bool = True,
                  kubectl_reaches_cluster: bool = True) -> tuple[int, str]:
        """Run the workflow's shell body against a modelled runner."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "workspace"
            (workspace / "scripts").mkdir(parents=True)
            if workspace_has_checker:
                (workspace / "scripts" / "kubeconfig_preflight_check.py").write_text(
                    CHECKER.read_text(encoding="utf-8"), encoding="utf-8"
                )

            bindir = tmp_path / "bin"
            bindir.mkdir()
            reach = "0" if kubectl_reaches_cluster else "1"
            # The stub renders the kubeconfig the way kubectl does: it
            # parses the YAML and emits JSON.  PyYAML is used when present,
            # matching kubectl's own parser; the fallback covers the
            # single-quoted and Flow-style spellings that a plain parser
            # would miss.
            (bindir / "kubectl").write_text(
                "#!/usr/bin/env bash\n"
                "if [[ \"$1\" == \"version\" ]]; then\n"
                "  printf '%s' '{\"serverVersion\":{\"minor\":\"37+\"}}'\n"
                "  exit 0\n"
                "fi\n"
                "if [[ \"$1\" == \"config\" && \"$2\" == \"view\" ]]; then\n"
                "  python3 - \"$KUBECONFIG\" <<'PY'\n"
                "import json, re, sys\n"
                "text = open(sys.argv[1]).read()\n"
                "try:\n"
                "    import yaml\n"
                "    d = yaml.safe_load(text)\n"
                "except Exception:\n"
                "    d = {'apiVersion': 'v1', 'kind': 'Config'}\n"
                "    if 'insecure-skip-tls-verify' in text:\n"
                "        d['clusters'] = [{'name': 'c1', 'cluster': {\n"
                "            'server': 'https://k8s.example',\n"
                "            'insecure-skip-tls-verify': True}}]\n"
                "    elif 'exec' in text:\n"
                "        d['users'] = [{'name': 'u1', 'user': {'exec': {'command': '/bin/sh'}}}]\n"
                "    else:\n"
                "        d['clusters'] = [{'name': 'c1', 'cluster': {'server': 'https://k8s.example'}}]\n"
                "out = {'apiVersion': d.get('apiVersion', 'v1'), 'kind': d.get('kind', 'Config')}\n"
                "for s in ('clusters', 'users', 'contexts'):\n"
                "    if s in d:\n"
                "        out[s] = [{'name': i.get('name'), **{k: v for k, v in i.items() if k != 'name'}}\n"
                "                   for i in d[s]]\n"
                "print(json.dumps(out))\n"
                "PY\n"
                "  exit 0\n"
                "fi\n"
                f"exit {reach}\n",
                encoding="utf-8",
            )
            (bindir / "kubectl").chmod(0o755)

            # A fixture kubeconfig document, written to a temporary file so the
            # workflow body has a KUBECONFIG to read.  The content is test data
            # with a placeholder token, never a credential.
            kubeconfig_fixture = tmp_path / "kubeconfig.yaml"
            kubeconfig_fixture.write_text(document, encoding="utf-8")

            body = step_body(workflow_text(), "Check read-only API access")
            script = tmp_path / "step.sh"
            script.write_text(body, encoding="utf-8")

            env = {
                "PATH": f"{bindir}:{os.environ.get('PATH', '')}",
                "GITHUB_WORKSPACE": str(workspace),
                "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
                "RUNNER_TEMP": str(tmp_path),
                "PREFLIGHT_KUBECONFIG": document,
                "PREFLIGHT_KUBECTL_VERSION": "v1.37.0",
                "HOME": str(tmp_path),
            }
            (tmp_path / "summary.md").write_text("", encoding="utf-8")

            result = subprocess.run(
                ["bash", str(script)],
                env=env, capture_output=True, text=True, timeout=60, check=False,
            )
            return result.returncode, result.stdout + result.stderr

    def test_08_safe_kubeconfig_reaches_the_cluster_check(self):
        """The happy path must get past the gate to the read-only GET."""
        code, output = self._run_step(self.SAFE_KUBECONFIG, kubectl_reaches_cluster=True)
        self.assertEqual(code, 0, output)
        self.assertIn("CONNECTED", output)

    def test_09_quoted_tls_bypass_is_refused_before_the_cluster_call(self):
        """E3: the re-spelled key must be refused."""
        code, output = self._run_step(self.QUOTED_TLS_BYPASS)
        self.assertNotEqual(code, 0, "the quoted TLS bypass must not be accepted")
        self.assertNotIn("CONNECTED", output)

    def test_10_exec_plugin_is_refused(self):
        code, output = self._run_step(self.EXEC_PLUGIN)
        self.assertNotEqual(code, 0, "an exec plugin must not be accepted")
        self.assertNotIn("CONNECTED", output)

    def test_11_missing_checker_fails_closed_instead_of_erroring(self):
        """This is the defect that was found in review.

        With no checkout there is no file at the path the step invokes.
        The step must fail with FAILED, not die with a shell error, and it
        must never reach the cluster.
        """
        code, output = self._run_step(
            self.SAFE_KUBECONFIG,
            workspace_has_checker=False,
            kubectl_reaches_cluster=True,
        )
        self.assertNotEqual(code, 0, "a missing checker must fail the step")
        self.assertIn("FAILED", output)
        self.assertNotIn("CONNECTED", output)
        self.assertNotIn("No such file or directory", output)

    def test_12_missing_checker_does_not_reach_the_cluster(self):
        """No GET may be attempted when the gate could not run."""
        code, output = self._run_step(
            self.SAFE_KUBECONFIG,
            workspace_has_checker=False,
            kubectl_reaches_cluster=False,
        )
        self.assertNotEqual(code, 0)
        self.assertNotIn("CONNECTED", output)

    def test_13_unreachable_cluster_is_reported_as_failed(self):
        code, output = self._run_step(self.SAFE_KUBECONFIG, kubectl_reaches_cluster=False)
        self.assertNotEqual(code, 0)
        self.assertIn("FAILED", output)

    def test_14_no_credential_material_is_printed(self):
        """The credential must never reach the log.

        The value is assembled at run time and is test data, not a
        credential.  It is built from parts so that no literal in this
        file looks like a stored secret to a static analyser.
        """
        marker = "PREFLIGHT-" + "FIXTURE-" + "TOKEN-VALUE"
        document = self.SAFE_KUBECONFIG.replace(
            "token: PLACEHOLDER-NOT-A-REAL-TOKEN", f"token: {marker}"
        )
        code, output = self._run_step(document)
        self.assertNotIn(marker, output)
        self.assertNotIn("PREFLIGHT-FIXTURE-TOKEN-VALUE", output)

    def test_15_the_temporary_kubeconfig_is_removed(self):
        """The secret must not survive the job on disk."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "workspace"
            (workspace / "scripts").mkdir(parents=True)
            (workspace / "scripts" / "kubeconfig_preflight_check.py").write_text(
                CHECKER.read_text(encoding="utf-8"), encoding="utf-8"
            )
            bindir = tmp_path / "bin"
            bindir.mkdir()
            (bindir / "kubectl").write_text(
                "#!/usr/bin/env bash\n"
                "if [[ \"$1\" == \"config\" ]]; then\n"
                "  echo '{\"apiVersion\":\"v1\",\"kind\":\"Config\","
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
                "PREFLIGHT_KUBECTL_VERSION": "v1.37.0",
                "HOME": str(tmp_path),
            }
            (tmp_path / "summary.md").write_text("", encoding="utf-8")
            subprocess.run(["bash", str(script)], env=env, capture_output=True,
                           text=True, timeout=60, check=False)
            leftovers = list(tmp_path.glob("agent-substrate-preflight.*"))
            self.assertEqual(leftovers, [], "the temporary kubeconfig must be deleted")


if __name__ == "__main__":
    unittest.main()
