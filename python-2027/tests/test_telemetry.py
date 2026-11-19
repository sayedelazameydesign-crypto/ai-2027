from __future__ import annotations

import json

import pytest

from ai2027_protocol.telemetry import ObservabilityRuntime


def test_observability_records_spans_and_scrubbed_json_logs() -> None:
    runtime = ObservabilityRuntime(json_logs=True)
    fake_secret = "AIza" + "X" * 35
    with runtime.start_span("outer.op", {"api_key": fake_secret, "count": 3}):
        trace_id = runtime.current_trace_id()
        assert len(trace_id) == 32
        runtime.log("info", f"processing key {fake_secret}", token=fake_secret, safe="ok")
        with runtime.start_span("inner.op", {"step": 1}):
            pass

    spans = runtime.exported_spans()
    assert len(spans) == 2
    inner, outer = spans[0], spans[1]
    assert inner.name == "inner.op"
    assert inner.parent_span_id == outer.span_id
    assert outer.name == "outer.op"
    assert outer.status == "OK"
    assert outer.attributes["api_key"] == "[REDACTED]"
    assert outer.attributes["count"] == 3

    lines = [json.loads(line) for line in runtime.log_text().splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0]["token"] == "[REDACTED]"  # noqa: S105
    assert fake_secret not in json.dumps(lines[0])
    assert lines[0]["trace_id"] == trace_id
    runtime.shutdown()


def test_observability_records_error_span_and_console_mode() -> None:
    runtime = ObservabilityRuntime(json_logs=False)
    assert len(runtime.current_trace_id()) == 32
    fake_sk = "sk-proj-" + "Y" * 34
    with pytest.raises(RuntimeError), runtime.start_span("failing.op"):
        raise RuntimeError(f"boom {fake_sk}")

    spans = runtime.exported_spans()
    assert len(spans) == 1
    assert spans[0].status == "ERROR"
    runtime.log("warning", "console_warning", step="done")
    assert "console_warning" in runtime.log_text()
    runtime.shutdown()
