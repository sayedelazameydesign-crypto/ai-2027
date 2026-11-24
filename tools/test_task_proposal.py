import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('task_proposal', Path(__file__).with_name('task_proposal.py'))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'source.py').write_text('value = 1\n', encoding='utf-8')

    def test_preview_never_modifies_file(self):
        result = tool.propose(self.root, {'path': 'source.py', 'old': '1', 'new': '2'})
        self.assertEqual(result['status'], 'proposal_only')
        self.assertIn('+value = 2', result['diff'])
        self.assertEqual((self.root / 'source.py').read_text(), 'value = 1\n')
        self.assertFalse(result['tests_run'])
        self.assertFalse(result['file_modified'])

    def test_path_traversal_and_sensitive_paths_rejected(self):
        for name in ['../source.py', '/etc/passwd', 'dir/../../source.py', '.git/config', '.env.local', 'file.key']:
            with self.subTest(name=name), self.assertRaises(tool.ProposalError):
                tool.propose(self.root, {'path': name, 'old': 'x', 'new': 'y'})

    def test_symlink_file_and_directory_rejected(self):
        (self.root / 'link').symlink_to(self.root / 'source.py')
        (self.root / 'dirlink').symlink_to(self.root, target_is_directory=True)
        for name in ['link', 'dirlink/source.py']:
            with self.subTest(name=name), self.assertRaises(tool.ProposalError):
                tool.propose(self.root, {'path': name, 'old': '1', 'new': '2'})

    def test_no_match_ambiguous_and_oversized_rejected(self):
        (self.root / 'source.py').write_text('value = 11\n', encoding='utf-8')
        for old in ['missing', '1']:
            with self.assertRaises(tool.ProposalError):
                tool.propose(self.root, {'path': 'source.py', 'old': old, 'new': '2'})
        (self.root / 'large.py').write_text('x' * (tool.MAX_BYTES + 1))
        with self.assertRaises(tool.ProposalError):
            tool.propose(self.root, {'path': 'large.py', 'old': 'x', 'new': 'y'})

    def test_explicit_confirmation_never_modifies_file(self):
        request = {'path': 'source.py', 'old': '1', 'new': '2'}
        preview = tool.propose(self.root, request)
        receipt = tool.approve(self.root, request, preview['proposal_id'])
        self.assertEqual(receipt['status'], 'confirmed_not_applied')
        self.assertFalse(receipt['file_modified'])
        self.assertFalse(receipt['tests_run'])
        self.assertEqual((self.root / 'source.py').read_text(), 'value = 1\n')

    def test_confirmation_rejects_stale_file_or_changed_request(self):
        request = {'path': 'source.py', 'old': '1', 'new': '2'}
        preview = tool.propose(self.root, request)
        with self.assertRaises(tool.ProposalError):
            tool.approve(self.root, {**request, 'new': '3'}, preview['proposal_id'])
        (self.root / 'source.py').write_text('# changed\nvalue = 1\n')
        with self.assertRaises(tool.ProposalError):
            tool.approve(self.root, request, preview['proposal_id'])
        with self.assertRaises(tool.ProposalError):
            tool.approve(self.root, request, 'invalid')

    def test_confirmation_is_bound_to_workspace(self):
        request = {'path': 'source.py', 'old': '1', 'new': '2'}
        preview = tool.propose(self.root, request)
        with tempfile.TemporaryDirectory() as other:
            (Path(other) / 'source.py').write_text('value = 1\n')
            with self.assertRaises(tool.ProposalError):
                tool.approve(Path(other), request, preview['proposal_id'])

    def test_cli_rejects_invalid_request(self):
        proc = subprocess.run([sys.executable, str(Path(__file__).with_name('task_proposal.py')),
                               '--root', str(self.root)], input=json.dumps({'path': '../x'}),
                              text=True, capture_output=True)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stderr)['status'], 'rejected')


if __name__ == '__main__':
    unittest.main()
