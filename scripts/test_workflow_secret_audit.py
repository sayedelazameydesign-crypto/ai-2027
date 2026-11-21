#!/usr/bin/env python3
"""Tests for the workflow secret audit — static, names only, no values.

Run: python3 scripts/test_workflow_secret_audit.py -v
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import workflow_secret_audit as wsa  # noqa: E402


class ReferenceTests(unittest.TestCase):
    def test_01_an_env_assignment_is_the_safe_shape(self):
        refs = wsa.find_references("    env:\n      API_KEY: ${{ secrets.MY_KEY }}\n")
        self.assertEqual(len(refs), 1)
        self.assertTrue(refs[0]["safe"])
        self.assertEqual(refs[0]["name"], "MY_KEY")

    def test_02_interpolation_into_script_text_is_unsafe(self):
        line = '          run: curl -H "K: ${{ secrets.MY_KEY }}" https://x\n'
        refs = wsa.find_references(line)
        self.assertFalse(refs[0]["safe"])

    def test_03_a_secret_reference_in_if_is_rejected(self):
        refs = wsa.find_references("    if: ${{ secrets.MY_KEY != '' }}\n")
        self.assertFalse(refs[0]["safe"])

    def test_04_commented_references_are_ignored(self):
        self.assertEqual(wsa.find_references("#   KEY: ${{ secrets.OLD }}\n"), [])

    def test_05_line_numbers_and_multiple_references(self):
        text = ("a\n"
                "  A: ${{ secrets.ONE }}\n"
                "  B: ${{ secrets.TWO }} ${{ secrets.THREE }}\n")
        refs = wsa.find_references(text)
        self.assertEqual([r["line"] for r in refs], [2, 3, 3])
        self.assertEqual([r["name"] for r in refs], ["ONE", "TWO", "THREE"])

    def test_06_an_unrelated_expression_is_not_a_secret(self):
        self.assertEqual(wsa.find_references("  X: ${{ github.sha }}\n"), [])

    def test_07_a_named_reusable_workflow_secret_mapping_is_safe(self):
        text = ("jobs:\n  call:\n    uses: ./.github/workflows/child.yml\n"
                "    secrets:\n      CHILD_KEY: ${{ secrets.MY_KEY }}\n")
        refs = wsa.find_references(text)
        self.assertEqual(len(refs), 1)
        self.assertTrue(refs[0]["safe"])

    def test_08_a_secret_under_with_is_not_a_safe_transport(self):
        text = ("jobs:\n  call:\n    uses: ./.github/workflows/child.yml\n"
                "    with:\n      CHILD_KEY: ${{ secrets.MY_KEY }}\n")
        refs = wsa.find_references(text)
        self.assertEqual(len(refs), 1)
        self.assertFalse(refs[0]["safe"])


class AuditTests(unittest.TestCase):
    def _audit(self, workflow_text: str, register_text: str):
        with tempfile.TemporaryDirectory() as tmp:
            wf = Path(tmp) / "workflows"
            wf.mkdir()
            (wf / "x.yml").write_text(workflow_text, encoding="utf-8")
            doc = Path(tmp) / "SECRETS.md"
            doc.write_text(register_text, encoding="utf-8")
            return wsa.audit(workflows_dir=wf, register_path=doc)

    def test_10_a_documented_env_secret_passes(self):
        result = self._audit("    env:\n      K: ${{ secrets.MY_KEY }}\n", "uses `MY_KEY`")
        self.assertTrue(result["ok"], result["findings"])
        self.assertEqual(result["references"]["MY_KEY"], ["x.yml:2"])

    def test_11_an_undocumented_secret_fails(self):
        result = self._audit("    env:\n      K: ${{ secrets.MY_KEY }}\n", "nothing here")
        self.assertFalse(result["ok"])
        self.assertEqual(result["findings"][0]["rule"], "undocumented_secret")

    def test_11b_an_unrecorded_name_is_not_echoed(self):
        """A name nobody recorded may itself be sensitive; report the location."""
        result = self._audit("    env:\n      K: ${{ secrets.actual-key-material }}\n",
                             "the register says nothing")
        self.assertNotIn("actual-key-material", json.dumps(result))
        self.assertIn("<undocumented secret>", json.dumps(result))

    def test_11c_a_recorded_name_is_echoed_so_it_can_be_rotated(self):
        result = self._audit("    env:\n      K: ${{ secrets.GEMINI_API_KEY }}\n",
                             "| `GEMINI_API_KEY` | ci.yml |")
        self.assertIn("GEMINI_API_KEY", result["references"])

    def test_12_an_inline_secret_fails_even_when_documented(self):
        text = '          run: echo "${{ secrets.MY_KEY }}"\n'
        result = self._audit(text, "`MY_KEY`")
        self.assertFalse(result["ok"])
        rules = {f["rule"] for f in result["findings"]}
        self.assertEqual(rules, {"inline_secret"})

    def test_13_a_missing_doc_file_documents_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            wf = Path(tmp) / "workflows"
            wf.mkdir()
            (wf / "x.yml").write_text("  K: ${{ secrets.MY_KEY }}\n", encoding="utf-8")
            result = wsa.audit(workflows_dir=wf,
                               register_path=Path(tmp) / "absent.md")
        self.assertFalse(result["ok"])

    def test_14_an_empty_workflow_directory_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = wsa.audit(workflows_dir=Path(tmp) / "nope",
                               register_path=Path(tmp) / "nope.md")
        self.assertTrue(result["ok"])
        self.assertEqual(result["workflows_scanned"], 0)

    def test_15_the_result_is_json_serialisable(self):
        result = self._audit("  K: ${{ secrets.MY_KEY }}\n", "`MY_KEY`")
        json.loads(json.dumps(result))


class RealRepositoryTests(unittest.TestCase):
    """The check that keeps the actual workflows honest as they change."""

    def test_20_every_secret_in_real_workflows_is_documented_and_safely_mapped(self):
        result = wsa.audit()
        self.assertTrue(result["ok"], json.dumps(result["findings"], indent=2))
        self.assertIn("GEMINI_API_KEY", result["references"])
        self.assertIn("RENDER_API_KEY", result["references"])

    def test_21_the_audit_never_reads_a_secret_value(self):
        """It can only see names — proof: the source has no env-value reader."""
        src = (HERE / "workflow_secret_audit.py").read_text(encoding="utf-8")
        self.assertNotIn("os.environ", src)
        # a secret reference is matched by name only, never resolved
        result = wsa.audit()
        self.assertTrue(all("value" not in f for f in result["findings"]))

    def test_22_assert_mode_exits_one_on_a_violation(self):
        original = wsa.audit
        wsa.audit = lambda *a, **k: {"ok": False, "workflows_scanned": 1,
                                     "references": {},
                                     "findings": [{"file": "x.yml", "line": 1,
                                                   "rule": "inline_secret",
                                                   "detail": "…"}]}
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(wsa.main(["--assert"]), 1)
            self.assertIn("FAIL", buf.getvalue())
        finally:
            wsa.audit = original

    def test_23_assert_mode_exits_zero_when_clean(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(wsa.main(["--assert"]), 0)
        self.assertIn("env/reusable-workflow mapping and is documented", buf.getvalue())

    def test_24_lifecycle_is_manual_and_paid_checks_default_off(self):
        text = (wsa.WORKFLOWS / "project-lifecycle.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("pull_request:", text)
        self.assertNotIn("\n  push:", text)
        self.assertEqual(text.count("default: false"), 4)
        self.assertIn('GITHUB_REF" != "refs/heads/main"', text)

    def test_25_lifecycle_passes_only_named_provider_secrets(self):
        text = (wsa.WORKFLOWS / "project-lifecycle.yml").read_text(encoding="utf-8")
        for name in ("GEMINI_API_KEY", "NVIDIA_API_KEY", "E2B_API_KEY", "RENDER_API_KEY"):
            self.assertIn(f"secrets.{name}", text)
        self.assertNotIn("secrets: inherit", text)

    def test_26_render_requires_ci_provider_gates_and_exact_confirmation(self):
        text = (wsa.WORKFLOWS / "project-lifecycle.yml").read_text(encoding="utf-8")
        self.assertIn("RENDER_CONFIRM\" != \"$SERVICE_NAME", text)
        self.assertIn("needs.quality.result == 'success'", text)
        self.assertIn("needs.gemini.result == 'success'", text)
        self.assertIn("needs.nvidia.result == 'success'", text)
        self.assertIn("needs.e2b.result == 'success'", text)
        self.assertIn("require_credential: true", text)

    def test_27_nonsecret_variables_configure_models_and_render_service(self):
        render = (wsa.WORKFLOWS / "deploy.yml").read_text(encoding="utf-8")
        gemini = (wsa.WORKFLOWS / "gemini-live-check.yml").read_text(encoding="utf-8")
        nvidia = (wsa.WORKFLOWS / "nvidia-live-check.yml").read_text(encoding="utf-8")
        self.assertIn("inputs.service_id || vars.RENDER_SERVICE_ID", render)
        self.assertIn("inputs.service_name || vars.RENDER_SERVICE_NAME", render)
        self.assertIn("environment: production", render)
        self.assertIn("vars.GEMINI_MODEL", gemini)
        self.assertIn("vars.NVIDIA_MODEL", nvidia)

    def test_28_reusable_live_checks_fail_closed_when_requested_keys_are_missing(self):
        for filename, key in (("gemini-live-check.yml", "GEMINI_API_KEY"),
                              ("nvidia-live-check.yml", "NVIDIA_API_KEY"),
                              ("e2b-live-check.yml", "E2B_API_KEY"),
                              ("deploy.yml", "RENDER_API_KEY")):
            text = (wsa.WORKFLOWS / filename).read_text(encoding="utf-8")
            self.assertIn("workflow_call:", text, filename)
            self.assertIn("REQUIRE_CREDENTIAL", text, filename)
            self.assertIn(f"{key} is required", text, filename)

    def test_29_secret_bearing_operations_are_main_only(self):
        for filename in ("ci.yml", "gemini-live-check.yml", "nvidia-live-check.yml",
                         "e2b-live-check.yml", "deploy.yml", "project-lifecycle.yml",
                         "agent-substrate-preflight.yml"):
            text = (wsa.WORKFLOWS / filename).read_text(encoding="utf-8")
            self.assertIn("refs/heads/main", text, filename)

    def test_35_agent_substrate_preflight_is_manual_main_only_and_read_only(self):
        text = (wsa.WORKFLOWS / "agent-substrate-preflight.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("\n  pull_request:", text)
        self.assertNotIn("\n  push:", text)
        self.assertIn("if: github.ref == 'refs/heads/main'", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        # The job fetches one repository file, so it needs read scope on
        # the contents.  It must hold no other permission: no write, no
        # packages, no deployments.
        self.assertIn("permissions:\n  contents: read\n", text)
        for forbidden_permission in (
            "contents: write",
            "packages: write",
            "id-token: write",
            "actions: write",
            "deployments: write",
        ):
            self.assertNotIn(forbidden_permission, text)
        self.assertIn("ACTIONS_STEP_DEBUG: 'false'", text)
        self.assertIn("ACTIONS_RUNNER_DEBUG: 'false'", text)
        self.assertIn("secrets.AGENT_SUBSTRATE_PREFLIGHT_KUBECONFIG", text)
        self.assertIn("set +x", text)
        self.assertNotIn("echo $PREFLIGHT_KUBECONFIG", text)
        self.assertIn("""trap 'rm -f -- "$kubeconfig_path"' EXIT""", text)
        self.assertIn(
            "kubectl --request-timeout=15s get --raw='/api/v1/nodes?limit=1' >/dev/null 2>/dev/null",
            text,
        )
        self.assertIn('echo "CONNECTED"', text)
        self.assertIn('echo "FAILED"', text)
        for forbidden in (
            "kubectl apply",
            "kubectl create",
            "kubectl patch",
            "kubectl delete",
            "kubectl edit",
            "cluster-info",
        ):
            self.assertNotIn(forbidden, text)

    def test_36_agent_substrate_preflight_checks_the_kubeconfig_structurally(self):
        """The kubeconfig gate must inspect parsed structure, not raw text.

        Grepping the YAML for ``insecure-skip-tls-verify:`` was bypassable:
        YAML resolves a quoted key and a Flow-style mapping to the same key
        as the plain form, so a re-spelled setting passed the grep while
        kubectl still honoured it.  The workflow must therefore render the
        kubeconfig to JSON and hand it to the checker, and the old
        text-matching gate must be gone.
        """
        text = (wsa.WORKFLOWS / "agent-substrate-preflight.yml").read_text(encoding="utf-8")
        self.assertIn("kubectl config view --raw -o json", text)
        self.assertIn("scripts/kubeconfig_preflight_check.py", text)
        self.assertIn("grep -qx CONNECTED", text)
        # The bypassable text-matching gate must not return.
        self.assertNotIn("insecure-skip-tls-verify:", text)
        self.assertNotIn("grep -Eiq", text)

    def test_37_agent_substrate_preflight_never_prints_the_rendered_kubeconfig(self):
        """``config view --raw`` output carries credential material.

        It must be piped straight into the checker, never redirected to a
        file, echoed, or appended to the job summary.
        """
        text = (wsa.WORKFLOWS / "agent-substrate-preflight.yml").read_text(encoding="utf-8")
        for forbidden in (
            "config view --raw -o json >",
            "config view --raw -o json | tee",
            "config view --raw -o json | cat",
            "config view --raw -o json >>",
        ):
            self.assertNotIn(forbidden, text)
        # Only CONNECTED or FAILED may ever reach the summary.
        self.assertIn("""printf 'CONNECTED\\n' >> "$GITHUB_STEP_SUMMARY\"""", text)
        self.assertIn("""printf 'FAILED\\n' >> "$GITHUB_STEP_SUMMARY\"""", text)

    def test_38_agent_substrate_preflight_checkout_is_minimal_and_secret_free(self):
        """The checkout may not be given the secret, and may not persist a token.

        The preflight secret is read only by the connectivity step.  A
        checkout step that could see it, or that left a usable token in
        .git/config, would widen the blast radius of the job for no
        reason.
        """
        text = (wsa.WORKFLOWS / "agent-substrate-preflight.yml").read_text(encoding="utf-8")
        self.assertIn("persist-credentials: false", text)
        self.assertIn("sparse-checkout: /scripts/kubeconfig_preflight_check.py", text)
        self.assertIn("sparse-checkout-cone-mode: false", text)
        # The secret must appear exactly once: in the env of the check step.
        self.assertEqual(text.count("secrets.AGENT_SUBSTRATE_PREFLIGHT_KUBECONFIG"), 1)
        self.assertIn("PREFLIGHT_KUBECONFIG: ${{ secrets.AGENT_SUBSTRATE_PREFLIGHT_KUBECONFIG }}", text)

    def test_39_agent_substrate_preflight_pins_the_kubectl_version(self):
        """``latest`` would change the tool that produces the JSON silently."""
        text = (wsa.WORKFLOWS / "agent-substrate-preflight.yml").read_text(encoding="utf-8")
        self.assertNotIn("version: latest", text)
        self.assertRegex(text, r"version: v\d+\.\d+\.\d+")

    def test_30_windows_job_extracts_only_verified_blobs_without_checkout(self):
        if os.name == "nt":
            self.skipTest("the synthetic trailing-dot path exists only on POSIX filesystems")
        ci = (wsa.WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
        start = ci.index("      - name: Materialize verified BrowserSkill files without full checkout")
        run_start = ci.index("        run: |\n", start) + len("        run: |\n")
        end = ci.index("\n      - name: Native NTFS ACLs", run_start)
        source = textwrap.dedent(ci[run_start:end])
        self.assertIn("git(\"ls-tree\"", source)
        self.assertIn("git(\"cat-file\", \"blob\"", source)
        self.assertIn("hashlib.sha1", source)
        self.assertIn("url, revision)", source)
        self.assertIn("GITHUB_SHA", source)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bare, seed = root / "fixture.git", root / "seed"
            workspace, runner_temp = root / "workspace", root / "runner-temp"
            workspace.mkdir()
            runner_temp.mkdir()
            subprocess.run(["git", "init", "--bare", str(bare)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "clone", str(bare), str(seed)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "-C", str(seed), "config", "user.name", "CI fixture"], check=True)
            subprocess.run(["git", "-C", str(seed), "config", "user.email", "ci@example.invalid"], check=True)

            adapter_file = seed / "browser_skill_adapter" / "proof.py"
            adapter_file.parent.mkdir(parents=True)
            adapter_file.write_text("PROOF = 'tracked blob'\n", encoding="utf-8")
            archived = seed / "projects" / "celia-fashion-2026." / ".env.example"
            archived.parent.mkdir(parents=True)
            archived.write_text("must never be checked out\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(seed), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(seed), "commit", "-m", "fixture"], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "-C", str(seed), "branch", "-M", "main"], check=True)
            subprocess.run(["git", "-C", str(seed), "push", "origin", "main"], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            revision = subprocess.check_output(
                ["git", "-C", str(seed), "rev-parse", "HEAD"], text=True
            ).strip()
            env = os.environ | {
                "GITHUB_SHA": revision,
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_SERVER_URL": root.as_uri(),
                "GITHUB_REPOSITORY": "fixture",
                "GITHUB_WORKSPACE": str(workspace),
                "RUNNER_TEMP": str(runner_temp),
                "GIT_TERMINAL_PROMPT": "0",
            }
            result = subprocess.run([sys.executable, "-c", source], env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((workspace / "browser_skill_adapter" / "proof.py").read_text(),
                             "PROOF = 'tracked blob'\n")
            self.assertFalse((workspace / "projects").exists())

    def test_31_render_judge_receives_fail_closed_flag_and_formats_summary(self):
        text = (wsa.WORKFLOWS / "deploy.yml").read_text(encoding="utf-8")
        judge = text.split("      - name: Judge the run by the measured verdict\n", 1)[1]
        self.assertIn(
            "env:\n          REQUIRE_CREDENTIAL: ${{ inputs.require_credential }}\n        run: |\n",
            judge,
        )
        self.assertIn(r'write_text("\n".join(lines) + "\n")', text)

    def test_32_live_gemini_in_ci_requires_main_branch_explicit_opt_in(self):
        text = (wsa.WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
        dispatch = text.split("  workflow_dispatch:\n", 1)[1].split("  workflow_call:\n", 1)[0]
        self.assertIn("run_live_gemini:", dispatch)
        self.assertIn("default: false", dispatch)
        live_job = text.split("  live-gemini:\n", 1)[1]
        self.assertIn("github.ref == 'refs/heads/main'", live_job)
        self.assertIn("inputs.run_live_gemini", live_job)
        self.assertIn("require_credential: ${{ inputs.run_live_gemini }}", live_job)

    def test_33_windows_acl_job_uses_private_profile_and_reports_test_results(self):
        text = (wsa.WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("Provision isolated owner-only test profile", text)
        self.assertIn("SetAccessRuleProtection($true, $false)", text)
        self.assertIn('"USERPROFILE=$testHome"', text)
        self.assertIn('"TMP=$testTemp"', text)
        self.assertIn("names == expected", text)
        self.assertIn("Windows test traceback", text)

    def test_34_service_workflows_own_private_state_and_never_cleanup_through_links(self):
        workflows = (
            ("ci.yml", "ai-2027-ci-owned-state-v1"),
            ("ember.yml", "ai-2027-ember-owned-state-v1"),
        )
        for filename, owner_marker in workflows:
            with self.subTest(workflow=filename):
                text = (wsa.WORKFLOWS / filename).read_text(encoding="utf-8")
                self.assertIn("refusing pre-existing .ember-up workflow state", text)
                self.assertIn("mkdir -m 700 .ember-up", text)
                self.assertIn("set -o noclobber", text)
                self.assertIn("os.O_EXCL", text)
                self.assertIn("workflow-owner", text)
                self.assertIn(owner_marker, text)
                self.assertIn("[[ ! -L \"$pid_file\" && -f \"$pid_file\"", text)
                self.assertIn("refusing symlinked .ember-up during cleanup", text)
                self.assertNotIn("pkill -f", text)
                self.assertNotIn("/tmp/live-agent.log", text)
                self.assertNotIn("/tmp/hud.log", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
