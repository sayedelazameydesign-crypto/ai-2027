#!/usr/bin/env python3
"""Contract tests for the 24/7 VM bundle in deployment/vm/ (offline, $0, no root).

These pin the security model the plan (docs/DEPLOY-24-7-PLAN.md) relies on, so a
later edit cannot silently widen it:

* every long-running service binds 127.0.0.1 and runs unprivileged and sandboxed;
* only Caddy is public, and it forwards exactly /webhook and /healthz;
* each service reads only its own env file, and the templates hold no secret values;
* bootstrap installs/enables/probes exactly the units that exist;
* the credential generator embedded in bootstrap.sh writes owner-only files and
  refuses to overwrite existing secrets.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VM = ROOT / "deployment" / "vm"
UNITS = VM / "systemd"
LONG_RUNNING = {
    "ai2027-webhook": 8080,
    "ai2027-studio": 8790,
    "ai2027-live-agent": 8090,
    "ai2027-hud": 8091,
}
REQUIRED_HARDENING = {
    "NoNewPrivileges": "yes",
    "ProtectSystem": "strict",
    "ProtectHome": "yes",
    "PrivateTmp": "yes",
    "RestrictSUIDSGID": "yes",
    "CapabilityBoundingSet": "",
    "User": "ai2027",
    "Group": "ai2027",
    "Restart": "always",
}


def unit_entries(name: str) -> list[tuple[str, str]]:
    """Parse a unit into (key, value) pairs, keeping repeated keys (systemd allows them)."""
    out: list[tuple[str, str]] = []
    for raw in (UNITS / name).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";", "[")):
            continue
        key, _, value = line.partition("=")
        out.append((key.strip(), value.strip()))
    return out


def unit_map(name: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for key, value in unit_entries(name):
        result.setdefault(key, []).append(value)
    return result


class ServiceUnitTests(unittest.TestCase):
    def test_every_long_running_unit_is_loopback_only(self) -> None:
        for svc, port in LONG_RUNNING.items():
            text = (UNITS / f"{svc}.service").read_text(encoding="utf-8")
            body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
            with self.subTest(svc=svc):
                self.assertNotIn("0.0.0.0", body)
                self.assertNotIn("::", re.sub(r"\[\w+\]", "", body))
                self.assertIn(str(port), body)
                self.assertIn("127.0.0.1", body)

    def test_every_long_running_unit_is_unprivileged_and_sandboxed(self) -> None:
        for svc in LONG_RUNNING:
            entries = unit_map(f"{svc}.service")
            with self.subTest(svc=svc):
                for key, expected in REQUIRED_HARDENING.items():
                    self.assertEqual(entries.get(key), [expected], f"{svc}: {key}")
                self.assertTrue(entries.get("MemoryMax"), f"{svc}: MemoryMax missing")
                state = entries.get("StateDirectory", [""])[0]
                self.assertTrue(state.startswith("ai2027/"), f"{svc}: StateDirectory {state!r}")
                # Data paths named in the unit must live under its own StateDirectory.
                own = f"/var/lib/{state}"
                for value in sum((v for k, v in entries.items() if k in ("Environment", "ExecStart")), []):
                    for path in re.findall(r"/var/lib/ai2027/[\w./-]*", value):
                        self.assertTrue(path.startswith(own), f"{svc} writes outside {own}: {path}")

    def test_each_service_reads_only_its_own_env_file(self) -> None:
        for svc in LONG_RUNNING:
            files = unit_map(f"{svc}.service").get("EnvironmentFile", [])
            short = svc.removeprefix("ai2027-")
            with self.subTest(svc=svc):
                self.assertLessEqual(len(files), 1)
                for f in files:
                    self.assertEqual(f.lstrip("-"), f"/etc/ai2027/{short}.env")

    def test_exec_start_targets_exist_in_the_repo(self) -> None:
        for svc in LONG_RUNNING:
            exec_start = unit_map(f"{svc}.service")["ExecStart"][0]
            argv = shlex.split(exec_start)
            with self.subTest(svc=svc):
                if argv[0] == "/usr/bin/python3":
                    self.assertTrue((ROOT / argv[1]).is_file(), argv[1])
                else:
                    self.assertTrue(argv[0].startswith("/opt/ai2027/"), argv[0])
                    rel = argv[0].removeprefix("/opt/ai2027/")
                    self.assertTrue(rel.startswith("python-2027/.venv/bin/"), rel)

    def test_studio_spend_ceiling_is_forced_by_the_unit(self) -> None:
        env = " ".join(unit_map("ai2027-studio.service")["Environment"])
        self.assertIn("AI2027_MAX_SPEND_USD=0", env)

    def test_timers_have_matching_services(self) -> None:
        for timer in UNITS.glob("*.timer"):
            with self.subTest(timer=timer.name):
                self.assertTrue((UNITS / timer.name.replace(".timer", ".service")).is_file())


class CaddyEdgeTests(unittest.TestCase):
    def setUp(self) -> None:
        raw = (VM / "Caddyfile").read_text(encoding="utf-8")
        self.body = "\n".join(l for l in raw.splitlines() if not l.lstrip().startswith("#"))

    def test_exactly_two_public_paths_go_to_the_webhook(self) -> None:
        self.assertRegex(self.body, r"@public\s+path\s+/webhook\s+/healthz\s*\n")
        proxies = re.findall(r"reverse_proxy\s+(\S+)", self.body)
        self.assertEqual(proxies, ["127.0.0.1:8080"])
        for port in (8790, 8090, 8091):
            self.assertNotIn(str(port), self.body)

    def test_everything_else_is_404_and_body_is_capped(self) -> None:
        self.assertRegex(self.body, r"handle\s*\{\s*respond 404\s*\}")
        self.assertIn("max_size 1MB", self.body)

    def test_placeholders_are_rendered_by_bootstrap(self) -> None:
        boot = (VM / "bootstrap.sh").read_text(encoding="utf-8")
        for ph in ("__AI2027_PUBLIC_HOST__", "__AI2027_ACME_EMAIL__"):
            self.assertIn(ph, self.body)
            self.assertIn(ph, boot)


class EnvTemplateTests(unittest.TestCase):
    SECRET = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|WEBHOOK|FROM)$")

    def test_templates_hold_no_secret_values(self) -> None:
        for tpl in (VM / "env").glob("*.env.example"):
            for line in tpl.read_text(encoding="utf-8").splitlines():
                if not line or line.startswith("#"):
                    continue
                key, _, value = line.partition("=")
                with self.subTest(tpl=tpl.name, key=key):
                    if self.SECRET.search(key):
                        self.assertEqual(value, "", f"{tpl.name}: {key} must be empty")

    def test_every_env_file_a_unit_needs_has_a_source(self) -> None:
        boot = (VM / "bootstrap.sh").read_text(encoding="utf-8")
        for svc in LONG_RUNNING:
            for f in unit_map(f"{svc}.service").get("EnvironmentFile", []):
                name = Path(f.lstrip("-")).name.removesuffix(".env")
                with self.subTest(env=name):
                    templated = (VM / "env" / f"{name}.env.example").is_file()
                    generated = name == "live-agent" and "live-agent.env" in boot
                    self.assertTrue(templated or generated, name)
                    if templated:
                        self.assertIn(f"make_env {name}", boot)


class BootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.boot = (VM / "bootstrap.sh").read_text(encoding="utf-8")

    def test_services_and_timers_lists_match_the_unit_files(self) -> None:
        services = re.search(r"^SERVICES=\(([^)]*)\)", self.boot, re.M).group(1).split()
        timers = re.search(r"^TIMERS=\(([^)]*)\)", self.boot, re.M).group(1).split()
        self.assertEqual(sorted(services), sorted(LONG_RUNNING))
        self.assertEqual(sorted(timers), sorted(p.name for p in UNITS.glob("*.timer")))

    def test_bootstrap_and_watchdog_probe_every_service_port(self) -> None:
        watchdog = (VM / "healthcheck.sh").read_text(encoding="utf-8")
        for svc, port in LONG_RUNNING.items():
            with self.subTest(svc=svc):
                self.assertIn(f"127.0.0.1:{port}/", self.boot)
                self.assertRegex(watchdog, rf"probe {re.escape(svc)}\s+http://127\.0\.0\.1:{port}/")

    def test_gate_runs_before_any_restart_and_can_roll_back(self) -> None:
        gate_at = self.boot.index("gate || rollback")
        self.assertLess(gate_at, self.boot.index('systemctl restart "${SERVICES[@]}"'))
        self.assertIn("--locked --no-dev", self.boot)
        self.assertIn('checkout --quiet --detach "$PREV"', self.boot)

    def test_shell_scripts_parse(self) -> None:
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash not available")
        for script in ("bootstrap.sh", "healthcheck.sh", "backup.sh"):
            with self.subTest(script=script):
                proc = subprocess.run([bash, "-n", str(VM / script)], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertTrue(os.access(VM / script, os.X_OK), f"{script} not executable")

    @unittest.skipIf(sys.platform.startswith("win"), "POSIX permission bits")
    def test_embedded_credential_generator_is_owner_only_and_never_overwrites(self) -> None:
        match = re.search(r"python3 - \"\$ETC\" <<'PY'\n(.*?)\nPY\n", self.boot, re.S)
        self.assertIsNotNone(match)
        code = match.group(1)
        with tempfile.TemporaryDirectory() as etc:
            old = os.umask(0o077)
            try:
                first = subprocess.run([sys.executable, "-c", code, etc], capture_output=True, text=True)
                self.assertEqual(first.returncode, 0, first.stderr)
                self.assertEqual(first.stdout, "", "generator must never print secrets")
                for name in ("live-agent.env", "live-agent-operator.json"):
                    mode = stat.S_IMODE(os.stat(Path(etc) / name).st_mode)
                    self.assertEqual(mode & 0o077, 0, f"{name} mode {oct(mode)}")
                cred = json.loads((Path(etc) / "live-agent-operator.json").read_text())
                env = dict(
                    l.split("=", 1)
                    for l in (Path(etc) / "live-agent.env").read_text().splitlines()
                    if l and not l.startswith("#")
                )
                self.assertEqual(env["AI2027_READ_ONLY"], "0")
                self.assertEqual(env["AI2027_WRITE_TOKEN"], cred["write_token"])
                self.assertGreaterEqual(len(cred["write_token"]), 32)
                hashes = json.loads(env["AI2027_READ_TOKEN_HASHES"])
                self.assertEqual(
                    hashes[cred["read_id"]], hashlib.sha256(cred["read_secret"].encode()).hexdigest()
                )
                before = (Path(etc) / "live-agent.env").read_bytes()
                second = subprocess.run([sys.executable, "-c", code, etc], capture_output=True, text=True)
                self.assertNotEqual(second.returncode, 0, "must refuse to overwrite secrets")
                self.assertEqual((Path(etc) / "live-agent.env").read_bytes(), before)
            finally:
                os.umask(old)


if __name__ == "__main__":
    unittest.main()
