#!/usr/bin/env python3
"""Tests for scripts/doctor.py.

These tests assert the properties that make the doctor trustworthy, not
just that it runs:

* it never prints a secret VALUE (only names);
* a missing credential is reported as "absent", never as a failure,
  because the $0 offline fallback is a supported mode of operation;
* the budget posture is read from the environment, fail-closed by default;
* an unsafe OpenRouter model id is explained rather than silently accepted;
* the exit-code contract is 0/1/2/3 and is stable;
* the doctor is stdlib-only, so it can still run in a broken checkout.
"""

from __future__ import annotations

import ast
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import doctor  # noqa: E402


def _run(argv: list[str], env: dict[str, str] | None = None) -> tuple[int, str]:
    """Run the doctor with a controlled environment and capture its output."""
    with mock.patch.dict(os.environ, env or {}, clear=True):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = doctor.main(argv)
    return code, buffer.getvalue()


class DoctorExitCodeContractTests(unittest.TestCase):
    """The exit codes must mean exactly what the docstring says."""

    def test_exit_codes_are_the_documented_four(self) -> None:
        self.assertEqual(doctor.EXIT_OK, 0)
        self.assertEqual(doctor.EXIT_FAILED, 1)
        self.assertEqual(doctor.EXIT_USAGE, 2)
        self.assertEqual(doctor.EXIT_NOT_ATTEMPTED, 3)

    def test_a_report_with_only_passes_exits_zero(self) -> None:
        report = doctor.Report()
        report.add("ok", "pass")
        self.assertEqual(report.exit_code(), doctor.EXIT_OK)

    def test_a_single_failure_exits_one(self) -> None:
        report = doctor.Report()
        report.add("ok", "pass")
        report.add("bad", "fail")
        self.assertEqual(report.exit_code(), doctor.EXIT_FAILED)

    def test_a_skip_alone_is_not_a_failure(self) -> None:
        report = doctor.Report()
        report.add("optional", "skip")
        self.assertEqual(report.exit_code(), doctor.EXIT_NOT_ATTEMPTED)

    def test_a_warning_is_not_a_failure(self) -> None:
        report = doctor.Report()
        report.add("warned", "warn")
        self.assertEqual(report.exit_code(), doctor.EXIT_OK)

    def test_failure_outranks_skip(self) -> None:
        report = doctor.Report()
        report.add("optional", "skip")
        report.add("bad", "fail")
        self.assertEqual(report.exit_code(), doctor.EXIT_FAILED)


class DoctorStdlibOnlyTests(unittest.TestCase):
    """The doctor must have no third-party imports.

    It is the tool you reach for when the environment is broken. If it
    needed a package to run, it could not tell you that the package is
    missing.
    """

    def test_no_third_party_imports(self) -> None:
        """Only the repository's OWN package may be imported non-stdlib.

        ``ai2027_protocol`` is first-party: it lives in this repository and
        is installed from it, so importing it is not a third-party
        dependency. Anything else that is not in the standard library would
        make the doctor depend on the environment it is meant to diagnose.
        """
        path = Path(doctor.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        stdlib = set(sys.stdlib_module_names)
        first_party = {"ai2027_protocol"}
        offenders: list[str] = []
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name not in stdlib and name not in first_party:
                    offenders.append(name)
        self.assertEqual(offenders, [], f"non-stdlib imports: {sorted(set(offenders))}")

    def test_the_first_party_import_is_optional(self) -> None:
        """The doctor must work without the package installed.

        It is imported inside a function and guarded, never at module level.
        """
        path = Path(doctor.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module_level: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                module_level.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                module_level.append(node.module)
        self.assertNotIn(
            "ai2027_protocol",
            module_level,
            "the package must not be imported at module level",
        )

    def test_module_level_import_of_yaml_is_absent(self) -> None:
        """A regression guard for the PyYAML mistake.

        PyYAML is not a dependency of this repository, so an import of it
        would break the doctor exactly when the environment is incomplete.
        """
        source = Path(doctor.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import yaml", source)


class DoctorSecretSafetyTests(unittest.TestCase):
    """A secret VALUE must never reach the output."""

    FAKE = "sk-FAKEVALUE000000000000NOTREAL"

    def test_secret_values_are_never_printed(self) -> None:
        env = {
            "OPENROUTER_API_KEY": self.FAKE,
            "GEMINI_API_KEY": self.FAKE,
            "RENDER_API_KEY": self.FAKE,
            "E2B_API_KEY": self.FAKE,
        }
        code, output = _run(["--json"], env)
        self.assertNotIn(self.FAKE, output)
        self.assertNotIn("sk-FAKE", output)

    def test_secret_names_are_reported(self) -> None:
        """The NAME is safe and useful, so it must appear."""
        code, output = _run(["--json"], {"OPENROUTER_API_KEY": self.FAKE})
        self.assertIn("OPENROUTER_API_KEY", output)

    def test_no_secret_length_or_prefix_is_reported(self) -> None:
        env = {"OPENROUTER_API_KEY": self.FAKE}
        code, output = _run(["--json"], env)
        self.assertNotIn(str(len(self.FAKE)), output)


class DoctorMissingCredentialTests(unittest.TestCase):
    """A missing key is normal, not a failure.

    The package ships a deterministic $0 offline fallback precisely so the
    system is usable with no provider credentials at all. If the doctor
    failed on a missing key, people would learn to ignore it.
    """

    def test_missing_credentials_do_not_fail(self) -> None:
        report = doctor.Report()
        doctor.check_secrets(report)
        self.assertEqual(report.failed, [])
        self.assertTrue(any(c.name == "provider credentials" for c in report.checks))

    def test_present_credentials_do_not_fail(self) -> None:
        report = doctor.Report()
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "x"}, clear=True):
            doctor.check_secrets(report)
        self.assertEqual(report.failed, [])


class DoctorBudgetPostureTests(unittest.TestCase):
    """The $0 fail-closed default is the most important local invariant."""

    def test_unset_budget_is_fail_closed(self) -> None:
        report = doctor.Report()
        with mock.patch.dict(os.environ, {}, clear=True):
            doctor.check_budget_posture(report)
        self.assertEqual(report.failed, [])
        self.assertIn("$0", report.checks[0].detail)

    def test_zero_budget_is_pass(self) -> None:
        report = doctor.Report()
        with mock.patch.dict(os.environ, {"AI2027_MAX_SPEND_USD": "0"}, clear=True):
            doctor.check_budget_posture(report)
        self.assertEqual(report.failed, [])

    def test_positive_budget_is_a_warning_not_a_silent_pass(self) -> None:
        report = doctor.Report()
        with mock.patch.dict(os.environ, {"AI2027_MAX_SPEND_USD": "5"}, clear=True):
            doctor.check_budget_posture(report)
        self.assertEqual(report.failed, [])
        warnings = [c for c in report.checks if c.status == "warn"]
        self.assertTrue(warnings, "a raised ceiling must be surfaced")

    def test_non_numeric_budget_is_a_warning(self) -> None:
        report = doctor.Report()
        with mock.patch.dict(os.environ, {"AI2027_MAX_SPEND_USD": "lots"}, clear=True):
            doctor.check_budget_posture(report)
        self.assertEqual(report.failed, [])
        self.assertTrue([c for c in report.checks if c.status == "warn"])


class DoctorModelIdTests(unittest.TestCase):
    """An unsafe model id must be explained, not silently accepted."""

    def test_free_router_id_is_accepted(self) -> None:
        report = doctor.Report()
        with mock.patch.dict(os.environ, {"OPENROUTER_MODEL": "openrouter/free"}, clear=True):
            doctor.check_settings(report)
        self.assertEqual(report.failed, [])
        self.assertFalse([c for c in report.checks if c.status == "warn"])

    def test_pinned_free_id_is_accepted(self) -> None:
        report = doctor.Report()
        with mock.patch.dict(
            os.environ, {"OPENROUTER_MODEL": "some-vendor/some-model:free"}, clear=True
        ):
            doctor.check_settings(report)
        self.assertEqual(report.failed, [])
        self.assertFalse([c for c in report.checks if c.status == "warn"])

    def test_paid_id_is_warned_about(self) -> None:
        report = doctor.Report()
        with mock.patch.dict(
            os.environ, {"OPENROUTER_MODEL": "some-vendor/some-model"}, clear=True
        ):
            doctor.check_settings(report)
        self.assertEqual(report.failed, [])
        self.assertTrue([c for c in report.checks if c.status == "warn"])

    def test_auto_router_is_warned_about(self) -> None:
        report = doctor.Report()
        with mock.patch.dict(os.environ, {"OPENROUTER_MODEL": "openrouter/auto"}, clear=True):
            doctor.check_settings(report)
        self.assertTrue([c for c in report.checks if c.status == "warn"])


class DoctorBlueprintTests(unittest.TestCase):
    """The Studio service must keep its read-only, $0, sync:false posture."""

    def test_the_live_blueprint_passes(self) -> None:
        report = doctor.Report()
        doctor.check_render_blueprint(report)
        self.assertEqual(report.failed, [], [c.detail for c in report.failed])
        statuses = {c.name: c.status for c in report.checks}
        self.assertEqual(statuses.get("render blueprint"), "pass")
        self.assertEqual(statuses.get("studio service"), "pass")

    def test_a_broken_blueprint_is_detected(self) -> None:
        """The check must actually look at the file, not hardcode a pass."""
        text = (REPO_ROOT / "render.yaml").read_text(encoding="utf-8")
        broken = text.replace(
            '      - key: AI2027_MAX_SPEND_USD\n        value: "0"',
            '      - key: AI2027_MAX_SPEND_USD\n        value: "999"',
        )
        self.assertNotEqual(text, broken, "the fixture edit did not apply")
        with mock.patch.object(doctor, "ROOT", REPO_ROOT):
            with mock.patch.object(Path, "read_text", autospec=True, return_value=broken):
                report = doctor.Report()
                doctor.check_render_blueprint(report)
        self.assertTrue(report.failed, "a raised ceiling must be detected")
        self.assertIn("AI2027_MAX_SPEND_USD", report.failed[0].detail)

    def test_a_committed_secret_value_is_detected(self) -> None:
        text = (REPO_ROOT / "render.yaml").read_text(encoding="utf-8")
        broken = text.replace(
            "      - key: OPENROUTER_API_KEY\n        sync: false",
            '      - key: OPENROUTER_API_KEY\n        sync: false\n        value: "sk-oops"',
        )
        self.assertNotEqual(text, broken, "the fixture edit did not apply")
        with mock.patch.object(Path, "read_text", autospec=True, return_value=broken):
            report = doctor.Report()
            doctor.check_render_blueprint(report)
        self.assertTrue(report.failed, "a committed value must be detected")


class DoctorOutputTests(unittest.TestCase):
    """The output must be usable in both shapes."""

    def test_text_output_contains_the_check_names(self) -> None:
        code, output = _run([])
        self.assertIn("AI-2027 doctor", output)
        self.assertIn("budget posture", output)

    def test_json_output_is_valid_json(self) -> None:
        code, output = _run(["--json"])
        payload = json.loads(output)
        self.assertIn("checks", payload)
        self.assertIn("exit_code", payload)
        self.assertIsInstance(payload["checks"], list)
        self.assertTrue(payload["checks"])

    def test_json_exit_code_matches_the_text_exit_code(self) -> None:
        code_json, output_json = _run(["--json"])
        payload = json.loads(output_json)
        self.assertEqual(payload["exit_code"], code_json)

    def test_the_default_run_performs_no_network_io(self) -> None:
        """A doctor that hangs on a captive portal is a doctor nobody trusts."""
        import urllib.request

        def _explode(*args: object, **kwargs: object) -> None:
            raise AssertionError("the default doctor run must not touch the network")

        with mock.patch.object(urllib.request, "urlopen", _explode):
            with mock.patch.object(urllib.request, "Request", _explode):
                code, _ = _run([])
        self.assertIn(code, (doctor.EXIT_OK, doctor.EXIT_NOT_ATTEMPTED))

    def test_no_absolute_path_is_embedded_in_the_source(self) -> None:
        """Paths must resolve relative to the checkout, not to a hardcoded root."""
        source = Path(doctor.__file__).read_text(encoding="utf-8")
        self.assertNotIn("/home/", source)
        self.assertNotIn("/Users/", source)


class DoctorStudioDelegationTests(unittest.TestCase):
    """The Studio check must delegate, not re-implement the contract."""

    def test_it_runs_the_existing_verifier(self) -> None:
        verifier = REPO_ROOT / "scripts" / "verify_studio_instance.py"
        self.assertTrue(verifier.is_file())

        report = doctor.Report()
        with mock.patch.object(doctor.subprocess, "run") as run_mock:
            run_mock.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            doctor.check_studio(report, "https://example.invalid")
        self.assertTrue(run_mock.called)
        command = run_mock.call_args[0][0]
        self.assertIn("verify_studio_instance.py", " ".join(command))

    def test_a_failed_verifier_is_reported_as_a_failure(self) -> None:
        report = doctor.Report()
        with mock.patch.object(doctor.subprocess, "run") as run_mock:
            run_mock.return_value = mock.Mock(returncode=1, stdout="", stderr="")
            doctor.check_studio(report, "https://example.invalid")
        self.assertTrue(report.failed)

    def test_an_unreachable_instance_is_reported_as_a_failure(self) -> None:
        report = doctor.Report()
        with mock.patch.object(doctor.subprocess, "run") as run_mock:
            run_mock.return_value = mock.Mock(returncode=2, stdout="", stderr="")
            doctor.check_studio(report, "https://example.invalid")
        self.assertTrue(report.failed)


class DoctorDocumentationTests(unittest.TestCase):
    """The doctor must not drift from the registry it claims to follow."""

    def test_every_secret_name_is_documented_in_secrets_md(self) -> None:
        secrets_doc = (REPO_ROOT / "docs" / "SECRETS.md").read_text(encoding="utf-8")
        for name, _ in doctor.OPTIONAL_SECRETS:
            self.assertIn(
                name,
                secrets_doc,
                f"{name} is listed by the doctor but missing from docs/SECRETS.md",
            )

    def test_the_render_yaml_service_is_the_one_the_doctor_checks(self) -> None:
        text = (REPO_ROOT / "render.yaml").read_text(encoding="utf-8")
        self.assertIn("name: ai-2027-studio", text)


class EnvExampleTests(unittest.TestCase):
    """`.env.example` must stay a template, and must not drift.

    A template that contains a real value is a leak. A template that has
    fallen behind the registry is a trap: a person sets the variable it
    names and nothing happens.
    """

    ENV_EXAMPLE = REPO_ROOT / ".env.example"

    def test_the_template_exists(self) -> None:
        self.assertTrue(
            self.ENV_EXAMPLE.is_file(),
            ".env.example is missing; it is the documented quick-start template",
        )

    def test_every_variable_it_names_is_registered(self) -> None:
        """No variable may be listed that the registry does not know."""
        secrets_doc = (REPO_ROOT / "docs" / "SECRETS.md").read_text(encoding="utf-8")
        text = self.ENV_EXAMPLE.read_text(encoding="utf-8")
        names = {
            line.split("=", 1)[0].strip()
            for line in text.splitlines()
            if "=" in line and not line.strip().startswith("#")
        }
        for name in sorted(names):
            self.assertIn(
                name,
                secrets_doc,
                f"{name} is in .env.example but not registered in docs/SECRETS.md",
            )

    def test_no_line_carries_a_credential_shaped_value(self) -> None:
        """Every value must be empty or an obvious placeholder."""
        import re

        credential_shapes = re.compile(
            r"(sk-|gh[pousr]_|gsk_|AIza|AKIA|ASIA|Bearer\s+)[A-Za-z0-9_\-]{6,}"
        )
        text = self.ENV_EXAMPLE.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "=" not in line or line.strip().startswith("#"):
                continue
            _, _, value = line.partition("=")
            value = value.strip()
            if not value:
                continue
            self.assertIsNone(
                credential_shapes.search(value),
                f"line {lineno} looks like it carries a credential: {line.strip()!r}",
            )

    def test_it_never_states_the_fail_closed_posture_as_disabled(self) -> None:
        """The template must not hand a newcomer an unsafe starting point."""
        text = self.ENV_EXAMPLE.read_text(encoding="utf-8")
        self.assertIn("AI2027_MAX_SPEND_USD=0", text)
        self.assertIn("AI2027_READ_ONLY=1", text)

    def test_the_free_model_default_is_a_free_id(self) -> None:
        text = self.ENV_EXAMPLE.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("OPENROUTER_MODEL="):
                value = line.split("=", 1)[1].strip()
                self.assertTrue(
                    value == "openrouter/free" or value.endswith(":free"),
                    f"the default OpenRouter model {value!r} is not a free id",
                )
                return
        self.fail("OPENROUTER_MODEL is not declared in .env.example")


if __name__ == "__main__":
    unittest.main()
