import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import evaluate_local_planner as evaluation
import local_task_planner as planner


class EvaluationTests(unittest.TestCase):
    def test_exact_match_with_fake_model(self):
        def fake(model, port, prompt):
            data = json.loads(prompt.split('\n', 1)[1])
            case = next(c for c in evaluation.CASES if c['task'] == data['task'])
            return json.dumps({'old': case['source'], 'new': case['expected']})
        result = evaluation.evaluate('fake-model', 11434, True, fake)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['passed'], 3)
        self.assertEqual(result['total'], 3)
        self.assertIsNone(result['metadata']['model_version'])
        self.assertEqual(result['metadata']['memory_or_gpu'], 'not measured')
        self.assertTrue(all(c['elapsed_ms'] >= 0 for c in result['cases']))

    def test_structural_match_ignores_formatting_without_executing_code(self):
        self.assertEqual(evaluation.compare_candidate('value=\'أهلاً\'\n', 'value = "أهلاً"\n'), 'structural')
        self.assertEqual(evaluation.compare_candidate('x = 3\n', 'x = 2\n'), 'different')
        self.assertEqual(evaluation.compare_candidate('def invalid(:\n', 'x = 1\n'), 'invalid_python')

    def test_structural_equivalent_model_output_passes(self):
        def fake(model, port, prompt):
            data = json.loads(prompt.split('\n', 1)[1])
            case = next(c for c in evaluation.CASES if c['task'] == data['task'])
            formatted = case['expected'].replace(' = ', '=').replace('a + b', 'a+b').replace('a * b', 'a*b')
            return json.dumps({'old': case['source'], 'new': formatted})
        result = evaluation.evaluate('fake-model', 11434, True, fake)
        self.assertEqual(result['passed'], 3)
        self.assertTrue(all(c['match'] == 'structural' for c in result['cases']))

    def test_wrong_and_rejected_output_do_not_pass(self):
        def wrong(model, port, prompt):
            data = json.loads(prompt.split('\n', 1)[1])
            return json.dumps({'old': data['source'], 'new': 'wrong\n'})
        result = evaluation.evaluate('fake-model', 11434, True, wrong)
        self.assertEqual(result['passed'], 0)
        self.assertTrue(all(c['result'] == 'fail' for c in result['cases']))
        result = evaluation.evaluate('fake-model', 11434, True, lambda *args: 'invalid')
        self.assertEqual(result['passed'], 0)
        self.assertTrue(all(c['result'] == 'rejected' for c in result['cases']))

    def test_missing_consent_or_server_is_blocked(self):
        def blocked(*args):
            raise planner.Blocked('unavailable')
        self.assertEqual(evaluation.evaluate('fake', 11434, False, blocked)['status'], 'BLOCKED')
        result = evaluation.evaluate('fake', 11434, True, blocked)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['cases'], [])
        self.assertNotIn('passed', result)
        self.assertEqual(result['blocked_case'], 'add')
        self.assertIsNone(result['metadata']['model_version'])
        self.assertGreaterEqual(result['blocked_elapsed_ms'], 0)


if __name__ == '__main__':
    unittest.main()
