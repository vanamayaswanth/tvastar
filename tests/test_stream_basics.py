"""Tests for Session.stream() basic behavior.

Verifies Requirement 24: Test coverage for Session.stream().
- 24.1: stream() yields StreamEvent objects for a basic prompt
- 24.2: stream() handles tool calls and yields intermediate events
- 24.3: stream() enforces budget limits
"""

from __future__ import annotations

import pytest

from tvastar.agent import AgentSpec, create_agent
from tvastar.cost import BudgetExceeded, BudgetPolicy, register_model_cost
from tvastar.harness import Harness
from tvastar.model.mock import MockModel
from tvastar.types import (
    ModelResponse,
    StreamEvent,
    StopReason,
    TextBlock,
    ToolUseBlock,
    Usage,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(spec: AgentSpec):
    """Create a session from a Harness so _checkpoint() works."""
    h = Harness(spec)
    return h.session()


class HighUsageModel(MockModel):
    """Model that reports high token usage to trigger budget limits."""

    async def generate(self, messages, **kwargs):
        resp = await super().generate(messages, **kwargs)
        return ModelResponse(
            message=resp.message,
            stop_reason=resp.stop_reason,
            usage=Usage(input_tokens=500_000, output_tokens=500_000),
        )


# ---------------------------------------------------------------------------
# 24.1 — stream() yields StreamEvent objects for a basic prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_yields_stream_events():
    """Every object yielded by stream() must be a StreamEvent instance."""
    model = MockModel(script=["Hello from stream"])
    spec = AgentSpec(name="stream-events-test", model=model)
    session = _make_session(spec)

    events = []
    async with session:
        async for ev in session.stream("hi"):
            events.append(ev)

    assert len(events) > 0
    for ev in events:
        assert isinstance(ev, StreamEvent), f"Expected StreamEvent, got {type(ev)}"


@pytest.mark.asyncio
async def test_stream_yields_turn_start_text_turn_end():
    """A basic prompt produces turn_start, at least one text_delta, and turn_end."""
    model = MockModel(script=["Hello world"])
    spec = AgentSpec(name="stream-sequence-test", model=model)
    session = _make_session(spec)

    events = []
    async with session:
        async for ev in session.stream("say hello"):
            events.append(ev)

    types = [e.type for e in events]
    assert types[0] == "turn_start", "First event should be turn_start"
    assert "text_delta" in types, "Should include at least one text_delta event"
    assert types[-1] == "turn_end", "Last event should be turn_end"


@pytest.mark.asyncio
async def test_stream_turn_start_contains_step():
    """turn_start event should include the step number."""
    model = MockModel(script=["Hi"])
    spec = AgentSpec(name="stream-step-test", model=model)
    session = _make_session(spec)

    events = []
    async with session:
        async for ev in session.stream("hello"):
            events.append(ev)

    turn_starts = [e for e in events if e.type == "turn_start"]
    assert len(turn_starts) == 1
    assert turn_starts[0].data["step"] == 1


@pytest.mark.asyncio
async def test_stream_text_delta_contains_text():
    """text_delta events should carry the text content."""
    model = MockModel(script=["The answer is 42"])
    spec = AgentSpec(name="stream-text-test", model=model)
    session = _make_session(spec)

    events = []
    async with session:
        async for ev in session.stream("what is the answer"):
            events.append(ev)

    text_deltas = [e for e in events if e.type == "text_delta"]
    assert len(text_deltas) >= 1
    combined_text = "".join(e.data["text"] for e in text_deltas)
    assert "The answer is 42" in combined_text


@pytest.mark.asyncio
async def test_stream_turn_end_contains_text():
    """turn_end event should contain the final assistant text."""
    model = MockModel(script=["Final answer"])
    spec = AgentSpec(name="stream-turnend-test", model=model)
    session = _make_session(spec)

    events = []
    async with session:
        async for ev in session.stream("test"):
            events.append(ev)

    turn_ends = [e for e in events if e.type == "turn_end"]
    assert len(turn_ends) == 1
    assert "Final answer" in turn_ends[0].data.get("text", "")


# ---------------------------------------------------------------------------
# 24.2 — stream() handles tool calls and yields intermediate events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_tool_call_yields_tool_call_event():
    """When the model requests a tool call, stream() yields a tool_call event."""
    from tvastar.tools.base import tool as tool_decorator

    @tool_decorator
    async def greet(name: str) -> str:
        """Greet someone."""
        return f"Hello, {name}!"

    tool_use = ToolUseBlock(id="tc1", name="greet", input={"name": "Alice"})
    model = MockModel(script=[tool_use, "Done greeting"])

    spec = create_agent(
        "stream-toolcall-test",
        model=model,
        tools=[greet],
        detect=False,
    )
    h = Harness(spec)
    session = h.session()

    events = []
    async with session:
        async for ev in session.stream("greet Alice"):
            events.append(ev)

    tool_calls = [e for e in events if e.type == "tool_call"]
    assert len(tool_calls) == 1
    assert tool_calls[0].data["name"] == "greet"
    assert tool_calls[0].data["input"] == {"name": "Alice"}


@pytest.mark.asyncio
async def test_stream_tool_call_yields_tool_result_event():
    """After executing a tool, stream() yields a tool_result event with the output."""
    from tvastar.tools.base import tool as tool_decorator

    @tool_decorator
    async def add(a: int, b: int) -> str:
        """Add two numbers."""
        return str(a + b)

    tool_use = ToolUseBlock(id="tc2", name="add", input={"a": 3, "b": 4})
    model = MockModel(script=[tool_use, "The sum is 7"])

    spec = create_agent(
        "stream-toolresult-test",
        model=model,
        tools=[add],
        detect=False,
    )
    h = Harness(spec)
    session = h.session()

    events = []
    async with session:
        async for ev in session.stream("add 3 + 4"):
            events.append(ev)

    tool_results = [e for e in events if e.type == "tool_result"]
    assert len(tool_results) == 1
    assert tool_results[0].data["content"] == "7"
    assert tool_results[0].data["error"] is False


@pytest.mark.asyncio
async def test_stream_multiple_tool_calls_yield_multiple_events():
    """Multiple tool calls in one step yield multiple tool_call and tool_result events."""
    from tvastar.tools.base import tool as tool_decorator

    @tool_decorator
    async def echo(text: str) -> str:
        """Echo text."""
        return text

    tool_use_1 = ToolUseBlock(id="tc3", name="echo", input={"text": "first"})
    tool_use_2 = ToolUseBlock(id="tc4", name="echo", input={"text": "second"})
    # MockModel wraps each ToolUseBlock as a separate response, so use a
    # Message with two tool uses for a multi-tool step.
    from tvastar.types import Message

    multi_tool_msg = Message("assistant", [tool_use_1, tool_use_2])
    model = MockModel(script=[multi_tool_msg, "Both done"])

    spec = create_agent(
        "stream-multitools-test",
        model=model,
        tools=[echo],
        detect=False,
    )
    h = Harness(spec)
    session = h.session()

    events = []
    async with session:
        async for ev in session.stream("echo both"):
            events.append(ev)

    tool_calls = [e for e in events if e.type == "tool_call"]
    tool_results = [e for e in events if e.type == "tool_result"]
    assert len(tool_calls) == 2
    assert len(tool_results) == 2
    assert tool_calls[0].data["name"] == "echo"
    assert tool_calls[1].data["name"] == "echo"


@pytest.mark.asyncio
async def test_stream_tool_call_followed_by_model_response():
    """After a tool call, the model should get another turn, yielding more events."""
    from tvastar.tools.base import tool as tool_decorator

    @tool_decorator
    async def lookup(key: str) -> str:
        """Lookup a value."""
        return f"value_for_{key}"

    tool_use = ToolUseBlock(id="tc5", name="lookup", input={"key": "foo"})
    model = MockModel(script=[tool_use, "Found: value_for_foo"])

    spec = create_agent(
        "stream-toolchain-test",
        model=model,
        tools=[lookup],
        detect=False,
    )
    h = Harness(spec)
    session = h.session()

    events = []
    async with session:
        async for ev in session.stream("lookup foo"):
            events.append(ev)

    types = [e.type for e in events]
    # Expect: turn_start, tool_call, tool_result, turn_start (2nd turn), text_delta, turn_end
    assert types.count("turn_start") == 2, "Should have two turns (tool call + final response)"
    assert "tool_call" in types
    assert "tool_result" in types
    assert "turn_end" in types


# ---------------------------------------------------------------------------
# 24.3 — stream() enforces budget limits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_budget_stop():
    """stream() stops gracefully when budget is exceeded (on_exceed='stop')."""
    register_model_cost("mock", input_per_million=10.0, output_per_million=10.0)

    model = HighUsageModel(script=["Hello", "World"])
    budget = BudgetPolicy(max_usd=0.001, on_exceed="stop")
    spec = AgentSpec(
        name="stream-budget-stop-test",
        model=model,
        budget=budget,
    )
    session = _make_session(spec)

    events = []
    async with session:
        async for ev in session.stream("test"):
            events.append(ev)

    turn_ends = [e for e in events if e.type == "turn_end"]
    assert len(turn_ends) == 1
    assert turn_ends[0].data.get("stopped") == "budget"


@pytest.mark.asyncio
async def test_stream_budget_raise():
    """stream() raises BudgetExceeded when budget exceeded (on_exceed='raise')."""
    register_model_cost("mock", input_per_million=10.0, output_per_million=10.0)

    model = HighUsageModel(script=["Hello"])
    budget = BudgetPolicy(max_usd=0.001, on_exceed="raise")
    spec = AgentSpec(
        name="stream-budget-raise-test",
        model=model,
        budget=budget,
    )
    session = _make_session(spec)

    with pytest.raises(BudgetExceeded):
        async with session:
            async for ev in session.stream("test"):
                pass


@pytest.mark.asyncio
async def test_stream_budget_no_exceed_continues():
    """stream() runs to completion when budget is not exceeded."""
    register_model_cost("mock", input_per_million=0.001, output_per_million=0.001)

    model = MockModel(script=["All good"])
    budget = BudgetPolicy(max_usd=100.0, on_exceed="stop")
    spec = AgentSpec(
        name="stream-budget-ok-test",
        model=model,
        budget=budget,
    )
    session = _make_session(spec)

    events = []
    async with session:
        async for ev in session.stream("test"):
            events.append(ev)

    turn_ends = [e for e in events if e.type == "turn_end"]
    assert len(turn_ends) == 1
    # Should not have stopped due to budget
    assert turn_ends[0].data.get("stopped") is None or turn_ends[0].data.get("stopped") != "budget"
