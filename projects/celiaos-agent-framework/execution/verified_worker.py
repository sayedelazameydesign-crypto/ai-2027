"""Worker: atomically consume approval immediately before local execution.

The guard is a workflow boundary, not an OS security boundary. Other code with
access to the kernel or approval database can bypass it.
"""
import contextlib
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FRAMEWORK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FRAMEWORK))
from approval_gate import ApprovalGate
# The framework's `memory` package would shadow the kernel's namespace package.
sys.path.remove(str(FRAMEWORK))


class KernelExecutor:
    def __init__(self, runtime):
        self.runtime = runtime

    def run(self, action, params):
        if action != 'verified_file':
            raise ValueError('unsupported action')
        sys.path.insert(0, str(ROOT / 'command-hud'))
        import kernel_bridge
        runtime = self.runtime / 'execution'
        for name in ('runs', 'sandboxes', 'state'):
            (runtime / name).mkdir(parents=True, exist_ok=True)
        server = kernel_bridge.load(runtime=runtime, fresh=False)
        record = server.run_goal('create_file', params)
        result = {'status': record.get('status'), 'run_id': record.get('run_id'),
                  'memory': record.get('memory'), 'artifact_sha256': None,
                  'independent_hash_matches': False}
        if record.get('status') == 'VERIFIED':
            item = server.sandbox_file(record['run_id'], params['path'])
            expected = next((obs.get('data', {}).get('sha256') for obs in record.get('observations', [])
                             if obs.get('data', {}).get('sha256')), None)
            if item is not None:
                result['artifact_sha256'] = hashlib.sha256(item[0]).hexdigest()
                result['independent_hash_matches'] = bool(expected and result['artifact_sha256'] == expected)
        return result


def main():
    if len(sys.argv) != 2:
        raise ValueError('runtime directory required')
    request = json.load(sys.stdin)
    if (not isinstance(request, dict) or set(request) != {'id', 'digest'} or
            not all(isinstance(request[k], str) for k in request)):
        raise ValueError('approval id and digest required')
    runtime = Path(sys.argv[1]).resolve()
    gate = ApprovalGate(runtime)
    with contextlib.redirect_stdout(sys.stderr):
        result = gate.execute(request['id'], request['digest'], KernelExecutor(runtime))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
