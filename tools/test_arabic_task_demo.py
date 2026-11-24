import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('task_demo', Path(__file__).with_name('arabic_task_demo.py'))
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class ArabicTaskDemoTests(unittest.TestCase):
    def test_real_fixed_task_and_cleanup(self):
        result = demo.demonstration()
        self.assertEqual(result['status'], 'verified')
        self.assertFalse(result['model_used'])
        self.assertIn('-    return 0', result['diff'])
        self.assertIn('+    return a + b', result['diff'])
        self.assertEqual(result['test_exit_code'], 0)
        self.assertEqual(result['observed_sha256'], result['expected_sha256'])
        self.assertTrue(result['temporary_workspace_removed'])

    def test_cancellation_before_any_execution(self):
        with patch.object(demo.tempfile, 'TemporaryDirectory', side_effect=AssertionError('must not create workspace')):
            result = demo.demonstration(cancel=True)
        self.assertEqual(result['status'], 'cancelled')
        self.assertFalse(result['changed'])
        self.assertFalse(result['tests_run'])

    def test_failed_test_never_reports_verified(self):
        real_run = demo.run
        def fail_test(argv, root, env):
            if '-m' in argv:
                return subprocess.CompletedProcess(argv, 1, '', 'synthetic failure')
            return real_run(argv, root, env)
        with patch.object(demo, 'run', side_effect=fail_test):
            result = demo.demonstration()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['test_exit_code'], 1)
        self.assertTrue(result['temporary_workspace_removed'])

    def test_tampered_artifact_is_not_verified(self):
        real_run = demo.run
        def tamper(argv, root, env):
            outcome = real_run(argv, root, env)
            if '-m' in argv:
                (root / 'calculator.py').write_text('def add(a, b):\n    return -1\n')
            return outcome
        with patch.object(demo, 'run', side_effect=tamper):
            result = demo.demonstration()
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['artifact_verified'])


if __name__ == '__main__':
    unittest.main()
