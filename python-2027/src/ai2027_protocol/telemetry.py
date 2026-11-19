"""Structured logging (structlog) and OpenTelemetry tracing runtime."""

from __future__ import annotations

import io
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

import structlog
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Span, Status, StatusCode
from structlog.typing import EventDict, WrappedLogger

from .contracts import SpanRecord
from .security import redact_secrets, scrub_mapping


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


class ObservabilityRuntime:
    """Self-contained structlog + OpenTelemetry runtime with zero collector cost."""

    def __init__(
        self,
        *,
        service_name: str = "ai2027-python-2027",
        json_logs: bool = True,
        log_stream: io.StringIO | None = None,
    ) -> None:
        """Initialize an isolated TracerProvider, span exporter, and structlog logger."""
        self.service_name = service_name
        self._exporter = InMemorySpanExporter()
        self._provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        self._provider.add_span_processor(SimpleSpanProcessor(self._exporter))
        self._tracer = self._provider.get_tracer(service_name, "0.1.0")
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
    ) -> Iterator[Span]:
        """Open a real OpenTelemetry span and record errors without swallowing exceptions."""
        safe_attrs = scrub_mapping(attributes) if attributes else {}
        with self._tracer.start_as_current_span(name) as span:
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
        """Emit a scrubbed structured log event at the given level."""
        safe_fields = scrub_mapping(fields)
        method = getattr(self._logger, level.lower(), self._logger.info)
        method(redact_secrets(event), **safe_fields)

    def log_text(self) -> str:
        """Return captured structured log lines as a single string."""
        return self._stream.getvalue()

    def exported_spans(self) -> list[SpanRecord]:
        """Convert finished OpenTelemetry spans into Pydantic V2 SpanRecord models."""
        spans: tuple[ReadableSpan, ...] = self._exporter.get_finished_spans()
        records: list[SpanRecord] = []
        for span in spans:
            ctx = span.get_span_context()
            if ctx is None:  # pragma: no cover - defensive
                continue
            start_ns = span.start_time or 0
            end_ns = span.end_time or start_ns
            duration_ms = round(max(0, end_ns - start_ns) / 1_000_000.0, 3)
            attrs: dict[str, str | int | float | bool] = {}
            for key, val in (span.attributes or {}).items():
                if isinstance(val, str | int | float | bool):
                    attrs[str(key)] = redact_secrets(val) if isinstance(val, str) else val
            status_label: str = "ERROR" if span.status.status_code == StatusCode.ERROR else "OK"
            parent_id = (
                f"{span.parent.span_id:016x}"
                if span.parent is not None and span.parent.is_valid
                else None
            )
            records.append(
                SpanRecord(
                    name=span.name,
                    trace_id=f"{ctx.trace_id:032x}",
                    span_id=f"{ctx.span_id:016x}",
                    parent_span_id=parent_id,
                    duration_ms=duration_ms,
                    status="ERROR" if status_label == "ERROR" else "OK",
                    attributes=attrs,
                )
            )
        return records

    def shutdown(self) -> None:
        """Flush and close the underlying OpenTelemetry TracerProvider."""
        self._provider.shutdown()
