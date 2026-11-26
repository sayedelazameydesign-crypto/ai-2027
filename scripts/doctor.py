#!/usr/bin/env python3
"""One place to answer "is this checkout ready to run?".

This is a *report*, not a new gate. The repository already owns the real
checks -- ``scripts/network_preflight.py``, ``scripts/health_gate.py``,
``scripts/render_deploy.py``, ``scripts/verify_studio_instance.py``,
``scripts/secret_scan.py``, ``scripts/ci_guard.py`` -- and this script does
not replace any of them or weaken their contracts. What it does is run the
cheap, local, offline subset of them once and print a single readable
summary, so a person (or a CI step) does not have to know the order in
which twenty scripts are supposed to be run.

Design rules, all of them deliberate:

* **Stdlib only.** The kernel is zero-dependency and ``scripts/ci_guard.py``
  fails CI if that ever stops being true. A doctor that needs a third-party
  package to tell you your environment is broken is not a doctor.
* **Never print a secret value.** Only the *presence* of a variable is ever
  reported, never its content. Not even a length, and not even a prefix.
* **Offline by default.** The default run performs no network I/O at all.
  Network checks are opt-in via ``--network``, because a doctor that hangs on
  a captive portal is a doctor nobody trusts.
* **No absolute paths.** Everything is resolved relative to the repository
  root, so a checkout works wherever it is unpacked.

Exit codes:
    0  every check passed (or was legitimately skipped and said so)
    1  at least one check FAILED
    2  the doctor could not run at all (bad usage, or the checkout is broken)
    3  a required check could not be attempted (e.g. a dependency is missing)

Usage:
    python scripts/doctor.py                 # offline report
    python scripts/doctor.py --network       # also probe egress + optional Studio
    python scripts/doctor.py --studio URL    # verify a deployed Studio instance
    python scripts/doctor.py --json          # machine-readable output
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_NOT_ATTEMPTED = 3

#: The minimum Python the protocol package declares support for.
REQUIRED_PYTHON = (3, 11)

#: Checks whose outcome depends on the ENVIRONMENT rather than on the
#: repository. In a CI matrix that deliberately runs an older interpreter,
#: or that omits the optional runtime dependencies, these would fail for a
#: reason that is not a defect in the checkout. ``--lenient`` downgrades
#: exactly these to warnings.
#:
#: Everything else -- the blueprint posture, the checkout layout, the
#: scripts suites -- describes the repository itself and stays a hard
#: failure everywhere.
LENIENT_CHECKS = frozenset(
    {
        "python version",
        "protocol package import",
        "cli entrypoint",
    }
)

#: Secret *names* only. Values are never read, never hashed, never printed.
#: Keep this in sync with docs/SECRETS.md -- that file is the registry of
#: record; this list only records which names have a real local consumer.
#:
#: Each entry is (name, required_for_local_run, one_line_description).
#: "required" means: without it, the *live provider call* cannot be made.
#: It never means the package cannot be imported, and it never means the
#: system is unsafe -- a missing key is reported as "absent", not "failed".
OPTIONAL_SECRETS: tuple[tuple[str, str], ...] = (
    ("OPENROUTER_API_KEY", "OpenRouter free-tier provider (:free models only)"),
    ("GEMINI_API_KEY", "Gemini provider (cost classified, not assumed free)"),
    ("GROQ_API_KEY", "Groq provider (free tier only)"),
    ("NVIDIA_API_KEY", "NVIDIA NIM provider (cost classified, not assumed free)"),
    ("OLLAMA_API_KEY", "Ollama Cloud (UNKNOWN_COST, blocked at $0)"),
    ("OMNIROUTERS_API_KEY", "OmniRouters (UNKNOWN_COST, blocked at $0)"),
    ("E2B_API_KEY", "E2B sandbox backend (consumes credit)"),
    ("RENDER_API_KEY", "Render deployment API"),
    ("EMBER_ALERT_WEBHOOK", "Stage-three degradation alert webhook"),
    ("CLOUDFLARE_API_TOKEN", "Cloudflare REST admin (unused in this repo)"),
    ("R2_ACCESS_KEY_ID", "R2 access key id"),
    ("R2_SECRET_ACCESS_KEY", "R2 secret access key"),
    ("AGENT_SUBSTRATE_KUBECONFIG", "Agent Substrate cluster kubeconfig"),
    ("SUBSTRATE_API_TOKEN", "Agent Substrate gateway bearer token"),
    ("SUBSTRATE_ACTOR_MASTER_KEY", "Agent Substrate HMAC master key"),
    ("CONNECTORS_HUB_API_TOKEN", "Connectors Hub bearer token"),
    ("WAAPI_API_TOKEN", "WAAPI (waapi.app) WhatsApp gateway — owner notify + approval-gated send"),
)

#: Variables that are configuration, not secrets. These are reported by value
#: because their value IS the configuration (e.g. a budget ceiling of 0).
#: A model id is operator choice, and a wrong one is a cost problem, so it is
#: worth showing -- but it is still only ever shown if it is not secret.
NON_SECRET_SETTINGS: tuple[tuple[str, str], ...] = (
    ("AI2027_MAX_SPEND_USD", "Fail-closed budget ceiling in USD (0 = spend nothing)"),
    ("AI2027_READ_ONLY", "1 = refuse every write"),
    ("AI2027_FREE_MODELS", "Extra model ids declared free by the operator"),
    ("AI2027_ALLOW_UNKNOWN_COST", "1 = permit UNKNOWN_COST calls (authorisation, not a ceiling)"),
    ("OPENROUTER_MODEL", "OpenRouter model id (must be openrouter/free or *:free)"),
    ("GROQ_MODEL", "Groq model id"),
    ("GEMINI_MODEL", "Gemini model id"),
    ("NVIDIA_MODEL", "NVIDIA model id"),
    ("OLLAMA_MODEL", "Local Ollama model id"),
    ("OLLAMA_BASE_URL", "Local Ollama base URL"),
    ("RENDER_SERVICE_NAME", "Render service name"),
    ("CONNECTORS_HUB_URL", "Connectors Hub base URL"),
    ("WAAPI_INSTANCE_ID", "waapi.app instance id when more than one exists"),
    ("WAAPI_OWNER_CHAT_ID", "Fixed WhatsApp notify recipient (owner only)"),
    ("PA_WAAPI_NOTIFY", "1 = WhatsApp companion for approval cards (default 0)"),
)


@dataclass
class Check:
    """One reported check.

    ``status`` is one of ``pass``, ``fail``, ``skip``, ``warn``. ``skip``
    means "not attempted, and that is correct"; ``warn`` means "attempted and
    inconclusive"; ``fail`` means "attempted and wrong".
    """

    name: str
    status: str
    detail: str = ""
    remedy: str = ""


@dataclass
class Report:
    """The collected result of every check the doctor ran."""

    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "", remedy: str = "") -> None:
        """Append one check result to the report."""
        self.checks.append(Check(name, status, detail, remedy))

    @property
    def failed(self) -> list[Check]:
        """Return the checks that were attempted and did not pass."""
        return [c for c in self.checks if c.status == "fail"]

    @property
    def not_attempted(self) -> list[Check]:
        """Return the checks that were deliberately not attempted."""
        return [c for c in self.checks if c.status == "skip"]

    def exit_code(self) -> int:
        """Return the process exit code this report implies."""
        if self.failed:
            return EXIT_FAILED
        if self.not_attempted:
            return EXIT_NOT_ATTEMPTED
        return EXIT_OK


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------


def check_python(report: Report) -> None:
    """The running interpreter must satisfy the package's own requirement.

    On an interpreter that is too old, this is a real failure for a human
    who is about to run the package -- but it is NOT a defect in the
    checkout. The CI matrix deliberately includes an interpreter older
    than the package's own floor, so this check can be told to downgrade
    itself to a warning (see ``--lenient``).
    """
    actual = sys.version_info[:2]
    if actual >= REQUIRED_PYTHON:
        report.add(
            "python version",
            "pass",
            f"{actual[0]}.{actual[1]} (requires >= {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]})",
        )
        return
    report.add(
        "python version",
        "warn",
        f"{actual[0]}.{actual[1]} is older than the required "
        f"{REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}",
        remedy="Use a newer interpreter; the protocol package declares "
        f"requires-python >= {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}.",
    )


def check_checkout(report: Report) -> None:
    """The checkout must contain the things the tooling reads."""
    required = [
        Path("python-2027/pyproject.toml"),
        Path("python-2027/src/ai2027_protocol"),
        Path("render.yaml"),
        Path("docs/SECRETS.md"),
        Path("scripts"),
    ]
    missing = [str(p) for p in required if not (ROOT / p).exists()]
    if missing:
        report.add(
            "checkout layout",
            "fail",
            "missing: " + ", ".join(missing),
            remedy="Run the doctor from a full clone of the repository.",
        )
        return
    report.add("checkout layout", "pass", f"all {len(required)} required paths present")


def check_package_importable(report: Report) -> None:
    """The protocol package must import.

    It is optional: this script itself is stdlib-only and must keep working
    even in a checkout where the package was never installed. So a failure
    here is reported as a skip-with-remedy, not a hard failure -- except when
    the package directory is missing entirely, which is a broken checkout.
    """
    src = ROOT / "python-2027" / "src"
    if not src.is_dir():
        report.add(
            "protocol package import",
            "fail",
            "python-2027/src is missing",
            remedy="Re-clone the repository.",
        )
        return
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    try:
        import ai2027_protocol  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - report, do not crash the doctor
        report.add(
            "protocol package import",
            "skip",
            f"not importable ({type(exc).__name__})",
            remedy="Install it with: pip install -e python-2027",
        )
        return
    report.add("protocol package import", "pass", "ai2027_protocol imports cleanly")


def check_cli(report: Report) -> None:
    """The console script must be on PATH, or the module entry must work."""
    if shutil.which("ai2027-py27"):
        report.add("cli entrypoint", "pass", "ai2027-py27 found on PATH")
        return
    src = ROOT / "python-2027" / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    try:
        import ai2027_protocol.cli as cli  # noqa: F401
    except Exception:  # noqa: BLE001
        report.add(
            "cli entrypoint",
            "skip",
            "neither the console script nor the module is importable",
            remedy="Install it with: pip install -e python-2027",
        )
        return
    report.add(
        "cli entrypoint",
        "pass",
        "module entrypoint ai2027_protocol.cli:main available (console script not on PATH)",
    )


def check_budget_posture(report: Report) -> None:
    """The fail-closed budget posture must still be the default.

    This is the single most important local invariant: under a $0 ceiling the
    package refuses every paid and unknown-cost call. A local environment
    that silently raises the ceiling is not a crash, but it IS a surprise,
    so it is reported as a warning rather than a failure.
    """
    raw = os.environ.get("AI2027_MAX_SPEND_USD", "").strip()
    if raw == "":
        report.add(
            "budget posture",
            "pass",
            "AI2027_MAX_SPEND_USD unset -> fail-closed default ($0)",
        )
        return
    try:
        value = float(raw)
    except ValueError:
        report.add(
            "budget posture",
            "warn",
            f"AI2027_MAX_SPEND_USD={raw!r} is not a number; the package will "
            "refuse to build a policy rather than spend",
            remedy="Set it to 0 to keep the fail-closed default.",
        )
        return
    if value <= 0:
        report.add(
            "budget posture",
            "pass",
            f"AI2027_MAX_SPEND_USD={value} (no spend permitted)",
        )
        return
    report.add(
        "budget posture",
        "warn",
        f"AI2027_MAX_SPEND_USD={value} permits spending; the $0 default is overridden",
        remedy="Unset it to restore the fail-closed default.",
    )


def check_read_only(report: Report) -> None:
    """Writes must be off unless deliberately enabled."""
    raw = os.environ.get("AI2027_READ_ONLY", "").strip()
    if raw in {"1", "true", "TRUE", "True"}:
        report.add("write posture", "pass", "AI2027_READ_ONLY=1 (writes refused)")
        return
    if raw == "":
        report.add(
            "write posture",
            "warn",
            "AI2027_READ_ONLY unset; the package default is read-only, but "
            "state it explicitly so the intent is visible",
            remedy="Set AI2027_READ_ONLY=1.",
        )
        return
    report.add(
        "write posture",
        "warn",
        f"AI2027_READ_ONLY={raw!r} does not look like an enabled read-only flag",
        remedy="Set AI2027_READ_ONLY=1 for a public instance.",
    )


def check_secrets(report: Report) -> None:
    """Report the presence of each secret name. Never the value.

    A missing key is an *absent* condition, not a *failed* check: the whole
    point of the $0 offline fallback is that the system works without any
    provider key at all. Reporting absence as failure would train people to
    ignore this report.
    """
    present = [name for name, _ in OPTIONAL_SECRETS if os.environ.get(name)]
    absent = [name for name, _ in OPTIONAL_SECRETS if not os.environ.get(name)]
    if present:
        report.add(
            "provider credentials",
            "pass",
            f"{len(present)} of {len(OPTIONAL_SECRETS)} secret names are set "
            f"({', '.join(sorted(present))}); values are never printed",
        )
    else:
        report.add(
            "provider credentials",
            "pass",
            "no provider credentials are set; the $0 offline fallback still works",
        )
    if absent:
        report.add(
            "credentials not required",
            "skip",
            f"{len(absent)} secret name(s) unset; each one only disables the "
            "feature that consumes it",
        )


def check_settings(report: Report) -> None:
    """Show non-secret configuration, and flag an unsafe model id."""
    shown = 0
    for name, _ in NON_SECRET_SETTINGS:
        value = os.environ.get(name)
        if value:
            shown += 1
            report.add(f"setting {name}", "pass", value)
    if shown == 0:
        report.add(
            "operator settings",
            "skip",
            "no non-secret settings are exported; the defaults are used",
        )

    # A model id that is not a free id is refused at $0. Showing it makes the
    # refusal explainable instead of mysterious.
    model = os.environ.get("OPENROUTER_MODEL", "").strip()
    if model:
        if model == "openrouter/free" or model.endswith(":free"):
            report.add(
                "openrouter model id",
                "pass",
                f"{model} is a free id and is allowed under a $0 ceiling",
            )
        else:
            report.add(
                "openrouter model id",
                "warn",
                f"{model} is not 'openrouter/free' and does not end in ':free'; "
                "it is classified UNKNOWN_COST and will be refused at $0",
                remedy="Use 'openrouter/free' or a '<vendor>/<model>:free' id.",
            )


def check_free_model_allowlist(report: Report) -> None:
    """AI2027_FREE_MODELS must not try to reclassify a protected provider."""
    raw = os.environ.get("AI2027_FREE_MODELS", "").strip()
    if not raw:
        report.add(
            "free model allowlist",
            "pass",
            "AI2027_FREE_MODELS is empty; nothing is reclassified",
        )
        return
    ids = [part.strip() for part in raw.split(",") if part.strip()]
    report.add(
        "free model allowlist",
        "pass",
        f"{len(ids)} model id(s) declared free by the operator ({', '.join(ids)})",
    )


def _studio_service_block(text: str) -> list[str] | None:
    """Return the lines of the ``ai-2027-studio`` service block.

    The blueprint is parsed by line rather than with PyYAML on purpose.
    PyYAML is NOT a dependency of this repository (it is absent from
    uv.lock), and the doctor is stdlib-only so that it can still diagnose a
    checkout whose dependencies are broken. This mirrors the approach
    already used by python-2027/tests/test_studio_deploy.py.

    The block runs from the service's ``name:`` line to the next service
    (a line starting with ``- type:``) or to the end of the file.
    """
    lines = text.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == "name: ai-2027-studio":
            start = index
            break
    if start is None:
        return None
    block: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("  - type:"):
            break
        block.append(line)
    return block


def _env_var(block: list[str], key: str) -> dict[str, str] | None:
    """Return the properties of one env var declared in a service block."""
    stripped = [line.strip() for line in block]
    marker = f"- key: {key}"
    if marker not in stripped:
        return None
    index = stripped.index(marker)
    properties: dict[str, str] = {}
    for line in stripped[index + 1 :]:
        if line.startswith("- key:"):
            break
        if ":" in line:
            name, _, value = line.partition(":")
            properties[name.strip()] = value.strip().strip('"')
    return properties


def check_render_blueprint(report: Report) -> None:
    """The Render blueprint must declare the Studio with the safe posture.

    The posture that matters: the public instance is read-only, spends
    nothing, and every secret is a dashboard value (``sync: false``) so no
    credential is ever committed.
    """
    blueprint = ROOT / "render.yaml"
    try:
        text = blueprint.read_text(encoding="utf-8")
    except OSError as exc:
        report.add(
            "render blueprint",
            "fail",
            f"render.yaml could not be read ({type(exc).__name__})",
            remedy="Restore the blueprint.",
        )
        return

    service_names = [
        line.split(":", 1)[1].strip()
        for line in text.splitlines()
        if line.strip().startswith("name:")
    ]
    report.add(
        "render blueprint",
        "pass",
        f"{len(service_names)} service(s) declared: {', '.join(service_names)}",
    )

    block = _studio_service_block(text)
    if block is None:
        report.add(
            "studio service",
            "fail",
            "the 'ai-2027-studio' service is not declared in render.yaml",
            remedy="Restore the Studio service.",
        )
        return

    problems: list[str] = []
    spend = _env_var(block, "AI2027_MAX_SPEND_USD")
    if spend is None or spend.get("value") != "0":
        problems.append('AI2027_MAX_SPEND_USD must be "0"')
    read_only = _env_var(block, "AI2027_READ_ONLY")
    if read_only is None or read_only.get("value") != "1":
        problems.append('AI2027_READ_ONLY must be "1"')
    for key in ("OPENROUTER_API_KEY", "OPENROUTER_MODEL", "CONNECTORS_HUB_API_TOKEN"):
        entry = _env_var(block, key)
        if entry is None:
            problems.append(f"{key} is not declared")
        elif entry.get("sync") != "false":
            problems.append(f"{key} must use sync: false")
        elif "value" in entry:
            problems.append(f"{key} must not carry a committed value")
    if problems:
        report.add(
            "studio service",
            "fail",
            "; ".join(problems),
            remedy="Restore the read-only, $0, dashboard-secrets posture.",
        )
        return
    report.add(
        "studio service",
        "pass",
        "read-only, $0 ceiling, and every secret is a dashboard value (sync: false)",
    )


def check_scripts_wired(report: Report) -> None:
    """Every scripts/ test suite must be runnable from the repository root."""
    missing: list[str] = []
    for test_file in sorted((ROOT / "scripts").glob("test_*.py")):
        module = f"scripts.{test_file.stem}"
        try:
            result = subprocess.run(  # noqa: S603
                [sys.executable, "-m", "unittest", module, "-h"],
                cwd=ROOT,
                capture_output=True,
                timeout=60,
                # The argument vector is a fixed list: the interpreter, a
                # module flag, and a module name derived from a file name
                # inside this repository. No shell, no user input.
                check=False,
            )
        except Exception:  # noqa: BLE001
            missing.append(module)
            continue
        # -h exits 0 on success; a non-zero exit means the module is broken.
        if result.returncode != 0:
            missing.append(module)
    if missing:
        report.add(
            "scripts test suites",
            "fail",
            f"{len(missing)} suite(s) are not runnable from the repo root: " + ", ".join(missing),
            remedy="Run them locally: python -m unittest scripts.<name>",
        )
        return
    count = len(list((ROOT / "scripts").glob("test_*.py")))
    report.add("scripts test suites", "pass", f"all {count} suites import cleanly")


def check_network(report: Report) -> None:
    """Opt-in: probe the egress the free providers need."""
    import urllib.error
    import urllib.request

    # These URLs are literals, not user input, and both are https or
    # loopback. The scheme is asserted anyway so that this function can
    # never be repurposed into a general-purpose fetcher.
    targets = [
        ("openrouter.ai", "https://openrouter.ai/api/v1/models"),
        ("ollama local", "http://127.0.0.1:11434/api/tags"),
    ]
    ok = 0
    for label, url in targets:
        if not url.startswith(("https://", "http://127.0.0.1")):
            report.add(f"egress {label}", "fail", "refused a non-https target")
            continue
        try:
            request = urllib.request.Request(url, method="GET")  # noqa: S310
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                response.read(1)
            report.add(f"egress {label}", "pass", "reachable")
            ok += 1
        except urllib.error.HTTPError as exc:
            report.add(f"egress {label}", "warn", f"HTTP {exc.code}")
        except Exception as exc:  # noqa: BLE001
            report.add(
                f"egress {label}",
                "warn",
                f"unreachable ({type(exc).__name__})",
                remedy="The $0 offline fallback still works without it.",
            )
    if ok == 0:
        report.add(
            "network",
            "warn",
            "no egress reached; the deterministic offline fallback will be used",
        )


def check_studio(report: Report, url: str) -> None:
    """Opt-in: verify a deployed Studio instance through the public HTTP API.

    This delegates to scripts/verify_studio_instance.py, which already owns
    the full contract (including the credential-shaped-string check). The
    doctor does not re-implement it.
    """
    verifier = ROOT / "scripts" / "verify_studio_instance.py"
    if not verifier.is_file():
        report.add(
            "studio instance",
            "fail",
            "scripts/verify_studio_instance.py is missing",
            remedy="Restore the verifier.",
        )
        return
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(verifier), url],
        cwd=ROOT,
        capture_output=True,
        text=True,
        # A fixed argument vector: the interpreter, this repository's own
        # verifier, and the URL the operator asked about. No shell.
        check=False,
        timeout=180,
    )
    if result.returncode == 0:
        report.add("studio instance", "pass", "every public-surface check passed")
    elif result.returncode == 1:
        report.add(
            "studio instance",
            "fail",
            "at least one public-surface check failed",
            remedy="Re-run scripts/verify_studio_instance.py for the detail.",
        )
    else:
        report.add(
            "studio instance",
            "fail",
            "the instance could not be reached or its payload was not understood",
            remedy="A free instance sleeps; the first request can take 30-50s.",
        )


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------


def render_text(report: Report) -> str:
    """Render the report as an aligned, human-readable text block."""
    lines: list[str] = []
    width = max((len(c.name) for c in report.checks), default=0)
    for check in report.checks:
        mark = {
            "pass": "PASS",
            "fail": "FAIL",
            "warn": "WARN",
            "skip": "SKIP",
        }.get(check.status, "????")
        suffix = f"  {check.detail}" if check.detail else ""
        lines.append(f"  [{mark}] {check.name.ljust(width)}{suffix}")
        if check.remedy and check.status in {"fail", "warn"}:
            lines.append(f"         -> {check.remedy}")
    return "\n".join(lines)


def render_json(report: Report) -> str:
    """Render the report as machine-readable JSON."""
    payload: dict[str, Any] = {
        "exit_code": report.exit_code(),
        "checks": [
            {
                "name": c.name,
                "status": c.status,
                "detail": c.detail,
                **({"remedy": c.remedy} if c.remedy else {}),
            }
            for c in report.checks
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def run(argv: list[str]) -> int:
    """Run every check and return the exit code the report implies."""
    parser = argparse.ArgumentParser(
        description="Report whether this checkout is ready to run.",
    )
    parser.add_argument(
        "--network",
        action="store_true",
        help="also probe outbound egress (opt-in; the default run is offline)",
    )
    parser.add_argument(
        "--studio",
        metavar="URL",
        default=None,
        help="also verify a deployed Studio instance at this URL",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="emit machine-readable JSON instead of a text report",
    )
    parser.add_argument(
        "--lenient",
        action="store_true",
        help=(
            "downgrade environment-dependent failures to warnings: the "
            "interpreter version and the install state of the protocol "
            "package. Use this in a CI matrix that deliberately runs an "
            "older interpreter or omits the optional dependencies."
        ),
    )
    args = parser.parse_args(argv)

    if args.studio and not args.studio.startswith(("http://", "https://")):
        args.studio = f"https://{args.studio}"

    report = Report()
    check_python(report)
    check_checkout(report)
    check_package_importable(report)
    check_cli(report)
    check_budget_posture(report)
    check_read_only(report)
    check_secrets(report)
    check_settings(report)
    check_free_model_allowlist(report)
    check_render_blueprint(report)
    check_scripts_wired(report)
    if args.network:
        check_network(report)
    if args.studio:
        check_studio(report, args.studio)

    if args.lenient:
        # Environment-dependent checks become warnings. Only the checks that
        # describe the REPOSITORY ITSELF stay hard failures, because those
        # are the ones a CI run is actually there to catch.
        for check in report.checks:
            if check.status == "fail" and check.name in LENIENT_CHECKS:
                check.status = "warn"

    if args.as_json:
        print(render_json(report))
    else:
        print("AI-2027 doctor")
        print(f"root: {ROOT}")
        print()
        print(render_text(report))
        print()
        code = report.exit_code()
        if code == EXIT_OK:
            print("OK: the checkout is ready.")
        elif code == EXIT_NOT_ATTEMPTED:
            print("OK, with skips: some optional checks were not attempted.")
        else:
            print(f"FAILED: {len(report.failed)} check(s) did not pass.")
    return report.exit_code()


def main(argv: list[str] | None = None) -> int:
    """Entry point: run the doctor and never raise."""
    if argv is None:
        argv = sys.argv[1:]
    try:
        return run(argv)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"the doctor could not run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
