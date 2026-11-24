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


if __name__ == '__main__':
    unittest.main()
