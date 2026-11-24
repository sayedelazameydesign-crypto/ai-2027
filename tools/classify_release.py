#!/usr/bin/env python3
"""classify_release.py — مصنّف مراحل الإصدار (2024 / 2027).

يقرأ projects/ ويكتب:
  - release_catalog.json
  - RELEASES-2024.md / RELEASES-2027.md
  - gaps/<proj>.md  (اختياري عبر --gaps)

المراحل: poc → prototype → mvp → alpha → beta → rc → v1.0
القاعدة الذهبية: لا مرحلة بدون دليل. التصريح البشري يتقدم، لكنه يُدقَّق ضد الاستدلال الخام.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

STAGES = ["poc", "prototype", "mvp", "alpha", "beta", "rc", "v1.0"]
LABELS = {
    "poc": "Proof of Concept",
    "prototype": "Prototype",
    "mvp": "MVP",
    "alpha": "Alpha",
    "beta": "Beta",
    "rc": "Release Candidate",
    "v1.0": "v1.0 / Stable",
    "unknown": "غير مصنّف",
}
VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.\-]+))?")
TEST_FILE_RE = re.compile(r"(^test_.*\.py$|.*_test\.py$)")
FREEZE_WORDS = re.compile(r"(feature[- ]complete|feature freeze|no new features)", re.I)
INTERNAL_WORDS = re.compile(r"(internal (testing|testers?)|dogfood(ing)?|تجارب داخلية)", re.I)
EXTERNAL_WORDS = re.compile(r"(beta testers?|early users?|used by|real users?)", re.I)
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
             ".mypy_cache", "dist", "build", ".next", "target"}

MISSING_FOR = {
    ("mvp", "2027"): ["openapi.json أو mcp.json", "evals/", "uv.lock",
                      ".devcontainer/ أو Dockerfile", "otel/Rصد", "guardrails/"],
    ("alpha", "2027"): ["observability (otel/telemetry)", "AGENTS.md أو CLAUDE.md"],
    ("beta", "2027"):  ["evals/", "openapi.json أو mcp.json"],
    ("prototype", "2027"): ["uv.lock", ".devcontainer/ أو Dockerfile"],
    ("mvp", "2024"): ["users.json (دليل استخدام)", "changelog", "ci"],
    ("alpha", "2024"): ["usage: internal مصرّح به", "tests أو ci"],
}


@dataclass
class Evidence:
    name: str
    path: str
    files: int = 0
    py_files: int = 0
    deps: int = 0
    tests: int = 0
    ci: bool = False
    docs: bool = False
    changelog: bool = False
    entrypoint: bool = False
    deployed: bool = False
    usage: str = "none"
    version: str | None = None
    semver: tuple[int, int, int] | None = None
    prerelease: str | None = None
    feature_complete: bool = False
    freeze: bool = False
    ai_native: bool = False
    evals: int = 0
    model_card: bool = False
    agent_ready: bool = False
    observability: bool = False
    cost_tracking: bool = False
    reproducible: bool = False
    sbom: bool = False
    guardrails: bool = False
    signed: bool = False
    declared: str | None = None
    kind: str | None = None
    stage: str = "unknown"
    reason: str = ""
    raw_stage: str = "unknown"
    raw_reason: str = ""
    signals: list[str] = field(default_factory=list)


def read_text(p: Path, limit: int = 200_000) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def walk_files(root: Path) -> list[Path]:
    out = []
    for p in root.rglob("*"):
        if p.is_file() and not (set(p.parts) & SKIP_DIRS):
            out.append(p)
    return out


def parse_version(raw: str | None):
    if not raw:
        return None, None
    m = VERSION_RE.search(raw)
    if not m:
        return None, None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))), m.group(4)


def git_tag_version(proj: Path, root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "tag", "-l", f"{proj.name}/v*",
             "--sort=-v:refname"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        tags = out.stdout.strip().splitlines()
        return tags[0].rsplit("/v", 1)[-1] if tags else None
    except (OSError, subprocess.SubprocessError):
        return None


def find_version(proj: Path, files: list[Path], root: Path):
    tag = git_tag_version(proj, root)
    if tag:
        return tag
    rj = proj / "release.json"
    if rj.is_file():
        try:
            v = json.loads(read_text(rj) or "{}").get("version")
            if isinstance(v, str):
                return v
        except json.JSONDecodeError:
            pass
    for name in ("VERSION", "version.txt"):
        f = proj / name
        if f.is_file():
            return read_text(f).strip()
    py = proj / "pyproject.toml"
    if py.is_file():
        m = re.search(r'^\s*version\s*=\s*"([^"]+)"', read_text(py), re.M)
        if m:
            return m.group(1)
    for f in files:
        if f.name == "__init__.py":
            m = re.search(r'__version__\s*=\s*["\']([^"\']+)', read_text(f))
            if m:
                return m.group(1)
    pkg = proj / "package.json"
    if pkg.is_file():
        try:
            return json.loads(read_text(pkg)).get("version")
        except json.JSONDecodeError:
            pass
    return None


def count_deps(proj: Path) -> int:
    py = proj / "pyproject.toml"
    if py.is_file():
        m = re.search(r"dependencies\s*=\s*\[(.*?)\]", read_text(py), re.S)
        if m:
            return len(re.findall(r'"', m.group(1))) // 2
    req = proj / "requirements.txt"
    if req.is_file():
        return len([l for l in read_text(req).splitlines()
                    if l.strip() and not l.strip().startswith("#")])
    return 0


def read_catalog_entry(proj: Path, root: Path) -> dict:
    cat = root.parent / "catalog.json"
    if not cat.is_file():
        return {}
    try:
        data = json.loads(read_text(cat))
        entries = data.get("projects", data) if isinstance(data, dict) else {}
        return entries.get(proj.name, {}) or {}
    except (json.JSONDecodeError, AttributeError):
        return {}


def scan(proj: Path, root: Path) -> Evidence:
    ev = Evidence(name=proj.name, path=str(proj))
    files = walk_files(proj)
    ev.files = len(files)
    ev.py_files = sum(1 for f in files if f.suffix == ".py")
    names = {f.name.lower() for f in files}
    dirs = {p.name.lower() for p in proj.iterdir() if p.is_dir()}

    if ev.py_files == 0 and not names & {"package.json", "vercel.json"}:
        ev.stage, ev.reason = "unknown", "لا يوجد كود قابل للتصنيف"
        return ev

    ev.deps = count_deps(proj)
    ev.tests = sum(1 for f in files if f.suffix == ".py" and TEST_FILE_RE.match(f.name))
    ev.ci = any([
        (proj / ".github" / "workflows").is_dir(),
        (proj / ".gitlab-ci.yml").is_file(),
        (proj / "Jenkinsfile").is_file(),
        (proj / ".circleci").is_dir(),
    ])
    ev.changelog = any(f.name.upper().startswith(("CHANGELOG", "HISTORY")) for f in files)
    readme = next((f for f in files if f.name.lower().startswith("readme")), None)
    rtxt = read_text(readme) if readme else ""
    ev.docs = (proj / "docs").is_dir() or (len(rtxt) > 400 and rtxt.count("\n##") >= 2)

    # entrypoint: ملف فعلي فقط
    ev.entrypoint = any(
        n in names for n in {"main.py", "cli.py", "__main__.py", "app.py", "server.py"}
    ) or "src" in dirs and any(f.parts[-2:-1] == ("src",) for f in files if f.name == "main.py")

    ev.deployed = any(
        n in names for n in {"vercel.json", "netlify.toml", "render.yaml",
                             "fly.toml", "Procfile", "app.yaml"}
    ) or bool(dirs & {".vercel", ".netlify"})

    ev.version = find_version(proj, files, root)
    ev.semver, ev.prerelease = parse_version(ev.version)

    combined = rtxt
    cl = next((f for f in files if f.name.upper().startswith(("CHANGELOG", "HISTORY"))), None)
    if cl:
        combined += "\n" + read_text(cl)
    ev.feature_complete = bool(FREEZE_WORDS.search(combined))
    ev.freeze = bool(re.search(r"(freeze|rc\d)", combined, re.I))

    # release.json
    rj = proj / "release.json"
    if rj.is_file():
        try:
            data = json.loads(read_text(rj) or "{}")
            if data.get("kind") == "kernel":
                ev.kind = "kernel"
            # _inferred: true → تجاهل الحقول البشرية
            if data.get("_inferred") is not True:
                declared = (data.get("stage") or "").lower() or None
                if declared in STAGES:
                    ev.declared = declared
                if data.get("usage") in {"none", "internal", "external"}:
                    ev.usage = data["usage"]
        except (json.JSONDecodeError, ValueError):
            pass

    # catalog.json
    cat = read_catalog_entry(proj, root)
    if (cat.get("status") or "").upper() == "SIMULATED":
        ev.usage = "none"

    # الاستخدام والانتشار
    combined = rtxt
    if ev.usage == "none":
        if (proj / "users.json").is_file() or (proj / "telemetry").is_dir():
            ev.usage = "external"
        elif INTERNAL_WORDS.search(combined):
            ev.usage = "internal"
        elif EXTERNAL_WORDS.search(combined) and ev.deployed:
            ev.usage = "external"

    # 2027
    texts = "\n".join(read_text(f, 80_000) for f in files[:60]
                      if f.suffix in {".md", ".toml", ".json", ".yaml", ".yml"})
    lock = bool(names & {"uv.lock", "poetry.lock", "pnpm-lock.yaml",
                         "package-lock.json", "Pipfile.lock"})
    container = "dockerfile" in names or ".devcontainer" in dirs

    ev.ai_native = bool(names & {"agents.md", "claude.md", ".cursorrules"})
    ev.evals = sum(1 for f in files if f.suffix == ".py"
                   and (f.name.startswith("eval_") or "evals" in f.parts))
    ev.model_card = "model_card.md" in names or "modelcard.md" in names
    ev.agent_ready = bool(names & {"openapi.json", "openapi.yaml", "mcp.json"})
    ev.observability = ("opentelemetry" in texts.lower()
                        or "telemetry" in dirs or "tracing.py" in names)
    ev.cost_tracking = bool(re.search(r"(token[_ ]?budget|cost[_ ]?ledger|usage_ledger)",
                                       texts, re.I))
    ev.reproducible = lock and container
    ev.sbom = bool(names & {"sbom.json", "cyclonedx.json", "cyclonedx.xml"})
    ev.guardrails = ("guardrails" in dirs or "filters.py" in names or "moderation" in dirs)
    ev.signed = ".sigstore" in dirs or "cosign.pub" in names

    return ev


def classify_2024(ev: Evidence) -> tuple[str, str]:
    major = ev.semver[0] if ev.semver else 0
    solid = ev.tests > 0 and ev.ci and ev.changelog and ev.docs
    if major >= 1 and solid:
        return "v1.0", "إصدار رئيسي ≥1.0 + اختبارات + CI + changelog + وثائق"
    if ev.prerelease and "rc" in ev.prerelease.lower():
        return "rc", f"نسخة ما قبل الإصدار: {ev.prerelease}"
    if ev.usage == "external" and solid and ev.feature_complete:
        return "beta", "مستخدمون خارجيون + نضج هندسي + اكتمال ميزات"
    if ev.usage == "external" and ev.entrypoint:
        return "mvp", "نقطة دخول + مستخدمون خارجيون"
    if ev.usage == "internal" and (ev.tests or ev.ci):
        return "alpha", "استخدام داخلي + اختبارات/CI"
    if ev.entrypoint and (ev.deployed or ev.py_files > 0):
        return "prototype", "نموذج يعمل من طرف إلى طرف بلا مستخدمين"
    return "poc", "إثبات جدوى أولي"


def classify_2027(ev: Evidence) -> tuple[str, str]:
    major = ev.semver[0] if ev.semver else 0
    if major >= 1 and ev.signed and ev.sbom and ev.model_card and ev.guardrails:
        return "v1.0", "توقيع + SBOM + بطاقة نموذج + حواجز"
    if ev.prerelease and "rc" in ev.prerelease.lower() and ev.feature_complete and ev.guardrails:
        return "rc", f"مرشح إصدار + حواجز — {ev.prerelease}"
    if ev.usage == "external" and ev.observability and ev.evals > 0 and ev.agent_ready:
        return "beta", "مستخدمون + رصد + تقييمات + جاهز للوكيل"
    if ev.usage == "external" and ev.observability and ev.ai_native:
        return "alpha", "مستخدمون + رصد + وثائق وكلاء"
    if ev.usage == "external" and ev.agent_ready and ev.reproducible:
        return "mvp", "استخدام مسجل + جاهز للوكيل + بيئة قابلة للتكرار"
    if ev.entrypoint and ev.reproducible:
        return "prototype", "نموذج يعمل + بيئة قابلة للتكرار"
    if ev.entrypoint and ev.deployed:
        return "prototype", "منشور لكن بلا دليل استخدام أو إعادة إنتاج"
    return "poc", "إثبات جدوى فقط"


def classify(ev: Evidence, standard: str) -> tuple[str, str]:
    """الأقوى دليلاً يفوز. التصريح اليدوي يتقدّم، لكن يُقارن بالاستدلال الخام."""
    if ev.declared in STAGES:
        return ev.declared, "تصريح بشري"
    return (classify_2027 if standard == "2027" else classify_2024)(ev)


def missing_for(ev: Evidence, target: str, standard: str) -> list[str]:
    return MISSING_FOR.get((target, standard), [])


def write_gap(ev: Evidence, target: str, standard: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    f = out_dir / f"{ev.name}.md"
    missing = missing_for(ev, target, standard)
    body = [
        f"# فجوة نضج: `{ev.name}` → `{target}` (معيار {standard})",
        "",
        f"**التصريح البشري:** `{ev.declared or '—'}`  ",
        f"**الاستدلال الآلي (خام):** `{ev.raw_stage}`  ",
        f"**القرار النهائي:** `{ev.stage}`  ",
        f"**السبب:** {ev.reason}",
        "",
        "## ما ينقص للوصول",
        "",
        *(f"- [ ] `{m}`" for m in missing),
        "",
        "## أدلة حالية",
        "",
        f"- اختبارات: {ev.tests}",
        f"- CI: {ev.ci}",
        f"- CHANGELOG: {ev.changelog}",
        f"- الوثائق: {ev.docs}",
        f"- نقطة دخول: {ev.entrypoint}",
        f"- منشور: {ev.deployed}",
        f"- الاستخدام: {ev.usage}",
        f"- قابلية إعادة الإنتاج: {ev.reproducible}",
        f"- جاهز للوكيل: {ev.agent_ready}",
        f"- رصد: {ev.observability}",
        f"- تقييمات: {ev.evals}",
        f"- حواجز: {ev.guardrails}",
        "",
        "_ملف مُولّد للمراجعة البشرية — لا يُفتح كـ Issue تلقائيًا._",
    ]
    f.write_text("\n".join(body), encoding="utf-8")
    return f


def render_md(classified: list[Evidence], drift: list[tuple],
              exempt: list[tuple], standard: str) -> str:
    lines = [f"# RELEASES — معيار {standard}", ""]
    lines += ["## ✅ مصنّف", "",
              "| المشروع | التصريح البشري | الاستدلال الخام | القرار النهائي | الدليل |",
              "|---|---|---|---|---|"]
    for e in sorted(classified, key=lambda x: STAGES.index(x.stage)):
        declared = e.declared or "—"
        raw_stage = getattr(e, 'raw_stage', '—')
        marker = "" if e.declared else " *(مستنتج)*"
        lines.append(f"| `{e.name}` | {declared} | {raw_stage} | {e.stage} | {e.reason}{marker} |")

    if drift:
        lines += ["", "## ⚠️ انحرافات (فجوة نضج)", "",
                  "| المشروع | بشري | مستنتج | الفجوة |",
                  "|---|---|---|---|"]
        for name, declared, inferred, gap in drift:
            lines.append(f"| `{name}` | {declared} | {inferred} | {gap} |")

    if exempt:
        lines += ["", "## ⚪️ مستثنى (غير قابل للتحقق)", "",
                  "| المشروع | التصريح | السبب |",
                  "|---|---|---|"]
        for name, declared, why in exempt:
            lines.append(f"| `{name}` | {declared} | {why} |")

    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="تصنيف مشاريع المستودع حسب مرحلة الإصدار")
    ap.add_argument("--root", default="projects", type=Path)
    ap.add_argument("--standard", choices=["2024", "2027"], default="2027")
    ap.add_argument("--out", default="release_catalog.json", type=Path)
    ap.add_argument("--md", default=None, type=Path)
    ap.add_argument("--gaps", default=None, type=Path)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--init", action="store_true")
    args = ap.parse_args()

    if not args.root.is_dir():
        print(f"✗ المجلد غير موجود: {args.root}", file=sys.stderr)
        return 2

    projects = [p for p in sorted(args.root.iterdir()) if p.is_dir()]
    rows: list[Evidence] = []
    classified: list[Evidence] = []
    drift: list[tuple] = []
    exempt: list[tuple] = []

    for proj in projects:
        ev = scan(proj, args.root)

        # 1) الاستدلال الخام أولًا — دائمًا، بمعزل عن التصريح
        raw_stage, raw_reason = (
            classify_2027(ev) if args.standard == "2027" else classify_2024(ev)
        )
        ev.raw_stage, ev.raw_reason = raw_stage, raw_reason

        # 2) القرار النهائي
        if ev.declared in STAGES:
            ev.stage, ev.reason = ev.declared, "تصريح بشري"
        else:
            ev.stage, ev.reason = raw_stage, raw_reason

        rows.append(ev)

        if ev.kind == "kernel":
            exempt.append((ev.name, ev.declared or "—", "kind: kernel — معايير المنتج لا تنطبق"))
            continue

        classified.append(ev)

        # 3) المقارنة الصحيحة: التصريح ضد الاستدلال الخام
        if ev.declared and ev.declared != raw_stage:
            gap = "، ".join(missing_for(ev, ev.declared, args.standard)) or "راجع الأدلة"
            drift.append((ev.name, ev.declared, raw_stage, gap))
            if args.gaps:
                write_gap(ev, ev.declared, args.standard, args.gaps)

    payload = {
        "standard": args.standard,
        "generated_by": "tools/classify_release.py",
        "stages": STAGES,
        "classified": [{k: v for k, v in e.__dict__.items() if k != "signals"}
                       for e in classified],
        "drift": [{"project": n, "declared": d, "inferred": i, "gap": g}
                  for n, d, i, g in drift],
        "exempt": [{"project": n, "declared": d, "reason": w} for n, d, w in exempt],
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"✓ كُتب {args.out} — {len(classified)} مصنّف، "
          f"{len(drift)} انحراف، {len(exempt)} مستثنى")

    if args.md:
        args.md.write_text(render_md(classified, drift, exempt, args.standard),
                           encoding="utf-8")
        print(f"✓ كُتب {args.md}")

    if args.init:
        for e in classified:
            f = Path(e.path) / "release.json"
            if not f.exists():
                f.write_text(json.dumps(
                    {"stage": e.stage, "usage": e.usage,
                     "version": e.version, "_inferred": True},
                    ensure_ascii=False, indent=2), encoding="utf-8")
        print("✓ أُنشئت ملفات release.json المبدئية (_inferred: true)")

    if args.check and drift:
        for name, declared, inferred, _ in drift:
            print(f"✗ انحراف: {name} — بشري: {declared}، مستنتج: {inferred}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
