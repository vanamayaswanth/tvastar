"""Observability — structured tracing with pluggable exporters.

The harness emits :class:`Span`-based events; a :class:`Tracer` fans them out to
any number of exporters. Built-in exporters: console (human-readable),
JSONL-file (machine-readable), and a null default (zero overhead). An
OpenTelemetry exporter shim is provided if the SDK is installed, so traces can
flow to OpenTelemetry / Braintrust / Sentry-style backends.

Design: tracing is *never* allowed to break a run. Exporter exceptions are
swallowed and logged, never propagated.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol


@dataclass
class Span:
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    parent_id: Optional[str] = None
    start: float = field(default_factory=time.time)
    end: Optional[float] = None
    status: str = "ok"
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def duration_ms(self) -> Optional[float]:
        return None if self.end is None else (self.end - self.start) * 1000


class Exporter(Protocol):
    def export(self, span: Span) -> None: ...


class NullExporter:
    def export(self, span: Span) -> None:  # noqa: D401
        return None


class ConsoleExporter:
    def __init__(self, stream=sys.stderr):
        self.stream = stream

    def export(self, span: Span) -> None:
        dur = f"{span.duration_ms:.0f}ms" if span.duration_ms is not None else "?"
        attrs = " ".join(f"{k}={v}" for k, v in span.attributes.items())
        print(f"[trace] {span.name} ({dur}) {span.status} {attrs}", file=self.stream)


class JSONLExporter:
    """Append one JSON object per span to a file — easy to grep/replay."""

    def __init__(self, path: str = "tvastar-trace.jsonl"):
        self.path = path

    def export(self, span: Span) -> None:
        rec = {
            "name": span.name,
            "span_id": span.span_id,
            "parent_id": span.parent_id,
            "duration_ms": span.duration_ms,
            "status": span.status,
            "attributes": span.attributes,
            "events": span.events,
            "start": span.start,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")


class OTelExporter:  # pragma: no cover - optional dep
    """Best-effort OpenTelemetry bridge (no-op if SDK absent)."""

    def __init__(self, tracer_name: str = "tvastar"):
        try:
            from opentelemetry import trace

            self._otel = trace.get_tracer(tracer_name)
        except Exception:
            self._otel = None

    def export(self, span: Span) -> None:
        if not self._otel:
            return
        with self._otel.start_as_current_span(span.name) as s:
            for k, v in span.attributes.items():
                s.set_attribute(k, v)


class Tracer:
    def __init__(
        self,
        exporters: Optional[list[Exporter]] = None,
        content_filter: Optional[Callable[[Span], Span]] = None,
    ):
        self.exporters: list[Exporter] = exporters or [NullExporter()]
        self.content_filter = content_filter
        self._stack: list[str] = []

    @contextmanager
    def span(self, name: str, **attributes: Any):
        span = Span(
            name=name,
            attributes=attributes,
            parent_id=self._stack[-1] if self._stack else None,
        )
        self._stack.append(span.span_id)
        try:
            yield span
        except Exception as e:
            span.status = f"error: {type(e).__name__}"
            raise
        finally:
            span.end = time.time()
            self._stack.pop()
            self._emit(span)

    def event(self, span: Span, name: str, **data: Any) -> None:
        span.events.append({"name": name, "at": time.time(), **data})

    def _emit(self, span: Span) -> None:
        if self.content_filter is not None:
            try:
                span = self.content_filter(span)
            except Exception:
                pass  # filter failures must never break a run
        for ex in self.exporters:
            try:
                ex.export(span)
            except Exception as e:  # never let tracing break a run
                print(f"[trace-error] exporter failed: {e}", file=sys.stderr)


#: a process-wide default; replace via Tracer(...) per harness as needed.
NULL_TRACER = Tracer()
