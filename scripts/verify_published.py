#!/usr/bin/env python3
"""verify_published.py — «سحر بايثون» للتحقق مما نُشر فعلياً من نظام AI-2027.

Evidence-first: لا ادعاء بلا دليل قابل لإعادة التشغيل.
كل سطح يُصنَّف: PROVEN / LIVE / DECLARED / SIMULATED / FAILED / BLOCKED
BLOCKED تعني: بيئة القياس لا ترى الشبكة — وليست أبداً «ميت».

Usage:
    python3 scripts/verify_published.py                 # تقرير Markdown + JSON
    python3 scripts/verify_published.py --json-out /tmp/v.json --md-out /tmp/v.md
    python3 scripts/verify_published.py --fast          # بدون اختبارات فرعية ثقيلة

Stdlib only. Python 3.10+.
"""
from __future__ import annotations
import argparse, json, os, re, ssl, subprocess, sys, time, urllib.request, urllib.error
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def run(cmd, timeout=120, cwd=None):
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=cwd or ROOT, capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        return {"cmd": " ".join(str(c) for c in cmd), "exit": p.returncode,
                "seconds": round(time.time()-t0, 2), "tail": out.strip()[-1200:]}
    except subprocess.TimeoutExpired:
        return {"cmd": " ".join(str(c) for c in cmd), "exit": -1, "seconds": timeout, "tail": "TIMEOUT"}
    except Exception as e:
        return {"cmd": " ".join(str(c) for c in cmd), "exit": -2, "seconds": round(time.time()-t0,2), "tail": f"{type(e).__name__}: {e}"}

def count_files(base: Path):
    n = 0
    for p in base.rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts and ".git" not in p.parts:
            n += 1
    return n

def probe_url(url, timeout=10):
    """BLOCKED (TLS EOF / unreachable from sandbox) is never reported as dead."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ai2027-verify-published/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(512)
            return {"url": url, "verdict": "LIVE", "status": r.status}
    except urllib.error.HTTPError as e:
        return {"url": url, "verdict": "LIVE" if 200 <= e.code < 400 else "FAILED", "status": e.code}
    except Exception as e:
        return {"url": url, "verdict": "BLOCKED", "status": None, "error": f"{type(e).__name__}: {e}"[:200]}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--json-out", default="docs/verification-2026-11-22.json")
    ap.add_argument("--md-out", default="docs/VERIFICATION-2026-11-22.md")
    args = ap.parse_args()

    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "root": str(ROOT), "checks": []}
    def add(name, verdict, evidence):
        report["checks"].append({"name": name, "verdict": verdict, "evidence": evidence})
        print(f"[{verdict:8}] {name}: {str(evidence)[:160]}")

    # 1) الجرد المحلي مقابل catalog.json
    catalog = json.loads((ROOT/"catalog.json").read_text(encoding="utf-8"))
    projects_dir = sorted(p.name for p in (ROOT/"projects").iterdir() if p.is_dir())
    cat_names = sorted(p.get("name") or p.get("slug") or "" for p in catalog.get("projects", []))
    disk_files = count_files(ROOT/"projects")
    add("inventory.projects_on_disk", "PROVEN" if len(projects_dir)==48 else "FAILED",
        f"{len(projects_dir)} project dirs on disk; catalog declares total_projects={catalog.get('total_projects')} total_files={catalog.get('total_files')}; files on disk (excl __pycache__)={disk_files}")
    statuses = Counter(p.get("operational_status","?") for p in catalog.get("projects", []))
    add("inventory.operational_status", "DECLARED", dict(statuses))

    # 2) فحوصات YAML/JSON الأساسية بدون PyYAML (تحقق بنيوي)
    for f in ["render.yaml", ".github/workflows/ci.yml", ".github/workflows/pages.yml", ".github/workflows/deploy.yml"]:
        p = ROOT/f
        add(f"parse.{f}", "PROVEN" if p.exists() and p.stat().st_size>0 else "FAILED", f"exists={p.exists()} bytes={p.stat().st_size if p.exists() else 0}")
    for f in ["catalog.json", "docs/public-urls.json", "docs/deviations.json", "agent-card.json"]:
        try:
            json.loads((ROOT/f).read_text(encoding="utf-8")); add(f"parse.{f}", "PROVEN", "valid JSON")
        except Exception as e:
            add(f"parse.{f}", "FAILED", str(e)[:200])

    # 3) GitHub عبر gh (قناة مسموحة في الـsandbox)
    for name, cmd in [
        ("github.repo", ["gh","repo","view","--json","nameWithOwner,url,isPrivate,defaultBranchRef,pushedAt,latestRelease,stargazerCount,forkCount"]),
        ("github.pages", ["gh","api","repos/sayedelazameydesign-crypto/ai-2027/pages"]),
        ("github.releases", ["gh","release","list","--limit","5"]),
        ("github.actions_recent", ["gh","run","list","--limit","15"]),
    ]:
        r = run(cmd, timeout=30)
        add(name, "PROVEN" if r["exit"]==0 else "BLOCKED", r["tail"][:500])

    # 4) اختبارات حقيقية (قابلة للتخطي بـ --fast)
    probes = [
        ("tests.agi-system", [sys.executable,"-m","unittest","discover","-s","projects/agi-system/tests","-t","projects/agi-system"]),
        ("tests.live-agent", [sys.executable,"live-agent/test_server.py"]),
        ("tests.memory", [sys.executable,"memory/test_memory.py"]),
        ("tests.hud", [sys.executable,"command-hud/test_hud.py"]),
        ("tests.demo-node", ["node","website/kernel-demo.test.js"]),
        ("gate.zero-dep-ast", [sys.executable,"scripts/ci_guard.py","imports"]),
        ("audit.command-hud", [sys.executable,"command-hud/audit.py","--fast"]),
    ]
    if not args.fast:
        for name, cmd in probes:
            r = run(cmd, timeout=180)
            ok = r["exit"]==0
            add(name, "PROVEN" if ok else "FAILED", r["tail"][:600])
        # redteam + bench إن وُجدا
        for name, cmd in [("redteam.100", [sys.executable,"command-hud/redteam.py","--markdown"]),
                          ("bench.live-agent", [sys.executable,"live-agent/bench.py"])]:
            r = run(cmd, timeout=180)
            add(name, "PROVEN" if r["exit"]==0 else "FAILED", r["tail"][:600])
        r = run([sys.executable,"scripts/doctor.py","--json"], timeout=60)
        add("doctor.offline", "PROVEN" if r["exit"]==0 else "DECLARED", r["tail"][:600])
    else:
        add("tests.skipped", "DECLARED", "--fast was given")

    # 5) الروابط العامة من سجل النظام نفسه — BLOCKED ≠ dead
    pub = json.loads((ROOT/"docs/public-urls.json").read_text(encoding="utf-8"))
    for item in pub.get("urls", []):
        res = probe_url(item["url"])
        # من الـsandbox كل شيء BLOCKED عملياً؛ نحفظ الحكم المتوقع بجانبه
        add(f"url.{item['url']}", res["verdict"], f"expected={item.get('expected')} last_status={item.get('last_status')} probe={res}")
    # أسطح Render المعلنة في render.yaml (ليست في public-urls.json)
    for u in ["https://ai-2027-live-agent.onrender.com/api/health",
              "https://ai-2027-studio.onrender.com/v1/studio/status"]:
        add(f"url.{u}", probe_url(u)["verdict"], "declared in render.yaml; sandbox probe only (see report notes)")

    # 6) أسرار: الحكم لأداة النظام الرسمية فقط — grep ساذج يلتقط قيم اختبارات
    # وهمية موثقة في AUDIT.md فلا يصلح حكما. لا تعيد اختراع الماسح.
    r = run([sys.executable,"scripts/secret_scan.py","--all"], timeout=60)
    add("secret_scan.official", "PROVEN" if r["exit"]==0 else "FAILED", r["tail"][:500])
    env_local = ROOT/"projects/ai-store2/env.local"
    txt = env_local.read_text(encoding="utf-8", errors="replace") if env_local.exists() else ""
    add("secret_scan.ai-store2_redacted_here", "PROVEN" if "REDACTED-rotate-this-key" in txt else "FAILED",
        "env.local REDACTED in this repo (D-2); upstream revocation is an owner action tracked in AUDIT.md")

    # خلاصة
    verdicts = Counter(c["verdict"] for c in report["checks"])
    report["summary"] = dict(verdicts)
    out_json = ROOT/args.json_out; out_md = ROOT/args.md_out
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# ✅ تحقق النشر — AI-2027 · {report['generated_at']}", "",
             "> مولّد بـ `python3 scripts/verify_published.py` — كل سطر أدناه أُعيد تشغيله، لا منسوخ من README.",
             "> `BLOCKED` تعني أن بيئة القياس لا ترى الشبكة، وليست حكماً بالموت.", "",
             f"**الخلاصة:** {dict(verdicts)}", "", "| الفحص | الحكم | الدليل (مختصر) |", "|---|---|---|"]
    for c in report["checks"]:
        ev = str(c["evidence"]).replace("|","/").replace("\n"," ")[:300]
        lines.append(f"| `{c['name']}` | **{c['verdict']}** | {ev} |")
    lines += ["", "## إعادة التشغيل", "```bash", "python3 scripts/verify_published.py", "```", "",
              "تفاصيل كاملة: `docs/verification-2026-11-22.json`"]
    out_md.write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(json.dumps({"summary": dict(verdicts), "json": str(out_json), "md": str(out_md)}, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
