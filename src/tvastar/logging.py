"""Structured logging — single-line JSON log entries with tracing integration.

Provides :class:`StructuredLogger`, a zero-dependency structured logger that emits
one JSON object per line. Integrates with the existing span stack in
:mod:`tvastar.observability` via contextvars for automatic correlation.

Design: logging is *never* allowed to break a run. On write failure, the logger
falls back to raw ``print(..., file=sys.stderr)`` — the ONE permitted escape hatch
for unstructured output (REQ-13 AC6).
"""

from __future__ import annotations

import contextvars
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TextIO

from tvastar.observability import _span_stack

# Correlation ID propagated via contextvars — set by the caller (e.g., HTTP middleware).
_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_correlation_id", default=None
)


@dataclass
class StructuredLogger:
    """Emits single-line JSON log entries."""

    name: str
    output: TextIO = field(default_factory=lambda: sys.stderr)
    min_level: str = "DEBUG"

    def emit(self, level: str, message: str, **fields: Any) -> None:
        """Write one JSON line. On failure, fall back to raw stderr."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "logger_name": self.name,
            "message": message[:4096],
            **self._context_fields(),
            **fields,
        }
        try:
            line = json.dumps(entry, default=str) + "\n"
            self.output.write(line)
            if self.output is not sys.stderr:
                self.output.flush()
        except Exception:
            # AC6 escape hatch: logging failure falls back to raw stderr
            print(f"[LOG FAILURE] {level} {self.name}: {message}", file=sys.stderr)

    def _context_fields(self) -> dict[str, Any]:
        """Pull correlation_id, span_id, trace_id from active context."""
        fields: dict[str, Any] = {}
        # Integration with observability.py Tracer
        stack = _span_stack.get()
        if stack:
            fields["span_id"] = stack[-1]
            fields["trace_id"] = stack[0]  # root span = trace
        # correlation_id from contextvars if set
        cid = _correlation_id.get(None)
        if cid:
            fields["correlation_id"] = cid
        return fields
