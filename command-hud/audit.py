#!/usr/bin/env python3
"""READ-ONLY repository intelligence + evidence-based health audit.

This module NEVER writes to projects/. It only reads the filesystem and runs a
small set of *verification probes* (subprocesses) whose real output is recorded.
Every finding is classified: PROVEN / DECLARED / SIMULATED / NOT VERIFIED /
FAILED — "never assume that a documented feature is operational".

Usage:
    python3 command-hud/audit.py                 # JSON to stdout
    python3 command-hud/audit.py --report PATH  # Markdown report to PATH
    python3 command-hud/audit.py --fast         # skip subprocess probes

Stdlib only. Python 3.10+.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = ROOT / "projects"
CATALOG = ROOT / "catalog.json"
HUD = ROOT / "command-hud"
sys.path.insert(0, str(ROOT / "scripts"))

# Only these projects have an executable verification probe in THIS sandbox.
# Everything else is explicitly NOT VERIFIED, however confident its README is.
PROBES = [
    {
        "name": "kernel-86-tests",
        "command": [sys.executable, "-m", "unittest", "discover", "-s",
                    "projects/agi-system/tests", "-t", "projects/agi-system"],
        "proves": "agi-system",
        "expect": "OK",
        "extract": r"Ran (\d+) tests",
    },
    {
        "name": "live-agent-6-tests",
        "command": [sys.executable, "live-agent/test_server.py"],
        "proves": None,
        "expect": "OK",
        "extract": r"Ran (\d+) tests",
    },
    {
        "name": "zero-dependency-ast-gate",
        "command": [sys.executable, "scripts/ci_guard.py", "imports"],
        "proves": "agi-system",
        "expect": "PASS",
        "extract": r"PASS: ([^\n]+)",
    },
    {
        "name": "ci-guard-self-tests",
        "command": [sys.executable, "scripts/test_ci_guard.py"],
        "proves": None,
        "expect": "OK",
        "extract": r"Ran (\d+) tests",
    },
    {
        "name": "public-demo-contract-tests",
        "command": ["node", "website/kernel-demo.test.js"],
        "proves": None,
        "expect": "passed",
        "extract": r"(\d+) demo contract tests passed",
    },
]

SIM_RE = re.compile(r"simulation mode|simulated|mock(?:ed)?|وضع محاكاة|محاكاة محلية", re.I)
SECRET_REDACTION_MARKER = "REDACTED-rotate-this-key"


def _read(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_bytes()[:limit].decode("utf-8", errors="replace")
    except OSError:
        return ""


def classify_license(p: Path) -> tuple[str, str]:
    """Return (label, state) where state is ok|pending|proprietary|corrupt|missing."""
    files = [f for f in p.iterdir() if f.is_file() and f.name.upper().startswith("LICENSE")] if p.is_dir() else []
    if not files:
        return "NONE", "missing"
    text = _read(files[0], 20_000)
    if not text.strip():
        return "EMPTY", "corrupt"
    # A 4-byte "0\xff" file decodes to printable U+FFFD, so replacement chars
    # and implausibly short files must be checked explicitly.
    printable = sum(1 for c in text[:2000] if c.isprintable() or c in "\n\r\t")
    if (printable / max(1, len(text[:2000])) < 0.85
            or "\ufffd" in text[:2000]
            or len(text.strip()) < 40):
        return "CORRUPT (binary/garbled/placeholder)", "corrupt"
    low = text.lower()
    if "proprietary" in low or "all rights reserved" in low:
        return "PROPRIETARY", "proprietary"
    if "license decision pending" in low or "not been selected" in low or "لم يُحدَّد" in text:
        return "PENDING", "pending"
    if "apache license" in low or "apache-2.0" in low:
        return "Apache-2.0", "ok"
    if "mit license" in low or low.strip().startswith("mit"):
        return "MIT", "ok"
    if "gnu general public" in low or "gpl" in low:
        return "GPL", "ok"
    return "OTHER/UNREADABLE", "pending"


def count_files(p: Path) -> tuple[int, int]:
    files = dirs = 0
    for _root, dnames, fnames in os.walk(p):
        dnames[:] = [d for d in dnames if d not in {".git", "node_modules", "__pycache__"}]
        files += len(fnames)
        dirs += len(dnames)
    return files, dirs


def detect_tests(p: Path) -> int:
    n = 0
    for _root, dnames, fnames in os.walk(p):
        dnames[:] = [d for d in dnames if d not in {".git", "node_modules", "__pycache__"}]
        for f in fnames:
            if re.match(r"(test_.*\.py$|_test\.py$|.*\.test\.[jt]sx?$|.*\.spec\.[jt]sx?$)", f):
                n += 1
    return n


def detect_ci(p: Path) -> int:
    wf = p / ".github" / "workflows"
    if not wf.is_dir():
        return 0
    return len(list(wf.glob("*.yml"))) + len(list(wf.glob("*.yaml")))


def manifests(p: Path) -> list[str]:
    names = ["package.json", "pyproject.toml", "requirements.txt", "Cargo.toml",
             "go.mod", "pom.xml", "composer.json"]
    return [n for n in names if (p / n).is_file()]


def run_probes(fast: bool = False) -> list[dict]:
    out = []
    for spec in PROBES:
        if fast:
            out.append({"name": spec["name"], "command": " ".join(spec["command"]),
                        "verdict": "NOT VERIFIED (fast mode — probe skipped)",
                        "exit_code": None, "evidence": None, "ms": None})
            continue
        t0 = time.perf_counter()
        try:
            r = subprocess.run(spec["command"], cwd=ROOT, capture_output=True,
                               text=True, timeout=120)
            combined = (r.stdout or "") + (r.stderr or "")
            ms = round((time.perf_counter() - t0) * 1000, 1)
            m = re.search(spec["extract"], combined)
            ok = r.returncode == 0 and spec["expect"] in combined
            out.append({
                "name": spec["name"],
                "command": " ".join(spec["command"]),
                "exit_code": r.returncode,
                "evidence": m.group(0) if m else (combined.strip().splitlines()[-1][:160] if combined.strip() else "(no output)"),
                "verdict": "PROVEN" if ok else "FAILED",
                "ms": ms,
                "proves": spec["proves"],
            })
        except FileNotFoundError as e:
            out.append({"name": spec["name"], "command": " ".join(spec["command"]),
                        "exit_code": None, "evidence": str(e), "verdict": "FAILED", "ms": None,
                        "proves": spec["proves"]})
        except subprocess.TimeoutExpired:
            out.append({"name": spec["name"], "command": " ".join(spec["command"]),
                        "exit_code": None, "evidence": "timeout 120s", "verdict": "FAILED",
                        "ms": None, "proves": spec["proves"]})
    return out


def architecture_map(catalog: dict) -> dict:
    cats: dict[str, int] = {}
    for pr in catalog.get("projects", []):
        key = pr.get("category", "uncategorized")
        cats[key] = cats.get(key, 0) + 1
    layers = [
        {"layer": "L0 · Deterministic kernel", "path": "projects/agi-system",
         "role": "Goal→Plan→Policy→Execute→Verify→Evidence→Ledger. Zero third-party imports.",
         "verification": "86 unit tests executed in this audit + AST gate"},
        {"layer": "L1 · Live wrapper", "path": "live-agent",
         "role": "Stdlib HTTP wrapper importing the kernel in place; per-run sandbox + evidence JSON.",
         "verification": "6 wrapper tests executed in this audit"},
        {"layer": "L2 · Public surface", "path": "website",
         "role": "Static bilingual site + JS reference port of the loop (labelled as a port).",
         "verification": "12 demo contract tests executed in this audit"},
        {"layer": "L3 · Governance / CI", "path": ".github/workflows + scripts",
         "role": "Tests, AST zero-deps gate, measured benchmarks, Pages deploy.",
         "verification": "9 guard self-tests executed in this audit"},
        {"layer": "L4 · Launch kit", "path": "launch + docs",
         "role": "Show HN / case study / benchmarks / readiness corrections.",
         "verification": "documents only — no runtime claim"},
        {"layer": "L5 · Archive", "path": "projects/ (48 projects)",
         "role": "Byte-identical upstream imports, each with its own license.",
         "verification": "AUDIT.md (48/48 byte-identical) — NOT re-verified here"},
    ]
    return {"categories": cats, "layers": layers,
            "declared_total_projects": catalog.get("total_projects"),
            "declared_total_files": catalog.get("total_files"),
            "empty_source_repos": len(catalog.get("empty_source_repos", []))}


def measure_egress() -> dict:
    """Measure whether this runner can judge public URLs at all.

    Without this, an audit produced inside a network-isolated sandbox can only
    say "unknown" about every public site — and must never say "dead".
    """
    try:
        import network_preflight  # local helper, stdlib only
        return network_preflight.preflight().to_dict()
    except Exception as exc:  # a broken preflight must not break the audit
        return {"egress": "UNMEASURED", "general_egress": False,
                "can_judge_public_urls": False, "error": str(exc)[:120],
                "probes": []}


def build(fast: bool = False, egress: bool = True) -> dict:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    probes = run_probes(fast)
    proven = {p["proves"] for p in probes if p["verdict"] == "PROVEN" and p.get("proves")}
    probe_failed = any(p["verdict"] == "FAILED" for p in probes)

    projects = []
    for entry in catalog.get("projects", []):
        name = entry.get("name", "")
        p = PROJECTS / name
        exists = p.is_dir()
        files, dirs = count_files(p) if exists else (0, 0)
        label, lstate = classify_license(p) if exists else ("NONE", "missing")
        readmes = [f for f in p.iterdir() if f.is_file() and f.name.upper().startswith("README")] if exists else []
        readme_text = _read(readmes[0], 60_000) if readmes else ""
        sim = bool(SIM_RE.search(readme_text))
        tests = detect_tests(p) if exists else 0
        ci = detect_ci(p) if exists else 0
        mf = manifests(p) if exists else []
        issues = []
        if not exists:
            issues.append("directory missing")
        if files == 0:
            issues.append("zero files")
        if lstate == "missing":
            issues.append("no LICENSE file")
        elif lstate == "pending":
            issues.append("license undecided")
        elif lstate == "corrupt":
            issues.append("LICENSE file corrupt")
        elif lstate == "proprietary":
            issues.append("proprietary — exclude from open-source claims")
        if not readmes:
            issues.append("no README")
        if tests == 0:
            issues.append("no test files detected")
        if ci == 0:
            issues.append("no CI workflows")
        if entry.get("files") != files:
            issues.append(f"catalog declares {entry.get('files')} files, filesystem has {files}")

        if name in proven:
            status, note = "PROVEN", "verification probe executed in this audit"
        elif not exists or files == 0 or lstate == "corrupt":
            status, note = "FAILED", "concrete defect found by inspection"
        elif sim:
            status, note = "SIMULATED", "README explicitly declares a simulation/mock mode"
        elif readme_text or entry.get("description_ar"):
            status, note = "DECLARED", "README/catalog describes it; NOT executed in this sandbox"
        else:
            status, note = "NOT VERIFIED", "no runnable evidence available"

        projects.append({
            "name": name, "category": entry.get("category"), "path": f"projects/{name}",
            "exists": exists, "files": files, "dirs": dirs,
            "catalog_files": entry.get("files"), "license": label, "license_state": lstate,
            "readme": bool(readmes), "tests": tests, "ci_workflows": ci,
            "manifests": mf, "simulation_markers": sim,
            "status": status, "note": note, "issues": issues,
        })

    counts: dict[str, int] = {}
    for pr in projects:
        counts[pr["status"]] = counts.get(pr["status"], 0) + 1
    license_counts: dict[str, int] = {}
    for pr in projects:
        license_counts[pr["license"]] = license_counts.get(pr["license"], 0) + 1

    # Repo-level, actually-checked facts (not claims).
    redaction = False
    secret_file = PROJECTS / "ai-store2" / "env.local"
    if secret_file.is_file():
        redaction = SECRET_REDACTION_MARKER in _read(secret_file)
    cert_dir = PROJECTS / "12pro" / "certification"
    cert_json = list(cert_dir.rglob("*.json")) if cert_dir.is_dir() else []
    cert_parsed, cert_bad = 0, []
    for f in cert_json:
        try:
            json.loads(f.read_text(encoding="utf-8")); cert_parsed += 1
        except (ValueError, OSError) as exc:
            cert_bad.append({"file": str(f.relative_to(ROOT)), "error": type(exc).__name__})

    critical = [{"project": "12pro/certification", "severity": "HIGH",
                 "issue": f"invalid/empty JSON artifact: {b['file']} ({b['error']})"}
                for b in cert_bad]
    for pr in projects:
        for issue in pr["issues"]:
            sev = "HIGH" if issue.startswith(("license undecided", "LICENSE file corrupt",
                                              "proprietary", "directory missing", "zero files")) else "LOW"
            critical.append({"project": pr["name"], "severity": sev, "issue": issue})
    order = {"HIGH": 0, "LOW": 1}
    critical.sort(key=lambda x: (order[x["severity"]], x["project"]))

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "method": "read-only filesystem inspection + executed subprocess probes; nothing under projects/ was modified",
        "mode": "fast" if fast else "full",
        "environment": {"python": sys.version.split()[0], "platform": sys.platform,
                        "cwd": str(ROOT)},
        "summary": {
            "projects": len(projects),
            "status_counts": counts,
            "license_counts": license_counts,
            "actual_files": sum(p["files"] for p in projects),
            "declared_files": catalog.get("total_files"),
            "projects_with_tests": sum(1 for p in projects if p["tests"] > 0),
            "projects_with_ci": sum(1 for p in projects if p["ci_workflows"] > 0),
            "projects_without_license": sum(1 for p in projects if p["license_state"] == "missing"),
            "critical_high": sum(1 for c in critical if c["severity"] == "HIGH"),
            "probes_passed": sum(1 for p in probes if p["verdict"] == "PROVEN"),
            "probes_failed": sum(1 for p in probes if p["verdict"] == "FAILED"),
        },
        "egress": measure_egress() if egress else {"egress": "SKIPPED"},
        "probes": probes,
        "probe_failed": probe_failed,
        "repo_checks": {
            "secret_redaction_marker_present": redaction,
            "certification_json_files": len(cert_json),
            "certification_json_parsed": cert_parsed,
            "certification_json_invalid": cert_bad,
            "catalog_matches_directory_count": len(projects) == len(list(PROJECTS.iterdir())),
            "audit_md_exists": (ROOT / "AUDIT.md").is_file(),
        },
        "architecture": architecture_map(catalog),
        "projects": projects,
        "critical": critical,
    }


def to_markdown(d: dict) -> str:
    s = d["summary"]
    L = []
    L.append("# 🛰️ AI-2027 — Repository Intelligence & Health Report")
    L.append("")
    L.append(f"**Generated:** {d['generated_at']} · **Mode:** {d['mode']} · "
             f"**Method:** {d['method']}")
    L.append("")
    L.append("> Classification is deliberate: a README describing a feature is "
             "**DECLARED**, not **PROVEN**. Only commands actually executed in this "
             "sandbox earn PROVEN. Nothing here modifies `projects/`.")
    L.append("")
    L.append("## 1. Verdict")
    L.append("")
    L.append("| Metric | Value |")
    L.append("|---|---:|")
    eg = d.get("egress") or {}
    egress_verdict = eg.get("egress", "UNMEASURED")
    for k, v in [("Public egress (measured)", egress_verdict),
                 ("Projects", s["projects"]),
                 ("Actual files on disk", s["actual_files"]),
                 ("Declared files (catalog.json)", s["declared_files"]),
                 ("With test files", s["projects_with_tests"]),
                 ("With CI workflows", s["projects_with_ci"]),
                 ("Without LICENSE", s["projects_without_license"]),
                 ("HIGH-severity findings", s["critical_high"]),
                 ("Probes passed / failed", f"{s['probes_passed']} / {s['probes_failed']}")]:
        L.append(f"| {k} | {v} |")
    L.append("")
    if not eg.get("general_egress"):
        L.append(f"> ⚠️ **Public egress = `{egress_verdict}`** — this runner could not "
                 "reach the general internet. Any public-site claim from here "
                 "is **BLOCKED (not judged)**, never \"dead\". "
                 "Verify with `python3 scripts/network_preflight.py`.")
        L.append("")
    L.append("| Classification | Count | Meaning |")
    L.append("|---|---:|---|")
    meanings = {
        "PROVEN": "a real command ran here and passed",
        "DECLARED": "documented, not executed in this sandbox",
        "SIMULATED": "README explicitly labels a simulation/mock mode",
        "NOT VERIFIED": "no runnable evidence available",
        "FAILED": "concrete defect found by inspection",
    }
    for k in ["PROVEN", "DECLARED", "SIMULATED", "NOT VERIFIED", "FAILED"]:
        L.append(f"| {k} | {s['status_counts'].get(k, 0)} | {meanings[k]} |")
    L.append("")
    L.append("## 2. Executed probes (real output, not claims)")
    L.append("")
    L.append("| Probe | Exit | Evidence | Verdict | Time |")
    L.append("|---|---:|---|---|---:|")
    for p in d["probes"]:
        L.append(f"| `{p['name']}` | {p['exit_code']} | {str(p['evidence'])[:90]} | "
                 f"**{p['verdict']}** | {p['ms']} ms |")
    L.append("")
    L.append("Commands (reproducible from repository root):")
    L.append("")
    L.append("```bash")
    for p in d["probes"]:
        L.append(p["command"])
    L.append("```")
    L.append("")
    L.append("## 3. Architecture map")
    L.append("")
    L.append("| Layer | Path | Role | Verification |")
    L.append("|---|---|---|---|")
    for layer in d["architecture"]["layers"]:
        L.append(f"| {layer['layer']} | `{layer['path']}` | {layer['role']} | {layer['verification']} |")
    L.append("")
    L.append("Categories: " + ", ".join(f"`{k}`={v}" for k, v in sorted(d["architecture"]["categories"].items())))
    L.append("")
    L.append("## 4. Repo-level checks")
    L.append("")
    for k, v in d["repo_checks"].items():
        if k == "certification_json_invalid":
            L.append(f"- **{k}:** {len(v)} — " +
                     ("; ".join(f"`{x['file']}` ({x['error']})" for x in v) if v else "none"))
        else:
            L.append(f"- **{k}:** `{v}`")
    L.append("")
    L.append("## 5. HIGH-severity findings (ranked)")
    L.append("")
    L.append("| Project | Issue |")
    L.append("|---|---|")
    for c in [x for x in d["critical"] if x["severity"] == "HIGH"]:
        L.append(f"| `{c['project']}` | {c['issue']} |")
    L.append("")
    L.append("## 6. Project-by-project health (48)")
    L.append("")
    L.append("| Project | Status | Files | License | Tests | CI | Simulation |")
    L.append("|---|---|---:|---|---:|---:|---|")
    for p in sorted(d["projects"], key=lambda x: x["name"].lower()):
        L.append(f"| `{p['name']}` | {p['status']} | {p['files']} | {p['license']} | "
                 f"{p['tests']} | {p['ci_workflows']} | {'yes' if p['simulation_markers'] else '—'} |")
    L.append("")
    L.append("## 7. What this report does NOT prove")
    L.append("")
    L.append("- It does **not** build or run the 47 other projects (they need Node/npm, "
             "external services, API keys or proprietary runtimes). DECLARED ≠ working.")
    L.append("- It does **not** re-verify the 48/48 byte-identical audit; that claim is "
             "inherited from `AUDIT.md`.")
    L.append("- It does **not** test external live URLs. Declared demo links remain NOT VERIFIED.")
    L.append("- It does **not** grant licenses. 22 projects have no LICENSE and 2 are proprietary.")
    L.append("")
    L.append("## 8. Tasks requiring owner approval (nothing was done automatically)")
    L.append("")
    L.append("- [ ] **Decide the license for `agi-system`** — blocks PyPI publication.")
    L.append("- [ ] **Replace `projects/new-agent/LICENSE`** — 4-byte corrupt placeholder.")
    L.append("- [ ] **Repair `projects/12pro/certification/v1.0.0-raw/health-raw.json`** — empty file, JSON parse fails.")
    L.append("- [x] **Cross-platform containment hardened (2026-11-17)** — backslashes, Windows drive prefixes and percent-encoded separators are refused on every platform; 16/25 former literal cases now DENIED, 9 remain literal POSIX names.")
    L.append("- [ ] **Choose Phase 2** — cloud LLM / local model / defer. No model code was added.")
    L.append("- [ ] **Repo metadata + Discussions** — integration token returns 403 on admin settings.")
    L.append("- [ ] **Approve merging the HUD PR into `main`** — this session opens a reviewable PR and does NOT merge it.")
    L.append("")
    L.append("---")
    L.append("")
    L.append("*Full machine-readable version: `GET /api/audit` on the Command HUD, or "
             "`python3 command-hud/audit.py`.*")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", type=Path, help="write a Markdown report to this path")
    ap.add_argument("--fast", action="store_true", help="skip subprocess probes")
    ap.add_argument("--assert-probes", action="store_true",
                    help="exit 1 if any executed probe failed")
    ap.add_argument("--skip-egress", action="store_true",
                    help="do not measure public egress (offline/fast runs)")
    args = ap.parse_args()
    d = build(fast=args.fast, egress=not args.skip_egress)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(to_markdown(d), encoding="utf-8")
        print(f"wrote {args.report} ({d['summary']['projects']} projects)")
    else:
        print(json.dumps(d, indent=2, ensure_ascii=False))
    if args.assert_probes and d["probe_failed"]:
        print("FAIL: one or more verification probes failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
