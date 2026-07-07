"""Tests for the TvastarEventExporter — spec-conformant event emission."""

import json
import os

import pytest

from tvastar.event_exporter import TvastarEventExporter
from tvastar.observability import Span, Tracer


@pytest.fixture
def event_file(tmp_path):
    return str(tmp_path / "events.jsonl")


def test_tool_call_emits_conformant_event(event_file):
    exporter = TvastarEventExporter(event_file)
    span = Span(
        name="tool.execute",
        attributes={
            "session": "sess_abc123",
            "step": 3,
            "tool": "bash",
            "input": {"command": "pytest -q"},
            "output": "3 passed",
            "is_error": False,
        },
    )
    span.end = span.start + 1.5
    exporter.export(span)

    with open(event_file) as f:
        event = json.loads(f.readline())

    assert event["spec_version"] == "tvastar/0.1"
    assert event["event_type"] == "tool_call"
    assert event["run_id"] == "sess_abc123"
    assert event["step"] == 3
    assert event["tool"]["name"] == "bash"
    assert event["tool"]["output"] == "3 passed"
    assert event["tool"]["is_error"] is False
    assert event["tool"]["duration_ms"] == pytest.approx(1500, abs=50)


def test_detection_emits_verification_block(event_file):
    exporter = TvastarEventExporter(event_file)
    span = Span(
        name="detector.unverified_completion",
        attributes={
            "session": "sess_xyz",
            "step": 7,
            "detector.name": "unverified_completion",
            "result": "FAIL",
            "severity": "error",
            "message": "claims success but last tool shows failure",
            "correction": "Re-run pytest now. Do NOT claim success.",
        },
    )
    span.end = span.start + 0.01
    exporter.export(span)

    with open(event_file) as f:
        event = json.loads(f.readline())

    assert event["spec_version"] == "tvastar/0.1"
    assert event["event_type"] == "detection"
    assert event["verification"]["result"] == "FAIL"
    assert event["verification"]["detector"] == "unverified_completion"
    assert event["verification"]["severity"] == "error"
    assert "correction" in event["verification"]


def test_unrecognized_span_is_skipped(event_file):
    exporter = TvastarEventExporter(event_file)
    span = Span(name="model.generate", attributes={"step": 1})
    span.end = span.start + 0.5
    exporter.export(span)

    assert not os.path.exists(event_file) or os.path.getsize(event_file) == 0


def test_approval_event(event_file):
    exporter = TvastarEventExporter(event_file)
    span = Span(
        name="event.approval",
        attributes={
            "session": "sess_001",
            "step": 2,
            "tool": "bash",
            "approved": True,
            "approver": "model:claude-haiku-3",
            "reason": "read-only operation",
        },
    )
    span.end = span.start
    exporter.export(span)

    with open(event_file) as f:
        event = json.loads(f.readline())

    assert event["event_type"] == "approval"
    assert event["approval"]["approved"] is True
    assert event["approval"]["approver"] == "model:claude-haiku-3"


def test_compaction_event(event_file):
    exporter = TvastarEventExporter(event_file)
    span = Span(
        name="event.compaction",
        attributes={
            "session": "sess_002",
            "step": 15,
            "messages_before": 47,
            "messages_after": 12,
            "trigger": "overflow",
        },
    )
    span.end = span.start
    exporter.export(span)

    with open(event_file) as f:
        event = json.loads(f.readline())

    assert event["event_type"] == "compaction"
    assert event["compaction"]["messages_before"] == 47
    assert event["compaction"]["messages_after"] == 12
    assert event["compaction"]["trigger"] == "overflow"


def test_tracer_integration(event_file):
    """TvastarEventExporter works as a Tracer exporter."""
    exporter = TvastarEventExporter(event_file)
    tracer = Tracer(exporters=[exporter])

    with tracer.span("tool.execute", session="s1", step=0, tool="read"):
        pass

    with open(event_file) as f:
        event = json.loads(f.readline())

    assert event["spec_version"] == "tvastar/0.1"
    assert event["event_type"] == "tool_call"
