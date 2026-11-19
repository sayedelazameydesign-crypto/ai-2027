"""Swappable structlog + OpenTelemetry observability (Local, Persistent, Distributed, Export)."""

from __future__ import annotations

import io
import json
import os
import re
import threading
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx
import structlog
from opentelemetry import context as otel_context
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import NonRecordingSpan, Span, SpanContext, Status, StatusCode, TraceFlags
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from structlog.typing import EventDict, WrappedLogger

from .contracts import SpanRecord, StrictModel
from .security import SecurityValidationError, redact_secrets, scrub_mapping, validate_provider_url

_PROPAGATOR = TraceContextTextMapPropagator()
_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")


class ObservabilityMode(StrEnum):
    """Supported zero-cost observability profiles."""

    LOCAL = "local"
    PERSISTENT = "persistent"
    DISTRIBUTED = "distributed"
    EXPORT = "export"


class ObservabilityConfig(StrictModel):
    """Configuration controlling local, persistent, distributed, and optional export sinks."""

    mode: ObservabilityMode = ObservabilityMode.LOCAL
    persist_dir: Path | None = None
    otlp_endpoint: str | None = None

    @classmethod
    def from_env(cls, *, default_dir: Path | None = None) -> ObservabilityConfig:
        """Build an ObservabilityConfig from environment variables safely."""
        raw_mode = os.environ.get("AI2027_OBSERVABILITY_MODE", "").strip().lower()
        mode = (
            ObservabilityMode(raw_mode)
            if raw_mode in {m.value for m in ObservabilityMode}
            else (
                ObservabilityMode.PERSISTENT if default_dir is not None else ObservabilityMode.LOCAL
            )
        )
        raw_dir = os.environ.get("AI2027_TELEMETRY_DIR", "").strip()
        persist_dir = Path(raw_dir).resolve() if raw_dir else default_dir
        raw_otlp = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        otlp_endpoint: str | None = None
        if raw_otlp:
            try:
                otlp_endpoint = validate_provider_url(raw_otlp, allow_local_http=True)
            except SecurityValidationError:
                otlp_endpoint = None
        return cls(mode=mode, persist_dir=persist_dir, otlp_endpoint=otlp_endpoint)


@runtime_checkable
class SpanExporterPort(Protocol):
    """Swappable span exporter contract for JSONL persistence or external OTLP sinks."""

    @property
    def name(self) -> str:
        """Return exporter name."""
        ...

    def export_spans(self, spans: Sequence[SpanRecord]) -> int:
        """Export sanitized SpanRecord batches and return count written/accepted."""
        ...


def _readable_to_record(span: ReadableSpan) -> SpanRecord | None:
    ctx = span.get_span_context()
    if ctx is None:  # pragma: no cover - defensive
        return None
    start_ns = span.start_time or 0
    end_ns = span.end_time or start_ns
    duration_ms = round(max(0, end_ns - start_ns) / 1_000_000.0, 3)
    attrs: dict[str, str | int | float | bool] = {}
    for key, val in (span.attributes or {}).items():
        if isinstance(val, str | int | float | bool):
            attrs[str(key)] = redact_secrets(val) if isinstance(val, str) else val
    status_label: str = "ERROR" if span.status.status_code == StatusCode.ERROR else "OK"
    parent_id = (
        f"{span.parent.span_id:016x}" if span.parent is not None and span.parent.is_valid else None
    )
    return SpanRecord(
        name=span.name,
        trace_id=f"{ctx.trace_id:032x}",
        span_id=f"{ctx.span_id:016x}",
        parent_span_id=parent_id,
        duration_ms=duration_ms,
        status="ERROR" if status_label == "ERROR" else "OK",
        attributes=attrs,
    )


class JsonlFileSpanExporter:
    """Append-only JSONL span exporter persisting runtime evidence to disk at $0."""

    name = "jsonl-file"

    def __init__(self, path: Path) -> None:
        """Initialize the file exporter targeting path/spans.jsonl."""
        self.path = path
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def export_spans(self, spans: Sequence[SpanRecord]) -> int:
        """Append sanitized spans as JSON lines safely."""
        if not spans:
            return 0
        lines = "".join(item.model_dump_json() + "\n" for item in spans)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(lines)
        return len(spans)

    def read_recent(self, *, limit: int = 100) -> list[SpanRecord]:
        """Reload persisted SpanRecord entries from disk."""
        if not self.path.is_file():
            return []
        with self._lock:
            raw_lines = self.path.read_text(encoding="utf-8").splitlines()
        out: list[SpanRecord] = []
        for raw in raw_lines[-limit:]:
            if not raw.strip():
                continue
            try:
                out.append(SpanRecord.model_validate_json(raw))
            except ValueError:
                continue
        return out


class HttpOtlpSpanExporter:
    """Optional bounded OTLP/JSON HTTP span exporter when an endpoint is configured."""

    name = "otlp-http-json"

    def __init__(self, endpoint_url: str, *, client: httpx.Client | None = None) -> None:
        """Bind to a validated HTTPS or loopback HTTP OTLP endpoint."""
        self.endpoint_url = validate_provider_url(endpoint_url, allow_local_http=True)
        self._client = client
        self.last_error: str | None = None

    def export_spans(self, spans: Sequence[SpanRecord]) -> int:
        """Post sanitized span records as JSON without crashing on offline targets."""
        if not spans:
            return 0
        payload = {
            "service": "ai2027-python-2027",
            "spans": [s.model_dump(mode="json") for s in spans],
        }
        target = (
            self.endpoint_url
            if self.endpoint_url.endswith("/v1/traces")
            else f"{self.endpoint_url}/v1/traces"
        )
        try:
            if self._client is not None:
                resp = self._client.post(target, json=payload, timeout=3.0)
            else:
                with httpx.Client(follow_redirects=False, timeout=3.0) as client:
                    resp = client.post(target, json=payload)
            if 200 <= resp.status_code < 300:
                self.last_error = None
                return len(spans)
            self.last_error = f"http_{resp.status_code}"
        except Exception as exc:
            self.last_error = redact_secrets(type(exc).__name__)
        return 0


class _JsonlSpanProcessor(SpanProcessor):
    """OpenTelemetry SpanProcessor that streams each ended span to a SpanExporterPort."""

    def __init__(self, exporter: SpanExporterPort) -> None:
        self._exporter = exporter

    def on_end(self, span: ReadableSpan) -> None:
        rec = _readable_to_record(span)
        if rec is not None:
            self._exporter.export_spans([rec])


def _scrub_event_dict(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Scrub secret patterns and sensitive keys before structlog rendering."""
    cleaned = scrub_mapping(event_dict)
    if isinstance(cleaned.get("event"), str):
        cleaned["event"] = redact_secrets(cleaned["event"])
    return cleaned


def _inject_trace_context(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Attach active OpenTelemetry trace and span IDs to log events when present."""
    span = otel_trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        event_dict.setdefault("trace_id", f"{ctx.trace_id:032x}")
        event_dict.setdefault("span_id", f"{ctx.span_id:016x}")
    return event_dict


def extract_trace_context(headers: Mapping[str, str]) -> otel_context.Context:
    """Extract W3C traceparent or X-Trace-Id headers into an OpenTelemetry Context."""
    lowered = {str(k).lower(): str(v).strip() for k, v in headers.items()}
    if "traceparent" in lowered:
        carrier = {"traceparent": lowered["traceparent"]}
        if "tracestate" in lowered:
            carrier["tracestate"] = lowered["tracestate"]
        ctx = _PROPAGATOR.extract(carrier=carrier)
        if otel_trace.get_current_span(ctx).get_span_context().is_valid:
            return ctx
    raw_trace = lowered.get("x-trace-id", "").lower()
    if _HEX32_RE.fullmatch(raw_trace) and int(raw_trace, 16) != 0:
        span_ctx = SpanContext(
            trace_id=int(raw_trace, 16),
            span_id=int(uuid.uuid4().hex[:16], 16) or 1,
            is_remote=True,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )
        return otel_trace.set_span_in_context(NonRecordingSpan(span_ctx))
    return otel_context.get_current()


def inject_trace_headers(headers: dict[str, str] | None = None) -> dict[str, str]:
    """Inject active W3C traceparent and X-Trace-Id headers for distributed propagation."""
    out = dict(headers or {})
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    if "traceparent" in carrier:
        out["traceparent"] = carrier["traceparent"]
    ctx = otel_trace.get_current_span().get_span_context()
    if ctx.is_valid:
        out["x-trace-id"] = f"{ctx.trace_id:032x}"
    return out


class ObservabilityRuntime:
    """Swappable structlog + OTel runtime supporting Local, Persistent, Distributed, Export."""

    def __init__(
        self,
        *,
        service_name: str = "ai2027-python-2027",
        json_logs: bool = True,
        log_stream: io.StringIO | None = None,
        config: ObservabilityConfig | None = None,
        extra_exporters: Sequence[SpanExporterPort] = (),
    ) -> None:
        """Initialize isolated TracerProvider, optional file persistence, and structlog."""
        self.service_name = service_name
        self.config = config or ObservabilityConfig()
        self._exporter = InMemorySpanExporter()
        self._provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        self._provider.add_span_processor(SimpleSpanProcessor(self._exporter))
        self._file_exporter: JsonlFileSpanExporter | None = None
        self._log_file_lock = threading.Lock()
        self._log_file_path: Path | None = None
        if self.config.persist_dir is not None:
            self._file_exporter = JsonlFileSpanExporter(self.config.persist_dir / "spans.jsonl")
            self._provider.add_span_processor(_JsonlSpanProcessor(self._file_exporter))
            self._log_file_path = self.config.persist_dir / "logs.jsonl"
        self._extra_exporters: list[SpanExporterPort] = list(extra_exporters)
        if self.config.otlp_endpoint:
            self._extra_exporters.append(HttpOtlpSpanExporter(self.config.otlp_endpoint))

        self._tracer = self._provider.get_tracer(service_name, "0.2.0")
        self._stream = log_stream if log_stream is not None else io.StringIO()

        renderer: structlog.types.Processor = (
            structlog.processors.JSONRenderer()
            if json_logs
            else structlog.dev.ConsoleRenderer(colors=False)
        )
        self._logger = structlog.wrap_logger(
            structlog.PrintLogger(file=self._stream),
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso", utc=True),
                _inject_trace_context,
                _scrub_event_dict,
                renderer,
            ],
            wrapper_class=structlog.BoundLogger,
        )

    @contextmanager
    def start_span(
        self,
        name: str,
        attributes: Mapping[str, str | int | float | bool] | None = None,
        *,
        parent_context: otel_context.Context | None = None,
    ) -> Iterator[Span]:
        """Open a real OpenTelemetry span linked to parent_context when provided."""
        safe_attrs = scrub_mapping(attributes) if attributes else {}
        with self._tracer.start_as_current_span(name, context=parent_context) as span:
            for key, value in safe_attrs.items():
                if isinstance(value, str | int | float | bool):
                    span.set_attribute(key, value)
            try:
                yield span
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, redact_secrets(str(exc))[:200]))
                raise
            else:
                span.set_status(Status(StatusCode.OK))

    def current_trace_id(self) -> str:
        """Return the active 32-char OTel trace ID or create a fresh hex ID."""
        ctx = otel_trace.get_current_span().get_span_context()
        if ctx.is_valid:
            return f"{ctx.trace_id:032x}"
        return uuid.uuid4().hex

    def log(self, level: str, event: str, **fields: object) -> None:
        """Emit a scrubbed structured log event to memory and optional JSONL storage."""
        safe_fields = scrub_mapping(fields)
        clean_event = redact_secrets(event)
        prev_len = len(self._stream.getvalue())
        method = getattr(self._logger, level.lower(), self._logger.info)
        method(clean_event, **safe_fields)
        if self._log_file_path is not None:
            chunk = self._stream.getvalue()[prev_len:]
            if chunk:
                with self._log_file_lock:
                    self._log_file_path.parent.mkdir(parents=True, exist_ok=True)
                    with self._log_file_path.open("a", encoding="utf-8") as fh:
                        fh.write(chunk)

    def log_text(self) -> str:
        """Return captured structured log lines as a single string."""
        return self._stream.getvalue()

    def exported_spans(self) -> list[SpanRecord]:
        """Convert finished in-memory OpenTelemetry spans into Pydantic V2 SpanRecord models."""
        spans: tuple[ReadableSpan, ...] = self._exporter.get_finished_spans()
        records: list[SpanRecord] = []
        for span in spans:
            rec = _readable_to_record(span)
            if rec is not None:
                records.append(rec)
        return records

    def persisted_spans(self, *, limit: int = 100) -> list[SpanRecord]:
        """Reload persisted spans from JSONL storage, or fall back to memory spans."""
        if self._file_exporter is not None:
            return self._file_exporter.read_recent(limit=limit)
        return self.exported_spans()[-limit:]

    def export_now(self, *, limit: int = 100) -> dict[str, int]:
        """Push recent spans to configured external/extra SpanExporterPort destinations."""
        spans = self.persisted_spans(limit=limit)
        results: dict[str, int] = {}
        for exporter in self._extra_exporters:
            results[exporter.name] = exporter.export_spans(spans)
        return results

    def status_summary(self) -> dict[str, Any]:
        """Return active observability mode, persisted span counts, and export targets."""
        return {
            "mode": self.config.mode.value,
            "persistent_enabled": self._file_exporter is not None,
            "persist_dir": str(self.config.persist_dir) if self.config.persist_dir else None,
            "persisted_spans_count": len(self.persisted_spans(limit=500)),
            "in_memory_spans_count": len(self.exported_spans()),
            "distributed_headers": ["traceparent", "x-trace-id"],
            "extra_exporters": [exp.name for exp in self._extra_exporters],
            "otlp_configured": bool(self.config.otlp_endpoint),
        }

    def shutdown(self) -> None:
        """Flush and close the underlying OpenTelemetry TracerProvider."""
        self._provider.shutdown()


def format_observability_json(runtime: ObservabilityRuntime) -> str:
    """Serialize status summary + persisted spans safely as JSON."""
    payload = {
        "status": runtime.status_summary(),
        "spans": [s.model_dump(mode="json") for s in runtime.persisted_spans(limit=50)],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)
