"""Unit tests for StructuredLogger (REQ-13 AC1–AC3, AC6, AC7)."""

from __future__ import annotations

import io
import json

from tvastar.logging import StructuredLogger, _correlation_id
from tvastar.observability import _span_stack


def test_emit_produces_valid_single_line_json():
    buf = io.StringIO()
    logger = StructuredLogger(name="test.component", output=buf)
    logger.emit("INFO", "hello world")

    line = buf.getvalue()
    assert line.endswith("\n")
    assert line.count("\n") == 1  # single-line

    entry = json.loads(line)
    assert entry["level"] == "INFO"
    assert entry["logger_name"] == "test.component"
    assert entry["message"] == "hello world"
    assert "timestamp" in entry
    # Timestamp should contain timezone info (ends with +00:00 for UTC)
    assert "+" in entry["timestamp"] or "Z" in entry["timestamp"]


def test_message_truncated_to_4096():
    buf = io.StringIO()
    logger = StructuredLogger(name="trunc", output=buf)
    long_msg = "x" * 5000
    logger.emit("DEBUG", long_msg)

    entry = json.loads(buf.getvalue())
    assert len(entry["message"]) == 4096


def test_context_fields_from_span_stack():
    buf = io.StringIO()
    logger = StructuredLogger(name="ctx", output=buf)

    # Simulate a span stack (root + child)
    token = _span_stack.set(["root-span-id", "child-span-id"])
    try:
        logger.emit("INFO", "with spans")
    finally:
        _span_stack.reset(token)

    entry = json.loads(buf.getvalue())
    assert entry["trace_id"] == "root-span-id"
    assert entry["span_id"] == "child-span-id"


def test_correlation_id_included_when_set():
    buf = io.StringIO()
    logger = StructuredLogger(name="corr", output=buf)

    token = _correlation_id.set("req-abc-123")
    try:
        logger.emit("INFO", "correlated")
    finally:
        _correlation_id.reset(token)

    entry = json.loads(buf.getvalue())
    assert entry["correlation_id"] == "req-abc-123"


def test_no_context_fields_when_empty():
    buf = io.StringIO()
    logger = StructuredLogger(name="bare", output=buf)
    logger.emit("WARNING", "no context")

    entry = json.loads(buf.getvalue())
    assert "span_id" not in entry
    assert "trace_id" not in entry
    assert "correlation_id" not in entry


def test_extra_fields_passed_through():
    buf = io.StringIO()
    logger = StructuredLogger(name="extra", output=buf)
    logger.emit("ERROR", "oops", request_id="r1", status_code=500)

    entry = json.loads(buf.getvalue())
    assert entry["request_id"] == "r1"
    assert entry["status_code"] == 500


def test_escape_hatch_on_write_failure(capsys):
    """AC6: on write failure, fall back to raw print to stderr."""

    class BrokenStream:
        def write(self, _: str) -> int:
            raise OSError("disk full")

    logger = StructuredLogger(name="broken", output=BrokenStream())  # type: ignore[arg-type]
    logger.emit("ERROR", "important message")

    captured = capsys.readouterr()
    assert "[LOG FAILURE]" in captured.err
    assert "ERROR" in captured.err
    assert "broken" in captured.err
    assert "important message" in captured.err


def test_flush_on_file_output():
    """AC7: flush per-entry when writing to file output (not stderr)."""

    class TrackingStream:
        def __init__(self):
            self.written = []
            self.flush_count = 0

        def write(self, data: str) -> int:
            self.written.append(data)
            return len(data)

        def flush(self) -> None:
            self.flush_count += 1

    stream = TrackingStream()
    logger = StructuredLogger(name="flush", output=stream)  # type: ignore[arg-type]
    logger.emit("INFO", "one")
    logger.emit("INFO", "two")

    assert stream.flush_count == 2


def test_no_flush_on_stderr():
    """Stderr should not be explicitly flushed (it auto-flushes)."""
    import sys

    # We can't easily track stderr flush calls, but we verify the code path
    # doesn't crash and produces output
    buf = io.StringIO()
    logger = StructuredLogger(name="stderr_test", output=buf)
    # Pretend buf IS stderr for the identity check
    logger.output = sys.stderr
    # This should not crash (stderr flush is skipped)
    logger.emit("DEBUG", "to stderr")
