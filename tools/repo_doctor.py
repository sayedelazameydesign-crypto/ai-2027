#!/usr/bin/env python3
"""Free, read-only repository diagnostics. Run with --help for scope and limits."""
from __future__ import annotations

import argparse
import ast
import collections
import json
import os
from pathlib import Path
import subprocess
import sys
import tokenize
import xml.etree.ElementTree as ET

try:
    import tomllib
except ImportError:
    tomllib = None

MAX_SIZE = 5 * 1024 * 1024
CHECKERS = {'.py': 'python', '.json': 'json', '.toml': 'toml', '.xml': 'xml'}
PRIVATE_NAMES = {'.env', '.netrc', '.npmrc', '.pypirc', '.git-credentials', 'credentials.json'}


def private(path: Path) -> bool:
    name = path.name.lower()
    return (name in PRIVATE_NAMES or name.startswith('.env.') and name != '.env.example'
            or name.endswith(('.pem', '.key', '.p12', '.pfx')))


def inventory(root: Path) -> list[Path]:
    try:
        output = subprocess.run(['git', '-C', str(root), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                                capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError('تعذر سرد الملفات عبر Git؛ شغّل الأداة داخل مستودع Git.') from exc
    return sorted({Path(os.fsdecode(x)) for x in output.split(b'\0') if x})


def finding(path: str, checker: str, message: str, line: int | None = None) -> dict:
    # Never include source lines or parser messages: they can contain credentials.
    return {'path': path, 'check': checker, 'message': message, 'line': line}


def scan(root: Path) -> dict:
    counts = collections.Counter()
    extensions = collections.Counter()
    findings = []
    files = inventory(root)
    for relative in files:
        p = root / relative
        label = relative.as_posix()
        extensions[relative.suffix.lower() or '(no extension)'] += 1
        if private(relative):
            counts['skipped_sensitive'] += 1
            continue
        if p.is_symlink():
            counts['skipped_symlink'] += 1
            continue
        if not p.is_file():
            counts['missing_or_nonregular'] += 1
            continue
        kind = CHECKERS.get(p.suffix.lower())
        if not kind or kind == 'toml' and tomllib is None:
            counts['inventory_only'] += 1
            continue
        try:
            if p.stat().st_size > MAX_SIZE:
                counts['skipped_large'] += 1
                continue
            if kind == 'python':
                with tokenize.open(p) as stream:
                    text = stream.read()
                ast.parse(text, filename=label)
            elif kind == 'json':
                with p.open(encoding='utf-8-sig') as stream:
                    json.load(stream)
            elif kind == 'toml':
                with p.open('rb') as stream:
                    tomllib.load(stream)
            else:
                ET.parse(p)
            counts['checked'] += 1
            counts['checked_' + kind] += 1
        except (SyntaxError, json.JSONDecodeError, ET.ParseError, ValueError, UnicodeError) as exc:
            counts['checked'] += 1
            counts['checked_' + kind] += 1
            line = getattr(exc, 'lineno', None)
            if line is None and isinstance(exc, ET.ParseError):
                line = exc.position[0]
            findings.append(finding(label, kind, 'صياغة غير صالحة أو ترميز غير صالح', line))
        except OSError:
            counts['unreadable'] += 1
            findings.append(finding(label, kind, 'تعذرت قراءة الملف'))
    return {'schema_version': 1, 'scope': 'git tracked and non-ignored untracked files',
            'total_files': len(files), 'counts': dict(sorted(counts.items())),
            'extensions': dict(sorted(extensions.items())), 'findings': findings,
            'limitations': ['Syntax checks only; no code execution, semantic analysis or security guarantee.',
                            'Other file types are inventoried only; ignored files, symlinks and sensitive files are skipped.',
                            'TOML checking requires Python 3.11+.']}


def github_run(run_id: str, root: Path) -> dict:
    if not run_id.isdecimal():
        return {'status': 'error', 'message': 'معرّف التشغيل يجب أن يكون رقمياً.'}
    try:
        result = subprocess.run(['gh', 'run', 'view', run_id, '--json', 'url,status,conclusion,jobs'],
                                cwd=root, capture_output=True, text=True, timeout=30)
        if result.returncode:
            return {'status': 'unavailable', 'message': 'تعذر جلب التشغيل عبر gh. تحقق من الاتصال وصلاحيات GitHub.'}
        data = json.loads(result.stdout)
        jobs = []
        for job in data.get('jobs', []):
            if job.get('conclusion') in ('failure', 'timed_out', 'cancelled', 'action_required'):
                jobs.append({'name': job.get('name'), 'conclusion': job.get('conclusion'),
                             'url': job.get('url'), 'failed_steps': [s.get('name') for s in job.get('steps', [])
                                                                        if s.get('conclusion') == 'failure']})
        return {'status': 'ok', 'url': data.get('url'), 'conclusion': data.get('conclusion'), 'failed_jobs': jobs,
                'limitations': 'Job and step metadata only; logs and root causes are not automatically retrieved.'}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {'status': 'unavailable', 'message': 'تعذر تشغيل gh أو قراءة استجابته.'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'), help='Git repository root')
    parser.add_argument('--json', type=Path, help='write JSON report to this path')
    parser.add_argument('--github-run', metavar='ID', help='optionally inspect GitHub Actions run metadata via gh')
    parser.add_argument('--fail-on-findings', action='store_true', help='exit 1 if syntax/read findings are present')
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        report = scan(root)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.github_run:
        report['github_actions'] = github_run(args.github_run, root)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.json:
        args.json.write_text(rendered, encoding='utf-8')
        print(f'كُتب التقرير: {args.json} | ملفات: {report["total_files"]} | نتائج: {len(report["findings"])}')
    else:
        print(rendered, end='')
    return 1 if args.fail_on_findings and report['findings'] else 0


if __name__ == '__main__':
    sys.exit(main())
