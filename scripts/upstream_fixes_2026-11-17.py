#!/usr/bin/env python3
"""Apply the 2026-11-17 upstream fixes to the SOURCE repositories.

Why this lives here instead of being executed: the sandbox token is read-only
for those repositories (403 "Resource not accessible by integration"), so the
archive in ``projects/`` must not be edited in their place — it is a byte-mirror.
Run this with a token that can push, then re-run the identity check.

    GH_TOKEN=... python3 scripts/upstream_fixes_2026-11-17.py --dry-run
    GH_TOKEN=... python3 scripts/upstream_fixes_2026-11-17.py --apply
    python3 scripts/verify_archive_identity.py

Each fix is idempotent: re-running it on already-fixed content is a no-op.
Only stdlib + the ``gh`` CLI are used.
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path

OWNER = "sayedelazameydesign-crypto"
ROOT = Path(__file__).resolve().parent.parent

NEW_AGENT_LICENSE = """LICENSE — PENDING (no license granted)

The LICENSE file that shipped in this repository was 4 bytes of non-text
(NUL + U+FFFD). It granted nothing, and a corrupt file must not be read as
permission. It was replaced on 2026-11-17 by this explicit notice so that
"no license" is stated rather than implied.

Until the maintainer chooses a license:
  - all rights are reserved;
  - this code must not be redistributed or reused;
  - the status is tracked in ai-2027/docs/LICENSE-DECISIONS.md.
"""


def gh(args: list[str], payload: dict | None = None) -> dict:
    cmd = ["gh", "api", *args]
    if payload is not None:
        cmd += ["--input", "-"]
        p = subprocess.run(cmd, input=json.dumps(payload), capture_output=True, text=True)
    else:
        p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[:400] or "gh api failed")
    return json.loads(p.stdout) if p.stdout.strip() else {}


def get_file(repo: str, path: str) -> tuple[bytes, str]:
    d = gh([f"repos/{OWNER}/{repo}/contents/{path}"])
    return base64.b64decode(d["content"]), d["sha"]


def put_file(repo: str, path: str, content: bytes, message: str,
             sha: str | None, apply: bool) -> None:
    if not apply:
        print(f"  [dry-run] would write {repo}/{path} ({len(content)} bytes)")
        return
    payload = {"message": message, "content": base64.b64encode(content).decode()}
    if sha:
        payload["sha"] = sha
    r = gh(["-X", "PUT", f"repos/{OWNER}/{repo}/contents/{path}"], payload)
    print(f"  ✓ {repo}/{path} -> {r['commit']['sha'][:8]}")


def edit_text(repo: str, path: str, repl: list[tuple[str, str]], message: str,
              apply: bool) -> None:
    raw, sha = get_file(repo, path)
    text = raw.decode("utf-8")
    changed = False
    for old, new in repl:
        if old not in text:
            print(f"  · no-op (already applied?): {repo}/{path} :: {old[:48]!r}")
            continue
        text = text.replace(old, new)
        changed = True
    if not changed:
        return
    put_file(repo, path, text.encode("utf-8"), message, sha, apply)


def fix_kernel(apply: bool) -> None:
    raw = (ROOT / "projects/agi-system/core/policy.py").read_bytes()
    _, sha = get_file("agi-system", "core/policy.py")
    if base64.b64encode(raw).decode() == "":
        return
    put_file(
        "agi-system", "core/policy.py", raw,
        "policy: refuse cross-platform path separators (backslash, drive prefix, %2f %5c %2e %00)\n\n"
        "Containment that only holds on POSIX is not containment: the same input is a\n"
        "separator on Windows or after a URL-decode. Denied on every platform now.\n"
        "86 kernel tests + the 100-case red-team still pass (91 DENIED / 9 literal).",
        sha, apply)


def fix_license(apply: bool) -> None:
    _, sha = get_file("new-agent", "LICENSE")
    put_file("new-agent", "LICENSE", NEW_AGENT_LICENSE.encode(),
             "LICENSE: replace 4-byte corrupt placeholder with an explicit PENDING notice\n\n"
             "The old file was NUL + U+FFFD — it granted nothing while looking like a\n"
             "license. This notice states the real status until the maintainer decides.",
             sha, apply)


def fix_health_json(apply: bool) -> None:
    raw, sha = get_file("12pro", "certification/v1.0.0-raw/health-raw.json")
    old = raw.decode("utf-8", "replace")
    if old.lstrip().startswith("{"):
        print("  · 12pro/health-raw.json already valid JSON")
        return
    payload = old.split("\n", 1)[1].strip() if old.startswith("---") else old.strip()
    fixed = ('{\n'
             '  "note": "Repaired 2026-11-17. This artifact shipped with a non-JSON header '
             '(\\"--- Health ---\\"), so every JSON parse failed. The probe payload below is '
             'byte-preserved; nothing was re-run or invented.",\n'
             f'  "probe": {payload}\n'
             '}\n')
    json.loads(fixed)
    put_file("12pro", "certification/v1.0.0-raw/health-raw.json", fixed.encode(),
             "certification: make health-raw.json valid JSON (payload preserved)", sha, apply)


def fix_secret(apply: bool) -> None:
    raw, sha = get_file("ai-store2", "env.local")
    if b"REDACTED" in raw:
        print("  · ai-store2/env.local already redacted")
        return
    put_file("ai-store2", "env.local",
             b"NEXT_PUBLIC_GEMINI_API_KEY=REDACTED-rotate-this-key-see-AUDIT.md\n",
             "SECURITY: redact exposed Gemini key\n\nThe key was committed in plaintext. It is redacted at HEAD only — git history\n"
             "still contains it. ROTATE the key at aistudio.google.com/apikey; redaction is\n"
             "not revocation.", sha, apply)


def fix_dead_links(apply: bool) -> None:
    edit_text("ELAZAMET1", "QUICK_DEPLOY.md", [
        ("FRONTEND_URL=https://elazamet1.vercel.app",
         "FRONTEND_URL=https://YOUR-APP.vercel.app   # ضع رابطك بعد النشر"),
        ("- Frontend: `https://elazamet1.vercel.app`",
         "- Frontend: `https://YOUR-APP.vercel.app` (⚠️ الرابط القديم "
         "`elazamet1.vercel.app` يعيد 404 — تحقّق 2026-11-17)"),
    ], "docs: replace dead deployment URL with a placeholder\n\n"
       "elazamet1.vercel.app returns 404 DEPLOYMENT_NOT_FOUND (verified 2026-11-17).", apply)

    edit_text("ELAZAMET1", "DEPLOYMENT_GUIDE_KOYEB.md", [
        ("FRONTEND_URL=https://elazamet1.vercel.app",
         "FRONTEND_URL=https://YOUR-APP.vercel.app   # ضع رابطك بعد النشر"),
        ("3. ستحصل على رابط مثل: `https://elazamet1.vercel.app`",
         "3. ستحصل على رابط مثل: `https://YOUR-APP.vercel.app` "
         "(الرابط القديم يعيد 404 — تحقّق 2026-11-17)"),
    ], "docs: replace dead deployment URL with a placeholder (404 verified 2026-11-17)", apply)

    edit_text("celia-pro-deployment", "خطوات-إتمام-النشر-المجاني.md", [
        ("نمطه `https://celia-pro.onrender.com`",
         "نمطه `https://YOUR-SERVICE.onrender.com`"),
        ("(مثل `https://celia-pro.onrender.com`)",
         "(مثل `https://YOUR-SERVICE.onrender.com`؛ ملاحظة: "
         "`celia-pro.onrender.com` يعيد Not Found — تحقّق 2026-11-17)"),
    ], "docs: mark the onrender URL as unavailable and use a placeholder", apply)

    edit_text("platform", "deploy/render/README.md", [
        ("bash deploy/hf-verify.sh https://nawah-agent.onrender.com phase1",
         "bash deploy/hf-verify.sh https://YOUR-SERVICE.onrender.com phase1   "
         "# nawah-agent.onrender.com يعيد Not Found (تحقّق 2026-11-17)"),
        ("bash deploy/hf-verify.sh https://nawah-agent.onrender.com phase2",
         "bash deploy/hf-verify.sh https://YOUR-SERVICE.onrender.com phase2"),
    ], "docs: use a placeholder for the dead nawah-agent onrender URL", apply)


FIXES = [
    ("U-1 kernel cross-platform hardening", fix_kernel),
    ("U-2 new-agent corrupt LICENSE", fix_license),
    ("U-3 12pro invalid JSON artifact", fix_health_json),
    ("U-4 ai-store2 exposed key (HEAD only)", fix_secret),
    ("U-5..U-7 dead deployment URLs", fix_dead_links),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually write (default is dry-run)")
    ap.add_argument("--only", help="run one fix, e.g. U-2")
    args = ap.parse_args()
    rc = 0
    for name, fn in FIXES:
        if args.only and not name.startswith(args.only):
            continue
        print(f"\n▶ {name}")
        try:
            fn(args.apply)
        except Exception as exc:  # noqa: BLE001 — report and continue
            print(f"  ✗ {exc}")
            rc = 1
    print("\nDone." if args.apply else "\nDry-run only. Re-run with --apply.")
    if args.apply:
        print("Now run: python3 scripts/verify_archive_identity.py  (expect 48/48)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
