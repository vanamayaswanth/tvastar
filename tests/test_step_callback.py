"""Tests for step_callback in the Session loop (Requirement 14)."""

import warnings

import pytest

from tvastar import Harness, create_agent, default_toolset
from tvastar.model import MockModel
from tvastar.types import ToolUseBlock


async def test_step_callback_invoked_each_step():
    """step_callback is called after each model generate, receiving step number, response, and messages."""
    calls: list[tuple] = []

    def callback(step: int, response, messages: list):
        calls.append((step, response, list(messages)))

    script = [
        ToolUseBlock(name="list_files", input={}),
        "Done.",
    ]
    agent = create_agent(
        "cb-test",
        model=MockModel(script),
        instructions="test",
        tools=default_toolset(),
        step_callback=callback,
        detect=False,
    )
    h = Harness(agent)
    r = await h.run("go")
    assert r.text == "Done."
    # Two steps: one tool use + one final text
    assert len(calls) == 2
    assert calls[0][0] == 1  # step 1
    assert calls[1][0] == 2  # step 2


async def test_step_callback_receives_model_response():
    """step_callback receives the actual ModelResponse object from the generate call."""
    responses_received = []

    def callback(step: int, response, messages: list):
        responses_received.append(response)

    agent = create_agent(
        "cb-resp",
        model=MockModel(["Hello world"]),
        instructions="test",
        step_callback=callback,
        detect=False,
    )
    h = Harness(agent)
    r = await h.run("hi")
    assert len(responses_received) == 1
    # ModelResponse should have a text property or message containing "Hello world"
    resp = responses_received[0]
    assert hasattr(resp, "message") or hasattr(resp, "text")


async def test_step_callback_receives_current_messages():
    """step_callback receives the current messages list (including the just-appended assistant message)."""
    messages_snapshots = []

    def callback(step: int, response, messages: list):
        messages_snapshots.append(list(messages))

    agent = create_agent(
        "cb-msgs",
        model=MockModel(["response text"]),
        instructions="test",
        step_callback=callback,
        detect=False,
    )
    h = Harness(agent)
    r = await h.run("hello")
    assert len(messages_snapshots) == 1
    # Messages should contain at least the user message and the assistant response
    msgs = messages_snapshots[0]
    assert len(msgs) >= 2  # user + assistant at minimum


async def test_step_callback_exception_does_not_break_loop():
    """If step_callback raises, the loop continues and produces a valid result."""

    def bad_callback(step: int, response, messages: list):
        raise RuntimeError("callback exploded")

    agent = create_agent(
        "cb-crash",
        model=MockModel(["still works"]),
        instructions="test",
        step_callback=bad_callback,
        detect=False,
    )
    h = Harness(agent)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        r = await h.run("hi")
    assert r.text == "still works"
    assert r.stopped == "end_turn"
    # A warning was issued
    assert any("step_callback raised" in str(warning.message) for warning in w)


async def test_step_callback_not_called_when_none():
    """When step_callback is None (default), no error occurs and the loop runs normally."""
    agent = create_agent(
        "cb-none",
        model=MockModel(["fine"]),
        instructions="test",
        detect=False,
    )
    h = Harness(agent)
    r = await h.run("hi")
    assert r.text == "fine"


async def test_step_callback_called_before_tool_execution():
    """step_callback is invoked before tools execute — the messages at that point
    should NOT yet contain the tool result."""
    call_messages: list = []

    def callback(step: int, response, messages: list):
        call_messages.append(list(messages))

    script = [
        ToolUseBlock(name="list_files", input={}),
        "Done listing.",
    ]
    agent = create_agent(
        "cb-order",
        model=MockModel(script),
        instructions="test",
        tools=default_toolset(),
        step_callback=callback,
        detect=False,
    )
    h = Harness(agent)
    r = await h.run("list")
    # First callback (step 1 = tool use): messages should have user + assistant but no tool result yet
    step1_msgs = call_messages[0]
    # The last message should be the assistant (tool use), not a user (tool result)
    assert step1_msgs[-1].role == "assistant"
