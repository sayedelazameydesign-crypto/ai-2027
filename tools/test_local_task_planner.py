import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import local_task_planner as planner
import task_proposal


class LocalPlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'demo.py'
        self.path.write_text('def add(a, b):\n    return 0\n')

    def test_valid_response_is_only_preview(self):
        def fake(model, port, prompt):
            self.assertIn('أصلح الجمع', prompt)
            return json.dumps({'old': 'return 0', 'new': 'return a + b'})
        result = planner.plan(self.root, 'أصلح الجمع', 'demo.py', 'local-model', 11434, True, fake)
        self.assertEqual(result['status'], 'proposal_only')
        self.assertFalse(result['file_modified'])
        self.assertFalse(result['tests_run'])
        self.assertIn('+    return a + b', result['preview']['diff'])
        self.assertIn('return 0', self.path.read_text())

    def test_missing_consent_never_queries(self):
        def fake(*args):
            raise AssertionError('network must not be called')
        with self.assertRaises(planner.Blocked):
            planner.plan(self.root, 'أصلح', 'demo.py', 'model', 11434, False, fake)

    def test_untrusted_model_output_rejected(self):
        for response in ['not json', '{"old":"return 0","new":"x","tool":"shell"}',
                         '{"old":"missing","new":"x"}']:
            with self.subTest(response=response), self.assertRaises(task_proposal.ProposalError):
                planner.plan(self.root, 'أصلح', 'demo.py', 'model', 11434, True,
                             lambda *args: response)
        self.assertIn('return 0', self.path.read_text())

    def test_changed_file_during_model_call_rejected(self):
        def fake(*args):
            self.path.write_text('# modified\ndef add(a, b):\n    return 0\n')
            return json.dumps({'old': 'return 0', 'new': 'return a + b'})
        with self.assertRaises(task_proposal.ProposalError):
            planner.plan(self.root, 'أصلح', 'demo.py', 'model', 11434, True, fake)

    def test_traversal_rejected_before_query(self):
        with self.assertRaises(task_proposal.ProposalError):
            planner.plan(self.root, 'أصلح', '../secret', 'model', 11434, True,
                         lambda *args: self.fail('must not query'))

    def test_absent_server_is_blocked_not_fake_success(self):
        class Broken:
            def request(self, *args, **kwargs):
                raise ConnectionRefusedError()
            def close(self):
                pass
        from unittest.mock import patch
        with patch.object(planner.http.client, 'HTTPConnection', return_value=Broken()):
            with self.assertRaises(planner.Blocked):
                planner.query_local('model', 11434, 'hi')

    def test_real_loopback_http_produces_reviewable_preview_only(self):
        requests = []
        class FakeOllama(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers['Content-Length'])
                requests.append((self.path, json.loads(self.rfile.read(length))))
                output = json.dumps({'response': json.dumps({'old': 'return 0', 'new': 'return a + b'})}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(output)))
                self.end_headers()
                self.wfile.write(output)
            def log_message(self, *_args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), FakeOllama)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            result = planner.plan(self.root, 'أصلح الجمع', 'demo.py', 'mock-model', server.server_port, True)
        finally:
            server.shutdown()
            worker.join(timeout=3)
            server.server_close()
        self.assertEqual(result['status'], 'proposal_only')
        self.assertEqual(result['model'], 'mock-model')
        self.assertIn('return a + b', result['preview']['diff'])
        self.assertIn('return 0', self.path.read_text())
        self.assertEqual(requests[0][0], '/api/generate')
        self.assertFalse(requests[0][1]['stream'])
        self.assertIn('أصلح الجمع', requests[0][1]['prompt'])
        # Separate, explicit approval on the disposable fixture; planner never applies.
        applied = task_proposal.apply(self.root, result['request_for_review'], result['preview']['proposal_id'])
        self.assertEqual(applied['status'], 'applied')
        self.assertIn('return a + b', self.path.read_text())

    def test_loopback_redirect_is_blocked(self):
        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(302)
                self.send_header('Location', 'https://example.com/')
                self.end_headers()
            def log_message(self, *_args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with self.assertRaises(planner.Blocked):
                planner.query_local('mock-model', server.server_port, 'task')
        finally:
            server.shutdown()
            worker.join(timeout=3)
            server.server_close()

    def test_cli_without_consent(self):
        cmd = [sys.executable, str(Path(__file__).with_name('local_task_planner.py')),
               '--root', str(self.root), '--model', 'model']
        proc = subprocess.run(cmd, input=json.dumps({'task': 'أصلح', 'path': 'demo.py'}),
                              text=True, capture_output=True)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)['status'], 'BLOCKED')


if __name__ == '__main__':
    unittest.main()
