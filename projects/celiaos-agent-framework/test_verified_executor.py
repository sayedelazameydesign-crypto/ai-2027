"""Offline tests: execution requires a bound approval at the worker boundary."""
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from approval_gate import ApprovalGate
from execution.action_executor import ActionExecutor
from execution.verified_executor import VerifiedFileExecutor


class VerifiedExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.gate = ApprovalGate(self.root)
        self.executor = ActionExecutor({'verified_file': VerifiedFileExecutor(self.root)})

    def request(self, path, content='hello'):
        return self.gate.request('verified_file', {'path': path, 'content': content})

    def run_file(self, path, content='hello'):
        req = self.request(path, content)
        self.gate.decide(req['id'], 'APPROVED', req['digest'])  # simulated test consent
        return self.executor.run('verified_file', {'id': req['id'], 'digest': req['digest']})

    def test_real_execution_evidence_and_memory_survive_worker_restart(self):
        first = self.run_file('a/hello.txt')
        result = first['result']
        self.assertEqual(first['state'], 'COMPLETED')
        self.assertEqual(result['status'], 'VERIFIED')
        self.assertTrue(result['independent_hash_matches'])
        self.assertEqual(result['artifact_sha256'], hashlib.sha256(b'hello').hexdigest())
        self.assertTrue(result['memory']['admitted'])
        path = self.root / 'execution' / 'runs' / (result['run_id'] + '.json')
        self.assertEqual(json.loads(path.read_text())['status'], 'VERIFIED')
        self.assertEqual(self.run_file('b/hello.txt', 'world')['state'], 'COMPLETED')
        with sqlite3.connect(self.root / 'execution' / 'state' / 'memory.db') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM memories').fetchone()[0], 2)
        self.assertTrue(path.exists())

    def test_worker_requires_approval_and_rejects_direct_params(self):
        req = self.request('demo/file.txt')
        with self.assertRaises(RuntimeError):
            self.executor.run('verified_file', {'id': req['id'], 'digest': req['digest']})
        with self.assertRaises(ValueError):
            self.executor.run('verified_file', {'path': 'file.txt', 'content': 'x'})
        self.assertFalse((self.root / 'execution').exists())
        worker = Path(__file__).parent / 'execution' / 'verified_worker.py'
        direct = subprocess.run([sys.executable, str(worker), str(self.root)],
                                input=json.dumps({'path': 'file.txt', 'content': 'x'}),
                                text=True, capture_output=True)
        self.assertNotEqual(direct.returncode, 0)
        self.assertFalse((self.root / 'execution').exists())

    def test_traversal_is_denied(self):
        result = self.run_file('../../escape.txt')
        self.assertEqual(result['state'], 'FAILED')
        self.assertEqual(result['result']['status'], 'DENIED')
        self.assertFalse(result['result']['memory']['admitted'])

    def test_memory_failure_is_not_success(self):
        state = self.root / 'execution' / 'state'
        state.mkdir(parents=True)
        # Keep the SQLite path a regular file so startup's runtime-layout
        # validation passes; corrupt contents exercise the nonfatal memory
        # ingestion failure after the kernel has completed the run.
        (state / 'memory.db').write_bytes(b'not-a-sqlite-database')
        result = self.run_file('file.txt')
        self.assertEqual(result['state'], 'FAILED')
        self.assertEqual(result['result']['status'], 'VERIFIED')
        self.assertFalse(result['result']['memory']['admitted'])


if __name__ == '__main__':
    unittest.main()
