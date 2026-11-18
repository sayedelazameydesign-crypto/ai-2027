#!/usr/bin/env python3
"""Stage 1 (Mode 1 · local) read-only probe for Tencent/BrowserSkill.

Mirrors `render_control_adapter/probe.py` and `scripts/gemini_live_check.py`:
  * performs **reads only** (`bsk --version`, `bsk status --json`) with
    `BSK_AUTO_START=0` so probing never spawns an orphan background daemon;
  * asserts `mutations_performed == 0` and refuses to exit 0 without a live,
    Protocol-2.0-compatible daemon AND at least one responsive browser;
  * checks `BSK_HOME` permissions and whether `local-pairings.json` exists
    **without ever reading its contents**;
  * uses distinct exit codes so "not installed on a CI runner" is never confused
    with "broken":
        0  READY   — binary + daemon + Protocol 2.0 + >=1 responsive browser
        1  FAILED  — protocol mismatch, unresponsive browser, or malformed reply
        2  SKIPPED — `bsk` not installed, daemon not running, or no browser connected
        3  BLOCKED — OS/sandbox permission denied accessing binary or BSK_HOME socket

Usage:
    python3 browser_skill_adapter/probe.py
    python3 browser_skill_adapter/probe.py --json
    python3 browser_skill_adapter/probe.py --report browser_skill_adapter/phase0-report.json

Stdlib only. Python 3.10+.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from adapter import (  # noqa: E402
    BskCommandError,
    MODE_STAGES,
    PINNED_CLI_VERSION,
    PROTOCOL_VERSION,
    Redactor,
    SubprocessBskRunner,
    check_protocol_status,
)

OK, FAILED, SKIPPED, BLOCKED = 0, 1, 2, 3


def _default_bsk_home(env: dict[str, str]) -> Path:
    explicit = (env.get("BSK_HOME") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    home = Path(env.get("HOME") or env.get("USERPROFILE") or str(Path.home()))
    return home / ".bsk"


def _inspect_bsk_home(bsk_home: Path) -> dict[str, Any]:
    """Metadata-only inspection of `BSK_HOME` — never reads `local-pairings.json`."""
    info: dict[str, Any] = {
        "exists": bsk_home.is_dir(),
        "daemon_json_present": False,
        "pairing_file_present": False,
        "audit_dir_present": False,
        "private_permissions": None,
    }
    if not bsk_home.is_dir():
        return info
    try:
        st = bsk_home.stat()
        if os.name != "nt":
            info["private_permissions"] = (stat.S_IMODE(st.st_mode) & 0o077) == 0
        info["daemon_json_present"] = (bsk_home / "daemon.json").is_file()
        info["pairing_file_present"] = (bsk_home / "local-pairings.json").is_file()
        info["audit_dir_present"] = (bsk_home / "audit").is_dir()
    except PermissionError:
        info["permission_denied"] = True
    except OSError:
        pass
    return info


def _summarise_browser(entry: dict[str, Any]) -> dict[str, Any]:
    """Safe, non-sensitive summary of one connected browser instance."""
    raw_id = str(entry.get("instance_id") or "")
    short_id = f"{raw_id[:4]}…" if len(raw_id) > 4 else raw_id
    return {
        "instance_hint": short_id,
        "browser_name": str(entry.get("browser_name") or "unknown"),
        "browser_version": str(entry.get("browser_version") or "unknown"),
        "extension_version": str(entry.get("extension_version") or "unknown"),
        "extension_protocol_version": str(entry.get("extension_protocol_version") or ""),
        "has_label": bool(str(entry.get("label") or "").strip()),
        "session_count": int(entry.get("session_count") or 0),
        "version_skew": bool(entry.get("version_skew")),
        "unresponsive": bool(entry.get("unresponsive")),
    }


def run_probe(
    *,
    bsk_bin: str | Path | None = None,
    bsk_home: str | Path | None = None,
    env: dict[str, str] | None = None,
    runner: Any | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Execute the Stage 1 read-only probe and return a structured report."""
    started = time.perf_counter()
    src_env = dict(os.environ if env is None else env)
    redactor = Redactor()
    resolved_home = Path(bsk_home).expanduser() if bsk_home else _default_bsk_home(src_env)
    home_meta = _inspect_bsk_home(resolved_home)

    active_runner = runner or SubprocessBskRunner(
        bsk_bin=bsk_bin,
        bsk_home=resolved_home if (bsk_home or src_env.get("BSK_HOME")) else None,
        base_env=src_env,
        redactor=redactor,
    )

    report: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "adapter": "browser_skill_adapter",
        "mode": "local",
        "stage": "1/3",
        "roadmap_modes": {k: {"stage": v["stage"], "enabled": v["enabled"]}
                          for k, v in MODE_STAGES.items()},
        "pinned_cli_version": PINNED_CLI_VERSION,
        "required_protocol": PROTOCOL_VERSION,
        "auto_start": False,
        "mutations_performed": 0,
        "bsk_home": home_meta,
        "verdict": "SKIPPED",
        "exit_code": SKIPPED,
        "ready": False,
        "cli_version": None,
        "daemon": None,
        "browsers": [],
        "warnings": [],
        "next_action": "",
        "elapsed_ms": 0.0,
    }

    if home_meta.get("permission_denied"):
        report["verdict"] = "BLOCKED"
        report["exit_code"] = BLOCKED
        report["reason"] = "permission_denied_bsk_home"
        report["next_action"] = "Check filesystem permissions on BSK_HOME (expected owner 0700)."
        report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return report

    # Step 1: binary + `--version` (zero side effects)
    try:
        ver_text = active_runner.call(["--version"], timeout=timeout, expect_json=False)
    except BskCommandError as exc:
        if exc.code == "bsk_not_installed":
            report["verdict"] = "SKIPPED"
            report["exit_code"] = SKIPPED
            report["reason"] = "bsk_not_installed"
            report["next_action"] = (
                "Install pinned bsk v0.5.0 on your machine: ./scripts/install_browserskill.sh "
                "(or scripts/install_browserskill.ps1 on Windows)"
            )
        elif exc.code == "permission_denied":
            report["verdict"] = "BLOCKED"
            report["exit_code"] = BLOCKED
            report["reason"] = "permission_denied_binary"
            report["next_action"] = "Verify executable permissions on the bsk binary."
        else:
            report["verdict"] = "FAILED"
            report["exit_code"] = FAILED
            report["reason"] = exc.code
            report["detail"] = redactor.scrub_text(exc.message)
            report["next_action"] = "Re-install pinned bsk via ./scripts/install_browserskill.sh."
        report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return report

    report["cli_version"] = redactor.scrub_text(str(ver_text))
    if PINNED_CLI_VERSION not in report["cli_version"]:
        report["warnings"].append(
            f"CLI reports {report['cli_version']!r}; repository pins v{PINNED_CLI_VERSION}"
        )

    # Step 2: `BSK_AUTO_START=0 bsk status --json` (read-only UDS/pipe probe)
    try:
        status_raw = active_runner.call(["status"], timeout=timeout, expect_json=True)
    except BskCommandError as exc:
        if exc.code == "daemon_unreachable":
            report["verdict"] = "SKIPPED"
            report["exit_code"] = SKIPPED
            report["reason"] = "daemon_not_running"
            report["next_action"] = (
                "Start the local daemon in your terminal: `bsk daemon start`, "
                "then re-run `python3 browser_skill_adapter/probe.py`."
            )
        elif exc.code == "permission_denied":
            report["verdict"] = "BLOCKED"
            report["exit_code"] = BLOCKED
            report["reason"] = "permission_denied_ipc"
            report["next_action"] = (
                "Sandbox/OS blocked access to BSK_HOME/run/daemon.sock; "
                "ensure daemon and adapter share the same OS user and BSK_HOME."
            )
        else:
            report["verdict"] = "FAILED"
            report["exit_code"] = FAILED
            report["reason"] = exc.code
            report["detail"] = redactor.scrub_text(exc.message)
            report["next_action"] = "Inspect `bsk doctor` and `bsk logs` on your local machine."
        report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return report

    # Step 3: Protocol 2.0 + extension health verification
    try:
        compat = check_protocol_status(status_raw)
    except BskCommandError as exc:
        report["verdict"] = "FAILED"
        report["exit_code"] = FAILED
        report["reason"] = exc.code
        report["detail"] = redactor.scrub_text(exc.message)
        report["next_action"] = (
            f"Update bsk CLI to v{PINNED_CLI_VERSION}, restart `bsk daemon`, "
            "then update the Chrome/Edge extension to v0.5.0."
        )
        report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return report

    raw_browsers = status_raw.get("browsers") if isinstance(status_raw, dict) else []
    report["browsers"] = [_summarise_browser(b) for b in (raw_browsers or []) if isinstance(b, dict)]
    report["daemon"] = {
        "version": compat["daemon_version"],
        "pinned_version_match": compat["pinned_version_match"],
        "protocol_version": compat["protocol_version"],
        "ws_port": status_raw.get("ws_port") if isinstance(status_raw, dict) else None,
        "local_auth_mode": compat["local_auth_mode"],
        "paired_devices": compat["paired_devices"],
        "browsers_total": compat["browsers_total"],
        "browsers_healthy": compat["browsers_healthy"],
    }

    if home_meta.get("private_permissions") is False:
        report["warnings"].append("BSK_HOME is group/world-accessible; run `chmod 700 ~/.bsk`")
    if compat["version_skew_ids"]:
        report["warnings"].append(
            "version_skew detected between daemon and extension; align both on v0.5.0"
        )
    if compat["paired_devices"] == 0 and not home_meta.get("pairing_file_present"):
        report["warnings"].append(
            "local connection is unpaired; run `bsk daemon pair` for verified identity "
            "(never log or share the pairing link)"
        )
    if not home_meta.get("audit_dir_present"):
        report["warnings"].append(
            "operation audit directory not found (off by default); enable Operation Audit "
            "in the extension Quick Actions for local task review"
        )

    if compat["browsers_total"] == 0:
        report["verdict"] = "SKIPPED"
        report["exit_code"] = SKIPPED
        report["reason"] = "no_browser_connected"
        report["next_action"] = (
            "Install and enable the BrowserSkill extension in Chrome "
            "(hhcmgoofomhgciiibhipgmgkgnoenaoi) or Edge "
            "(emacgiaaaiojkkpkddmmdfhmokgmnikg), then re-run this probe."
        )
    elif compat["browsers_healthy"] == 0:
        report["verdict"] = "FAILED"
        report["exit_code"] = FAILED
        report["reason"] = "all_browsers_unresponsive_or_incompatible"
        report["next_action"] = (
            "Connected extension is unresponsive or on an incompatible protocol; "
            "reload the extension and verify both CLI and extension are v0.5.0."
        )
    else:
        if report["mutations_performed"] != 0:
            raise AssertionError("probe performed a mutation — refusing to report READY")
        report["verdict"] = "READY"
        report["exit_code"] = OK
        report["ready"] = True
        report["reason"] = "local_browser_verified_online"
        extra = (" (multiple browsers connected — pass `--browser <label>` on tasks)"
                 if compat["browsers_healthy"] > 1 else "")
        report["next_action"] = (
            "Run a verified local task: "
            f"`python3 browser_skill_adapter/run_local_task.py --url https://example.com`{extra}"
        )

    report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return redactor.scrub(report)


def _write_private_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if os.name == "nt":
        path.write_text(payload, encoding="utf-8")
        return
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bsk-bin", help="explicit path to the `bsk` binary")
    ap.add_argument("--bsk-home", help="explicit BSK_HOME directory (default: ~/.bsk)")
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--report", type=Path,
                    help="write full JSON report to this gitignored path (mode 0600)")
    ap.add_argument("--json", action="store_true", help="emit JSON on stdout")
    args = ap.parse_args(argv)

    rep = run_probe(bsk_bin=args.bsk_bin, bsk_home=args.bsk_home, timeout=args.timeout)
    if args.report:
        _write_private_report(args.report, rep)

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(f"BROWSERSKILL PROBE — Stage {rep['stage']} (mode={rep['mode']}) · "
              f"verdict={rep['verdict']} · mutations={rep['mutations_performed']} · "
              f"{rep['elapsed_ms']} ms")
        if rep.get("cli_version"):
            print(f"  cli      : {rep['cli_version']} (pinned: v{rep['pinned_cli_version']})")
        if rep.get("daemon"):
            d = rep["daemon"]
            print(f"  daemon   : v{d['version']} · protocol {d['protocol_version']} · "
                  f"port {d['ws_port']} · paired={d['paired_devices']} · "
                  f"browsers={d['browsers_healthy']}/{d['browsers_total']} healthy")
        if rep.get("reason"):
            print(f"  reason   : {rep['reason']}")
        if rep.get("detail"):
            print(f"  detail   : {rep['detail']}")
        for w in rep.get("warnings") or []:
            print(f"  warning  : {w}")
        if rep.get("next_action"):
            print(f"  next     : {rep['next_action']}")
        if args.report:
            print(f"  report   : {args.report}")
    return int(rep["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
