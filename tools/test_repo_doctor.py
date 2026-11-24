import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('repo_doctor', Path(__file__).with_name('repo_doctor.py'))
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)

    def test_scan_inventory_and_privacy(self):
        (self.root / 'bad.py').write_text('def bad(:\n', encoding='utf8')
        (self.root / 'bad.json').write_text('{"token": secret}', encoding='utf8')
        (self.root / 'good.xml').write_text('<root/>', encoding='utf8')
        (self.root / '.env').write_text('secret', encoding='utf8')
        (self.root / 'note.md').write_text('hello', encoding='utf8')
        (self.root / '.gitignore').write_text('ignored.py\n', encoding='utf8')
        (self.root / 'ignored.py').write_text('bad (', encoding='utf8')
        (self.root / 'linked.py').symlink_to(self.root / 'bad.py')
        report = doctor.scan(self.root)
        self.assertEqual({f['path'] for f in report['findings']}, {'bad.py', 'bad.json'})
        self.assertNotIn('secret', json.dumps(report))
        self.assertEqual(report['counts']['skipped_sensitive'], 1)
        self.assertEqual(report['counts']['skipped_symlink'], 1)
        self.assertEqual(report['counts']['checked'], 3)
        self.assertNotIn('ignored.py', json.dumps(report))

    def test_github_unavailable_and_invalid_id(self):
        self.assertEqual(doctor.github_run('bad', self.root)['status'], 'error')
        with patch.object(doctor.subprocess, 'run', side_effect=FileNotFoundError):
            self.assertEqual(doctor.github_run('123', self.root)['status'], 'unavailable')

    def test_exit_status(self):
        (self.root / 'bad.py').write_text('def x(:', encoding='utf8')
        script = Path(__file__).with_name('repo_doctor.py')
        result = subprocess.run(['python3', str(script), '--root', str(self.root), '--fail-on-findings'], capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(json.loads(result.stdout)['findings']), 1)


if __name__ == '__main__':
    unittest.main()
