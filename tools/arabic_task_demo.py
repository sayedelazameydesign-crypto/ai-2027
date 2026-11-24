#!/usr/bin/env python3
"""Offline, deterministic Arabic task on a disposable Git repository.

This is a fixed demonstration, not an AI agent or a sandbox for untrusted code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

TASK = 'أصلح دالة الجمع، واعرض التغييرات، وشغّل اختبارًا ثم قدّم دليل النتيجة.'
BEFORE = 'def add(a, b):\n    return 0\n'
AFTER = 'def add(a, b):\n    return a + b\n'
TEST = '''import unittest
from calculator import add

class AdditionTest(unittest.TestCase):
    def test_addition(self):
        self.assertEqual(add(2, 3), 5)
        self.assertEqual(add(-2, 1), -1)

if __name__ == '__main__':
    unittest.main()
'''


def digest(text: bytes) -> str:
    return hashlib.sha256(text).hexdigest()


def child_env(home: Path) -> dict[str, str]:
    # Avoid user Git configuration, credentials and Python path injection.
    return {'PATH': os.defpath, 'HOME': str(home), 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': os.devnull, 'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
            'LC_ALL': 'C.UTF-8'}


def run(argv: list[str], root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True, timeout=20, check=False)


def git(root: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return run(['git', '-c', 'core.hooksPath=' + os.devnull, *args], root, env)


def demonstration(cancel: bool = False) -> dict:
    result: dict = {'schema_version': 1, 'task': TASK, 'mode': 'fixed_deterministic_demo',
                    'model_used': False, 'external_network_used_by_demo': False,
                    'limitations': ['Not a general agent or security sandbox.',
                                    'Only fixed trusted demonstration code is executed.']}
    if cancel:
        result.update(status='cancelled', changed=False, tests_run=False)
        return result
    with tempfile.TemporaryDirectory(prefix='ai2027-task-') as tmp:
        root = Path(tmp)
        env = child_env(root)
        init = git(root, env, 'init', '-q')
        if init.returncode:
            raise RuntimeError('تعذر إنشاء مستودع Git تجريبي')
        (root / 'calculator.py').write_text(BEFORE, encoding='utf-8')
        (root / 'test_calculator.py').write_text(TEST, encoding='utf-8')
        added = git(root, env, 'add', '--', 'calculator.py', 'test_calculator.py')
        if added.returncode:
            raise RuntimeError('تعذر تجهيز الملفات التجريبية')
        # Compare the edited working tree against the staged baseline; no commit is needed.
        target = root / 'calculator.py'
        if target.read_text(encoding='utf-8') != BEFORE:
            raise RuntimeError('لم تتطابق النسخة التجريبية مع النسخة المتوقعة')
        target.write_text(AFTER, encoding='utf-8')
        test = run([sys.executable, '-B', '-m', 'unittest', 'test_calculator', '-v'], root, env)
        diff = git(root, env, 'diff', '--no-ext-diff', '--', 'calculator.py')
        if diff.returncode:
            raise RuntimeError('تعذر قراءة التغييرات')
        # Verification reads the artifact again instead of trusting the edit operation.
        observed = digest(target.read_bytes())
        expected = digest(AFTER.encode('utf-8'))
        verified = observed == expected and bool(diff.stdout)
        passed = test.returncode == 0
        result.update(status='verified' if passed and verified else 'failed', changed=True,
                      tests_run=True, test_command='python -B -m unittest test_calculator -v',
                      test_exit_code=test.returncode, test_output=(test.stdout + test.stderr)[-4000:],
                      diff=diff.stdout, observed_sha256=observed, expected_sha256=expected,
                      artifact_verified=verified)
    result['temporary_workspace_removed'] = not root.exists()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cancel', action='store_true', help='cancel before creating the workspace')
    args = parser.parse_args()
    try:
        result = demonstration(args.cancel)
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        print(json.dumps({'status': 'error', 'message': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] in ('verified', 'cancelled') else 1


if __name__ == '__main__':
    sys.exit(main())
