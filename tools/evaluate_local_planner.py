#!/usr/bin/env python3
"""Small local task benchmark; offline fixtures, no code execution or remote fallback."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile

import local_task_planner as planner
import task_proposal

# Exact-answer fixtures: not a broad quality benchmark or evidence of parity.
CASES = (
    {'id': 'add', 'task': 'أصلح دالة الجمع بحيث تعيد مجموع العددين.',
     'source': 'def add(a, b):\n    return 0\n', 'expected': 'def add(a, b):\n    return a + b\n'},
    {'id': 'multiply', 'task': 'أصلح الدالة بحيث تعيد حاصل ضرب العددين.',
     'source': 'def multiply(a, b):\n    return 0\n', 'expected': 'def multiply(a, b):\n    return a * b\n'},
    {'id': 'greeting', 'task': 'غيّر الرسالة إلى أهلاً مع بقاء بنية الملف كما هي.',
     'source': 'message = "وداعاً"\n', 'expected': 'message = "أهلاً"\n'},
)


def evaluate(model: str, port: int, consent: bool, query=None) -> dict:
    if not consent:
        return {'status': 'BLOCKED', 'reason': 'يلزم إذن صريح لاستخدام النموذج المحلي', 'cases': []}
    results = []
    for case in CASES:
        with tempfile.TemporaryDirectory(prefix='ai2027-eval-') as tmp:
            root = Path(tmp)
            (root / 'case.py').write_text(case['source'], encoding='utf-8')
            try:
                kwargs = {} if query is None else {'query': query}
                output = planner.plan(root, case['task'], 'case.py', model, port, True, **kwargs)
                expected_hash = hashlib.sha256(case['expected'].encode('utf-8')).hexdigest()
                matches = output['preview']['proposed_sha256'] == expected_hash
                results.append({'id': case['id'], 'result': 'pass' if matches else 'fail',
                                'criterion': 'exact expected file bytes'})
            except planner.Blocked:
                return {'status': 'BLOCKED', 'reason': 'النموذج المحلي غير متاح؛ القياس غير مكتمل',
                        'model': model, 'cases': results}
            except task_proposal.ProposalError:
                results.append({'id': case['id'], 'result': 'rejected',
                                'criterion': 'invalid or unsafe proposal'})
    return {'status': 'complete', 'model': model, 'cases': results,
            'passed': sum(item['result'] == 'pass' for item in results), 'total': len(CASES),
            'limitations': 'Three fixed exact-match fixtures only; generated code was not executed; no competitor comparison.'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--port', type=int, default=11434)
    parser.add_argument('--allow-local-model', action='store_true')
    args = parser.parse_args()
    try:
        report = evaluate(args.model, args.port, args.allow_local_model)
    except (ValueError, OSError) as exc:
        print(json.dumps({'status': 'error', 'reason': str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'complete' and report['passed'] == report['total'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
