"""Local approval flow. Approval is a workflow gate, not user authentication."""
import argparse
import json
from pathlib import Path

from approval_gate import ApprovalGate
from execution.action_executor import ActionExecutor
from execution.verified_executor import VerifiedFileExecutor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True, type=Path)
    sub = parser.add_subparsers(dest='operation', required=True)
    req = sub.add_parser('request')
    req.add_argument('--path', required=True)
    req.add_argument('--content', required=True)
    req.add_argument('--ttl', type=int, default=900)
    for name in ('approve', 'reject', 'execute'):
        cmd = sub.add_parser(name)
        cmd.add_argument('id')
        cmd.add_argument('--digest', required=True)
    show = sub.add_parser('show')
    show.add_argument('id')
    args = parser.parse_args()
    gate = ApprovalGate(args.runtime)
    if args.operation == 'request':
        result = gate.request('verified_file', {'path': args.path, 'content': args.content}, args.ttl)
    elif args.operation == 'show':
        result = gate.get(args.id)
    elif args.operation in ('approve', 'reject'):
        result = gate.decide(args.id, 'APPROVED' if args.operation == 'approve' else 'REJECTED', args.digest)
    else:
        executor = ActionExecutor({'verified_file': VerifiedFileExecutor(args.runtime)})
        result = executor.run('verified_file', {'id': args.id, 'digest': args.digest})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result['state'] in ('FAILED', 'UNKNOWN') else 0


if __name__ == '__main__':
    raise SystemExit(main())
