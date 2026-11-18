"""Create an approval request; this command never executes it."""
import argparse
import json
from pathlib import Path
from approval_gate import ApprovalGate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--path', default='demo/hello.txt')
    parser.add_argument('--content', default='Hello from CeliaOS verified execution.\n')
    args = parser.parse_args()
    result = ApprovalGate(args.runtime).request('verified_file', {'path': args.path, 'content': args.content})
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
