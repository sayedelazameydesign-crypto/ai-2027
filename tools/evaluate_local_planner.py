#!/usr/bin/env python3
"""Small local task benchmark; offline fixtures, no code execution or remote fallback."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import platform
from pathlib import Path
import tempfile
import time
from datetime import datetime, timezone

import local_task_planner as planner
import task_proposal

# Fixed fixtures: not a broad quality benchmark or evidence of parity.
CASES = (
    {'id': 'add', 'task': 'أصلح دالة الجمع بحيث تعيد مجموع العددين.',
     'source': 'def add(a, b):\n    return 0\n', 'expected': 'def add(a, b):\n    return a + b\n'},
    {'id': 'multiply', 'task': 'أصلح الدالة بحيث تعيد حاصل ضرب العددين.',
     'source': 'def multiply(a, b):\n    return 0\n', 'expected': 'def multiply(a, b):\n    return a * b\n'},
    {'id': 'greeting', 'task': 'غيّر الرسالة إلى أهلاً مع بقاء بنية الملف كما هي.',
     'source': 'message = "وداعاً"\n', 'expected': 'message = "أهلاً"\n'},
)


def compare_candidate(candidate: str, expected: str) -> str:
    """Compare bytes and Python syntax trees, without executing generated code."""
    if candidate == expected:
        return 'exact'
    try:
        actual_tree = ast.dump(ast.parse(candidate), include_attributes=False)
        expected_tree = ast.dump(ast.parse(expected), include_attributes=False)
        if actual_tree == expected_tree:
            return 'structural'
        return 'different'
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return 'invalid_python'


def evaluate(model: str, port: int, consent: bool, query=None) -> dict:
    if not consent:
        return {'status': 'BLOCKED', 'reason': 'يلزم إذن صريح لاستخدام النموذج المحلي', 'cases': []}
    metadata = {'model_requested': model, 'model_version': None,
                'model_version_note': 'Not independently retrieved or verified.',
                'started_at_utc': datetime.now(timezone.utc).isoformat(),
                'python': platform.python_version(), 'platform': platform.system(),
                'architecture': platform.machine(), 'logical_cpus': os.cpu_count(),
                'memory_or_gpu': 'not measured',
                'timing_note': 'Wall time includes model request, local validation and preview; not model-only inference.'}
    results = []
    for case in CASES:
        with tempfile.TemporaryDirectory(prefix='ai2027-eval-') as tmp:
            root = Path(tmp)
            (root / 'case.py').write_text(case['source'], encoding='utf-8')
            start = time.perf_counter()
            try:
                kwargs = {} if query is None else {'query': query}
                output = planner.plan(root, case['task'], 'case.py', model, port, True, **kwargs)
                replacement = output['request_for_review']
                candidate = case['source'].replace(replacement['old'], replacement['new'], 1)
                candidate_hash = hashlib.sha256(candidate.encode('utf-8')).hexdigest()
                if candidate_hash != output['preview']['proposed_sha256']:
                    raise task_proposal.ProposalError('inconsistent candidate')
                match = compare_candidate(candidate, case['expected'])
                results.append({'id': case['id'], 'result': 'pass' if match in ('exact', 'structural') else 'fail',
                                'match': match, 'elapsed_ms': round((time.perf_counter() - start) * 1000, 2),
                                'criterion': 'exact bytes or same Python AST'})
            except planner.Blocked:
                return {'status': 'BLOCKED', 'reason': 'النموذج المحلي غير متاح؛ القياس غير مكتمل',
                        'metadata': metadata, 'blocked_case': case['id'],
                        'blocked_elapsed_ms': round((time.perf_counter() - start) * 1000, 2), 'cases': results}
            except task_proposal.ProposalError:
                results.append({'id': case['id'], 'result': 'rejected',
                                'elapsed_ms': round((time.perf_counter() - start) * 1000, 2),
                                'criterion': 'invalid or unsafe proposal'})
    return {'status': 'complete', 'model': model, 'metadata': metadata, 'cases': results,
            'passed': sum(item['result'] == 'pass' for item in results), 'total': len(CASES),
            'limitations': 'Three fixed fixtures and syntax-tree comparison only; code was not executed; no behavioral proof or competitor comparison.'}


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
