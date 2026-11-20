"""The Ollama transport path, proven over a real socket.

This file exists because ``ScriptedBackend`` proves too little. It shows the
fleet can consume a completion; it says nothing about endpoint validation,
loopback allowance, request shape, JSON decoding, or ``message.content``
extraction — a bug in any of those would be invisible to it.

``FakeOllamaServer`` speaks the real ``/api/chat`` contract over TCP on
loopback, so the *same* ``OllamaBackend`` object that would talk to a real
Ollama is exercised here. What it returns is scripted, and every test that
reports a number says so in its name or its assertion message.

The distinction this file insists on: **the transport is real, the intelligence
is not.** A score from these tests describes the plumbing, never a model.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from fake_ollama import FakeOllamaServer  # noqa: E402
from fleet2027.bench import run_benchmark, write_report  # noqa: E402
from fleet2027.llm import LLMRequest, OllamaBackend  # noqa: E402

HTML_ANSWER = """```html
<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<title>متجر العسل</title></head><body>
<h1>متجر العسل البلدي</h1>
<p>نجمع العسل من مناحل الفيوم مباشرة، ونعبئه بعد الطلب. كل برطمان يحمل رقم
الدفعة وتاريخ الجني، ويصل خلال ثمان وأربعين ساعة إلى جميع المحافظات المصرية.</p>
<h2>لماذا نحن</h2>
<p>لا وسطاء ولا تخزين طويل. السعر يشمل التوصيل، وإن لم يعجبك المنتج نسترده
خلال أسبوع بلا أسئلة. للطلب تواصل معنا وسنؤكد الطلب قبل الشحن.</p>
</body></html>
```"""

MD_ANSWER = """```markdown
# تقرير موجز

هذا النص مكتوب للقارئ لا تعليمات له بأن يكتبه، ويحتوي على معلومات كافية ليكون
مستندًا حقيقيًا. يشرح الفكرة الأساسية بوضوح ثم ينتقل إلى التفاصيل العملية.

## التفاصيل

- نقطة أولى تشرح الأساس وتذكر مثالًا عمليًا يمكن التحقق منه.
- نقطة ثانية تناقش الحدود وما لا يغطيه هذا المستند بوضوح.

## الخلاصة

خلاصة قصيرة تقول ما يجب فعله بعد ذلك، بلا حشو ولا مبالغة.
```"""


class TransportTests(unittest.TestCase):
    """The HTTP path itself: validate, POST, decode, extract."""

    def test_a_completion_travels_over_a_real_socket(self) -> None:
        with FakeOllamaServer(responses=[HTML_ANSWER]) as server:
            backend = OllamaBackend(base_url=server.url, model="fake:1b")
            response = backend.complete(
                LLMRequest(
                    prompt="اكتب موقعًا", system="sys", temperature=0.2,
                    max_tokens=100, timeout_s=10,
                )
            )
        self.assertEqual(response.backend, "ollama")
        self.assertEqual(response.model, "fake:1b")
        self.assertIn("<h1>", response.text)

    def test_the_prompt_reaches_the_server(self) -> None:
        """The instruction must survive every layer between plan and model."""
        with FakeOllamaServer(responses=[HTML_ANSWER]) as server:
            OllamaBackend(base_url=server.url).complete(
                LLMRequest(prompt="نص عربي مميز", system="", temperature=0.1,
                           max_tokens=10, timeout_s=10)
            )
        sent = json.dumps(server.requests[0], ensure_ascii=False)
        self.assertIn("نص عربي مميز", sent)

    def test_the_system_prompt_is_sent_as_a_system_message(self) -> None:
        with FakeOllamaServer(responses=[HTML_ANSWER]) as server:
            OllamaBackend(base_url=server.url).complete(
                LLMRequest(prompt="p", system="أنت كاتب دقيق", temperature=0.1,
                           max_tokens=10, timeout_s=10)
            )
        roles = [m["role"] for m in server.requests[0]["messages"]]
        self.assertEqual(roles, ["system", "user"])

    def test_a_non_loopback_http_endpoint_is_refused(self) -> None:
        """Plain HTTP to a remote host would leak prompts over the wire."""
        backend = OllamaBackend(base_url="http://example.com:11434")
        with self.assertRaises(Exception) as caught:  # noqa: B017 - kernel error type
            backend.complete(
                LLMRequest(prompt="p", system="", temperature=0.1, max_tokens=5,
                           timeout_s=5)
            )
        self.assertIn("loopback", str(caught.exception).lower())

    def test_unreachable_server_fails_rather_than_hanging(self) -> None:
        backend = OllamaBackend(base_url="http://127.0.0.1:1")
        with self.assertRaises(Exception):  # noqa: B017 - any transport error is fine
            backend.complete(
                LLMRequest(prompt="p", system="", temperature=0.1, max_tokens=5,
                           timeout_s=2)
            )


class EndToEndTests(unittest.TestCase):
    """The whole loop through HTTP: corpus → tasks → files → scores."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = FakeOllamaServer(responses=[HTML_ANSWER, MD_ANSWER])
        cls.server.start()
        cls.backend = OllamaBackend(base_url=cls.server.url, model="scripted:1b")
        cls.report = run_benchmark(backend=cls.backend)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.stop()

    def test_every_sentence_completes(self) -> None:
        """Research used to dead-letter here: its gathering step carried no
        ``out``, so an executor that writes files refused it."""
        self.assertEqual(self.report.completion_rate, 1.0)

    def test_a_model_call_is_recorded_in_evidence(self) -> None:
        """Not a flag the caller passed — read from the step's own record."""
        self.assertTrue(self.report.model_called)
        self.assertEqual(self.report.backend, "ollama")

    def test_the_server_was_actually_used(self) -> None:
        """At least one request per sentence proves the socket carried the work."""
        self.assertGreaterEqual(len(self.server.requests), len(self.report.results))

    def test_scripted_answers_score_above_the_scaffold(self) -> None:
        """A real, if scripted, answer beats a placeholder — the ruler has resolution."""
        baseline = 0.5783
        self.assertGreater(self.report.mean_score, baseline)

    def test_non_empty_artifacts_are_produced(self) -> None:
        for row in self.report.results:
            with self.subTest(sentence=row.sentence_id):
                self.assertGreater(row.chars, 0, "no artifact was written")
                self.assertEqual(row.status, "succeeded")


class HonestyTests(unittest.TestCase):
    """What these tests may and may not be read as proving."""

    def test_the_report_says_which_backend_produced_it(self) -> None:
        with FakeOllamaServer(responses=[HTML_ANSWER]) as server:
            report = run_benchmark(backend=OllamaBackend(base_url=server.url))
        payload = report.to_dict()
        self.assertEqual(payload["backend"], "ollama")
        self.assertTrue(payload["model_called"])

    def test_a_scaffold_run_records_no_model_call(self) -> None:
        """The contrast that makes the field meaningful."""
        report = run_benchmark()
        self.assertFalse(report.model_called)
        self.assertEqual(report.backend, "none")

    def test_reports_round_trip_through_json(self) -> None:
        with FakeOllamaServer(responses=[HTML_ANSWER]) as server:
            report = run_benchmark(backend=OllamaBackend(base_url=server.url))
        with tempfile.TemporaryDirectory() as tmp:
            path = write_report(report, Path(tmp) / "r.json")
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual(payload["summary"]["sentences"], 24)


if __name__ == "__main__":
    unittest.main(verbosity=2)
