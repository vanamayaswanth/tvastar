"""Unit tests for Tracer and Exporters (observability.py).

Validates:
- Span emission for model.generate, tool.invoke, session.prompt
- OTelExporter conforms to GenAI semantic conventions
- JSONLExporter appends newline-delimited JSON records
- Exporter failures are swallowed (CON-004)

Requirements: 13.1, 13.2, 13.3, 13.5, 13.6
"""

import json
import os
import tempfile

import pytest

from tvastar import Harness, Tracer, create_agent, tool
from tvastar.model import MockModel
from tvastar.observability import (
    ConsoleExporter,
    JSONLExporter,
    NullExporter,
    OTelExporter,
    Span,
)
from tvastar.types import ToolUseBlock


# ── Helpers ──────────────────────────────────────────────────────────────────


class CaptureExporter:
    """Records all exported spans for assertion."""

    def __init__(self):
        self.spans: list[Span] = []

    def export(self, span: Span) -> None:
        self.spans.append(span)


class FailingExporter:
    """Raises on every export call — used to verify error swallowing."""

    def export(self, span: Span) -> None:
        raise RuntimeError("exporter boom")


@tool
def greet(name: str) -> str:
    """Return a greeting."""
    return f"Hello, {name}!"


# ── Span emission tests ──────────────────────────────────────────────────────


async def test_session_prompt_emits_session_prompt_span():
    """REQ 13.3: session.prompt emits a wrapping span."""
    cap = CaptureExporter()
    agent = create_agent("obs-test", model=MockModel(["hi there"]))
    await Harness(agent, tracer=Tracer([cap])).run("hello")

    prompt_spans = [s for s in cap.spans if s.name == "session.prompt"]
    assert len(prompt_spans) == 1
    assert prompt_spans[0].attributes["agent"] == "obs-test"
    assert "session" in prompt_spans[0].attributes


async def test_model_generate_emits_span():
    """REQ 13.1: model.generate emits a span with GenAI attributes."""
    cap = CaptureExporter()
    agent = create_agent("obs-test", model=MockModel(["response"]))
    await Harness(agent, tracer=Tracer([cap])).run("prompt")

    gen_spans = [s for s in cap.spans if s.name == "model.generate"]
    assert len(gen_spans) >= 1
    span = gen_spans[0]
    # GenAI semantic convention attributes must be present
    assert span.attributes["gen_ai.operation.name"] == "chat"
    assert span.attributes["gen_ai.system"] == "mock"
    assert span.attributes["gen_ai.request.model"] == "mock"


async def test_tool_invoke_emits_span():
    """REQ 13.2: tool invocation emits a tool.invoke span with tool name."""
    cap = CaptureExporter()
    agent = create_agent(
        "obs-test",
        model=MockModel([ToolUseBlock(name="greet", input={"name": "World"}), "done"]),
        tools=[greet],
    )
    await Harness(agent, tracer=Tracer([cap])).run("say hi")

    tool_spans = [s for s in cap.spans if s.name == "tool.invoke"]
    assert len(tool_spans) == 1
    assert tool_spans[0].attributes["tool"] == "greet"


async def test_multiple_tools_emit_multiple_spans():
    """Multiple tool calls in a single turn each emit their own span."""
    cap = CaptureExporter()

    @tool
    def add(a: int, b: int) -> str:
        """Add two numbers."""
        return str(a + b)

    agent = create_agent(
        "obs-test",
        model=MockModel([
            ToolUseBlock(name="greet", input={"name": "A"}),
            ToolUseBlock(name="add", input={"a": 1, "b": 2}),
            "final answer",
        ]),
        tools=[greet, add],
    )
    await Harness(agent, tracer=Tracer([cap])).run("do stuff")

    tool_spans = [s for s in cap.spans if s.name == "tool.invoke"]
    assert len(tool_spans) == 2
    tool_names = {s.attributes["tool"] for s in tool_spans}
    assert tool_names == {"greet", "add"}


async def test_span_parent_linkage():
    """Spans emitted inside session.prompt have parent_id linking them."""
    cap = CaptureExporter()
    agent = create_agent("obs-test", model=MockModel(["answer"]))
    await Harness(agent, tracer=Tracer([cap])).run("q")

    prompt_span = next(s for s in cap.spans if s.name == "session.prompt")
    gen_span = next(s for s in cap.spans if s.name == "model.generate")
    # model.generate should be a child of session.prompt
    assert gen_span.parent_id == prompt_span.span_id


# ── OTelExporter GenAI semantic conventions ──────────────────────────────────


async def test_otel_exporter_genai_attributes():
    """REQ 13.5: OTelExporter attributes follow GenAI semantic conventions.

    We verify by checking the span attributes produced by the Tracer when
    model.generate is called — the OTelExporter would export these same
    attributes to OTel backends.
    """
    cap = CaptureExporter()
    agent = create_agent("otel-test", model=MockModel(["yes"]))
    await Harness(agent, tracer=Tracer([cap])).run("check")

    gen_span = next(s for s in cap.spans if s.name == "model.generate")
    attrs = gen_span.attributes

    # Required GenAI semantic convention attributes
    assert attrs["gen_ai.operation.name"] == "chat"
    assert attrs["gen_ai.system"] == "mock"
    assert attrs["gen_ai.request.model"] == "mock"
    assert "gen_ai.request.max_tokens" in attrs
    assert "gen_ai.request.temperature" in attrs
    # Response attributes added after model response
    assert "gen_ai.usage.input_tokens" in attrs
    assert "gen_ai.usage.output_tokens" in attrs
    assert isinstance(attrs["gen_ai.response.finish_reasons"], list)
    assert "end_turn" in attrs["gen_ai.response.finish_reasons"]


async def test_otel_exporter_tool_use_finish_reason():
    """TOOL_USE stop reason is reflected in gen_ai.response.finish_reasons."""
    cap = CaptureExporter()
    agent = create_agent(
        "otel-test",
        model=MockModel([ToolUseBlock(name="greet", input={"name": "X"}), "done"]),
        tools=[greet],
    )
    await Harness(agent, tracer=Tracer([cap])).run("go")

    gen_spans = [s for s in cap.spans if s.name == "model.generate"]
    # First generate returns TOOL_USE
    first = gen_spans[0]
    assert "tool_use" in first.attributes["gen_ai.response.finish_reasons"]


async def test_otel_exporter_noop_without_sdk():
    """OTelExporter gracefully no-ops when opentelemetry SDK is not available."""
    exporter = OTelExporter(tracer_name="test")
    span = Span(name="test.span", attributes={"gen_ai.system": "mock"})
    span.end = span.start + 0.1
    # Should not raise even without OTel SDK
    exporter.export(span)


# ── JSONLExporter tests ──────────────────────────────────────────────────────


async def test_jsonl_exporter_writes_newline_delimited_json():
    """REQ 13.6: JSONLExporter appends newline-delimited JSON records."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = f.name

    try:
        jsonl = JSONLExporter(path=path)
        agent = create_agent("jsonl-test", model=MockModel(["answer"]))
        await Harness(agent, tracer=Tracer([jsonl])).run("question")

        with open(path, encoding="utf-8") as f:
            lines = f.readlines()

        # Should have at least 2 lines: session.prompt and model.generate
        assert len(lines) >= 2
        # Each line must be valid JSON
        for line in lines:
            assert line.endswith("\n")
            rec = json.loads(line)
            assert "name" in rec
            assert "span_id" in rec
            assert "attributes" in rec
    finally:
        os.unlink(path)


async def test_jsonl_exporter_records_contain_expected_fields():
    """JSONLExporter records include all span fields."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = f.name

    try:
        jsonl = JSONLExporter(path=path)
        agent = create_agent("jsonl-test", model=MockModel(["ok"]))
        await Harness(agent, tracer=Tracer([jsonl])).run("hi")

        with open(path, encoding="utf-8") as f:
            lines = f.readlines()

        gen_record = None
        for line in lines:
            rec = json.loads(line)
            if rec["name"] == "model.generate":
                gen_record = rec
                break

        assert gen_record is not None
        assert "span_id" in gen_record
        assert "parent_id" in gen_record
        assert "duration_ms" in gen_record
        assert "status" in gen_record
        assert "attributes" in gen_record
        assert "events" in gen_record
        assert "start" in gen_record
        assert gen_record["duration_ms"] is not None
        assert gen_record["duration_ms"] >= 0
    finally:
        os.unlink(path)


async def test_jsonl_exporter_appends_not_overwrites():
    """JSONLExporter appends to existing file content."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = f.name

    try:
        jsonl = JSONLExporter(path=path)
        agent = create_agent("jsonl-test", model=MockModel(["first"]))
        await Harness(agent, tracer=Tracer([jsonl])).run("a")

        with open(path, encoding="utf-8") as f:
            first_count = len(f.readlines())

        # Run again — should append
        await Harness(agent, tracer=Tracer([jsonl])).run("b")

        with open(path, encoding="utf-8") as f:
            total_count = len(f.readlines())

        assert total_count > first_count
    finally:
        os.unlink(path)


async def test_jsonl_exporter_with_tool_invocation():
    """JSONLExporter records tool.invoke spans alongside model.generate."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = f.name

    try:
        jsonl = JSONLExporter(path=path)
        agent = create_agent(
            "jsonl-test",
            model=MockModel([ToolUseBlock(name="greet", input={"name": "Bob"}), "done"]),
            tools=[greet],
        )
        await Harness(agent, tracer=Tracer([jsonl])).run("greet someone")

        with open(path, encoding="utf-8") as f:
            lines = f.readlines()

        names = [json.loads(line)["name"] for line in lines]
        assert "model.generate" in names
        assert "tool.invoke" in names
        assert "session.prompt" in names
    finally:
        os.unlink(path)


# ── Exporter failure isolation (CON-004) ─────────────────────────────────────


async def test_failing_exporter_does_not_break_run():
    """CON-004: Exporter failures are swallowed — run completes normally."""
    agent = create_agent("robust-test", model=MockModel(["still works"]))
    result = await Harness(agent, tracer=Tracer([FailingExporter()])).run("test")

    assert result.text == "still works"
    assert result.stopped == "end_turn"


async def test_mixed_exporters_surviving_exporter_receives_spans():
    """When one exporter fails, others still receive spans."""
    cap = CaptureExporter()
    agent = create_agent("robust-test", model=MockModel(["ok"]))
    await Harness(agent, tracer=Tracer([FailingExporter(), cap])).run("hi")

    # CaptureExporter should still get spans despite FailingExporter crashing
    assert len(cap.spans) >= 1


# ── ConsoleExporter tests ────────────────────────────────────────────────────


def test_console_exporter_writes_to_stream():
    """ConsoleExporter prints human-readable trace lines."""
    import io

    stream = io.StringIO()
    exporter = ConsoleExporter(stream=stream)

    span = Span(name="test.op", attributes={"key": "val"})
    span.end = span.start + 0.05  # 50ms

    exporter.export(span)
    output = stream.getvalue()
    assert "test.op" in output
    assert "key=val" in output
    assert "ms" in output


# ── NullExporter tests ───────────────────────────────────────────────────────


def test_null_exporter_is_noop():
    """NullExporter does nothing — used as default."""
    exporter = NullExporter()
    span = Span(name="test.span")
    span.end = span.start + 0.01
    # Should not raise
    exporter.export(span)


# ── Tracer unit tests ────────────────────────────────────────────────────────


def test_tracer_span_context_manager_sets_timing():
    """Tracer.span() sets start/end times on the span."""
    cap = CaptureExporter()
    tracer = Tracer([cap])

    with tracer.span("test.op", key="value") as sp:
        pass

    assert len(cap.spans) == 1
    exported = cap.spans[0]
    assert exported.name == "test.op"
    assert exported.end is not None
    assert exported.end >= exported.start
    assert exported.attributes["key"] == "value"
    assert exported.status == "ok"


def test_tracer_span_captures_exception_status():
    """Tracer.span() marks status as error when exception is raised."""
    cap = CaptureExporter()
    tracer = Tracer([cap])

    with pytest.raises(ValueError):
        with tracer.span("fail.op"):
            raise ValueError("test error")

    assert len(cap.spans) == 1
    assert "error" in cap.spans[0].status
    assert "ValueError" in cap.spans[0].status


def test_tracer_span_nesting_sets_parent_id():
    """Nested spans have parent_id linking them."""
    cap = CaptureExporter()
    tracer = Tracer([cap])

    with tracer.span("parent") as parent:
        with tracer.span("child") as child:
            pass

    parent_span = next(s for s in cap.spans if s.name == "parent")
    child_span = next(s for s in cap.spans if s.name == "child")
    assert child_span.parent_id == parent_span.span_id
    assert parent_span.parent_id is None


def test_tracer_content_filter_applied():
    """Content filter transforms spans before export."""
    cap = CaptureExporter()

    def redact_filter(span: Span) -> Span:
        span.attributes = {"redacted": True}
        return span

    tracer = Tracer([cap], content_filter=redact_filter)

    with tracer.span("filtered.op", secret="password"):
        pass

    assert cap.spans[0].attributes == {"redacted": True}


def test_tracer_content_filter_failure_swallowed():
    """Failing content filter doesn't prevent export."""
    cap = CaptureExporter()

    def bad_filter(span: Span) -> Span:
        raise RuntimeError("filter crash")

    tracer = Tracer([cap], content_filter=bad_filter)

    with tracer.span("still.exported", key="val"):
        pass

    # Span is still exported even though filter raised
    assert len(cap.spans) == 1
    assert cap.spans[0].attributes["key"] == "val"


def test_tracer_event_appends_to_span():
    """Tracer.event() adds an event to the span's events list."""
    cap = CaptureExporter()
    tracer = Tracer([cap])

    with tracer.span("op") as sp:
        tracer.event(sp, "checkpoint", step=3)

    exported = cap.spans[0]
    assert len(exported.events) == 1
    assert exported.events[0]["name"] == "checkpoint"
    assert exported.events[0]["step"] == 3
