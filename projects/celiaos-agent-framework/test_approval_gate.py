"""Offline approval workflow tests; test decisions are simulated, not user consent."""
import concurrent.futures
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from approval_gate import ApprovalGate
from execution.action_executor import ActionExecutor
from execution.verified_executor import VerifiedFileExecutor


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.gate = ApprovalGate(self.root)
        self.executor = ActionExecutor({'verified_file': VerifiedFileExecutor(self.root)})
        self.req = self.gate.request('verified_file', {'path': 'demo/a.txt', 'content': 'hello'})
        self.id, self.digest = self.req['id'], self.req['digest']

    def approve(self):
        self.gate.decide(self.id, 'APPROVED', self.digest)

    def test_unapproved_cannot_execute(self):
        with self.assertRaises(RuntimeError):
            self.executor.run('verified_file', {'id': self.id, 'digest': self.digest})
        self.assertFalse((self.root / 'execution').exists())

    def test_approved_real_execution_once(self):
        self.approve()
        result = self.executor.run('verified_file', {'id': self.id, 'digest': self.digest})
        self.assertEqual(result['state'], 'COMPLETED')
        self.assertEqual(result['result']['status'], 'VERIFIED')
        with self.assertRaises(RuntimeError):
            self.executor.run('verified_file', {'id': self.id, 'digest': self.digest})
        self.assertEqual(ApprovalGate(self.root).get(self.id)['state'], 'COMPLETED')

    def test_reject_expiry_and_digest(self):
        with self.assertRaises(ValueError):
            self.gate.decide(self.id, 'APPROVED', 'wrong')
        self.gate.decide(self.id, 'REJECTED', self.digest)
        with self.assertRaises(ValueError):
            self.gate.execute(self.id, self.digest, self.executor)
        req = self.gate.request('verified_file', {'path':'b', 'content':'x'}, ttl=1)
        with patch('approval_gate.time.time', return_value=req['expires'] + 1):
            with self.assertRaises(ValueError):
                self.gate.decide(req['id'], 'APPROVED', req['digest'])

    def test_changed_payload_cannot_execute(self):
        self.approve()
        with sqlite3.connect(self.gate.path) as db:
            db.execute('UPDATE approvals SET params=? WHERE id=?', ('{"content":"changed","path":"demo/a.txt"}', self.id))
        with self.assertRaises(ValueError):
            self.gate.execute(self.id, self.digest, self.executor)

    def test_concurrent_claim_only_one(self):
        self.approve()
        def execute():
            try:
                return self.executor.run('verified_file', {'id': self.id, 'digest': self.digest})['state']
            except RuntimeError:
                return 'REJECTED'
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: execute(), range(2)))
        self.assertCountEqual(results, ['COMPLETED', 'REJECTED'])

    def test_executor_failure_is_unknown_and_not_retryable(self):
        self.approve()
        class Failing:
            def run(self, *args):
                raise RuntimeError('failed')
        self.assertEqual(self.gate.execute(self.id, self.digest, Failing())['state'], 'UNKNOWN')
        with self.assertRaises(ValueError):
            self.gate.execute(self.id, self.digest, self.executor)


if __name__ == '__main__':
    unittest.main()
