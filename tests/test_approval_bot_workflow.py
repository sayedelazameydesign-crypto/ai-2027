"""Narrow offline workflow policy guards, not a general YAML schema parser."""
from pathlib import Path
import re
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/approval-bot.yml"


class TestApprovalBotWorkflow(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        # Exclude comments so merely mentioning an endpoint is not a violation.
        self.code = "\n".join(line for line in self.text.splitlines()
                              if not line.lstrip().startswith("#"))

    def test_no_telegram_failure_diagnostics(self):
        for target in ("/deleteWebhook", "/setWebhook", "/getUpdates", "/getMe",
                       "/getWebhookInfo", "chat-discovery", "curl "):
            with self.subTest(target=target):
                self.assertNotIn(target, self.code)

    def test_schedule_still_runs_fail_closed_cycle(self):
        self.assertIn("inputs.mode || 'run'", self.code)
        self.assertIn('python3 scripts/approval_bot_ci.py --mode "$BOT_MODE"', self.code)
        self.assertIn('GOOGLE_SERVICE_ACCOUNT_JSON: ${{ secrets.GOOGLE_SERVICE_ACCOUNT_JSON }}', self.code)
        self.assertIn('PA_MEMORY_SHEET_ID: ${{ secrets.PA_MEMORY_SHEET_ID }}', self.code)
        self.assertIn('cancel-in-progress: false', self.code)
        self.assertIn('PA_WAAPI_NOTIFY: "0"', self.code)

    def test_preflight_precedes_install_and_cycle(self):
        preflight = self.code.index('--preflight-only')
        install = self.code.index('pip install')
        cycle = self.code.index('id: cycle')
        self.assertLess(preflight, install)
        self.assertLess(install, cycle)
        # Both commands use the same mode and job-level credentials.
        self.assertEqual(self.code.count('--mode "$BOT_MODE"'), 2)

    def test_cache_save_covers_failed_cycles_not_skipped_ones(self):
        self.assertNotIn('uses: actions/cache@', self.code)
        self.assertIn('uses: actions/cache/restore@', self.code)
        self.assertIn('uses: actions/cache/save@', self.code)
        condition = re.search(r"if: \$\{\{ (always\(\).*?) \}\}", self.code).group(1)
        for required in ("env.BOT_MODE == 'run'", "steps.cycle.outcome == 'success'",
                         "steps.cycle.outcome == 'failure'",
                         "hashFiles('.bot-state.json') != ''"):
            self.assertIn(required, condition)
        self.assertNotIn("outcome == 'skipped'", condition)
        self.assertEqual(self.code.count('key: approval-bot-state-${{ github.run_id }}-${{ github.run_attempt }}'), 2)


if __name__ == "__main__":
    unittest.main()
