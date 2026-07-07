"""Unit tests for Session agent loop.

Tests cover:
- prompt → model.generate → tool execution → append → repeat cycle
- END_TURN produces RunResult with stopped="end_turn"
- Concurrent tool execution within a single model response
- Message history grows correctly per iteration

Validates: Requirements 1.1, 1.2, 1.3
"""

from __future__ import annotations

import asyncio


from tvastar import Harness, create_agent
from tvastar.model.mock import MockModel
from tvastar.tools.base import tool
from tvastar.types import Message, ToolResultBlock, ToolUseBlock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@tool
def greet(name: str) -> str:
    """Greet someone."""
    return f"Hello, {name}!"


@tool
def add(a: int, b: int) -> str:
    """Add two numbers."""
    return str(int(a) + int(b))


@tool
async def slow_tool(value: str) -> str:
    """A tool that takes a short time (for concurrency testing)."""
    await asyncio.sleep(0.01)
    return f"processed:{value}"


def _make_agent(script, tools=None, max_steps=20):
    """Create a test agent with a scripted MockModel and optional tools."""
    tool_list = tools or [greet, add]
    return create_agent(
        "test-loop",
        model=MockModel(script),
        instructions="You are a test agent.",
        tools=tool_list,
        max_steps=max_steps,
        detect=False,  # disable detectors for focused loop testing
    )


# ---------------------------------------------------------------------------
# Test: prompt → model.generate → tool execution → append → repeat cycle
# ---------------------------------------------------------------------------


async def test_prompt_generate_tool_append_cycle():
    """Test the full loop cycle: prompt → model → tool → append → repeat → END_TURN.

    The model first requests a tool call, the harness executes it, appends the
    result, calls the model again, and the model returns END_TURN.
    """
    script = [
        # Step 1: model requests tool call
        ToolUseBlock(name="greet", input={"name": "World"}),
        # Step 2: model sees tool result, returns final text
        "The greeting is: Hello, World!",
    ]
    agent = _make_agent(script)
    h = Harness(agent)
    result = await h.run("Please greet World")

    # The loop should have done 2 steps: one tool call, one final response
    assert result.steps == 2
    assert result.stopped == "end_turn"
    assert "Hello, World!" in result.text

    # Verify the model was called twice
    assert len(agent.model.calls) == 2


async def test_multi_step_tool_loop():
    """Test multiple tool calls in sequence (one per model response)."""
    script = [
        # Step 1: first tool call
        ToolUseBlock(name="greet", input={"name": "Alice"}),
        # Step 2: second tool call
        ToolUseBlock(name="add", input={"a": 2, "b": 3}),
        # Step 3: final text
        "Alice was greeted and 2+3=5.",
    ]
    agent = _make_agent(script)
    h = Harness(agent)
    result = await h.run("Do both tasks")

    assert result.steps == 3
    assert result.stopped == "end_turn"
    assert len(agent.model.calls) == 3


# ---------------------------------------------------------------------------
# Test: END_TURN produces RunResult with stopped="end_turn"
# ---------------------------------------------------------------------------


async def test_end_turn_produces_stopped_end_turn():
    """When the model returns END_TURN immediately, RunResult.stopped is 'end_turn'."""
    script = ["Simple response with no tools needed."]
    agent = _make_agent(script)
    h = Harness(agent)
    result = await h.run("Hello")

    assert result.stopped == "end_turn"
    assert result.steps == 1
    assert result.text == "Simple response with no tools needed."


async def test_end_turn_after_tool_calls():
    """When the model calls a tool then returns END_TURN, stopped is still 'end_turn'."""
    script = [
        ToolUseBlock(name="add", input={"a": 10, "b": 20}),
        "The answer is 30.",
    ]
    agent = _make_agent(script)
    h = Harness(agent)
    result = await h.run("What is 10+20?")

    assert result.stopped == "end_turn"
    assert result.steps == 2
    assert "30" in result.text


# ---------------------------------------------------------------------------
# Test: Concurrent tool execution within a single model response
# ---------------------------------------------------------------------------


async def test_concurrent_tool_execution():
    """When a model response contains multiple tool calls, they execute concurrently.

    We verify this by checking:
    1. All tool results are present in the message history
    2. The results appear in a single tool-results message (not split across turns)
    """
    # Model returns a message with multiple tool_use blocks in one response
    multi_tool_msg = Message(
        "assistant",
        [
            ToolUseBlock(name="slow_tool", input={"value": "a"}, id="call_aaa"),
            ToolUseBlock(name="slow_tool", input={"value": "b"}, id="call_bbb"),
            ToolUseBlock(name="slow_tool", input={"value": "c"}, id="call_ccc"),
        ],
    )
    script = [
        multi_tool_msg,
        "All three processed.",
    ]
    agent = _make_agent(script, tools=[slow_tool])
    h = Harness(agent)

    sess = h.session()
    async with sess:
        result = await sess.prompt("Process a, b, c concurrently")

    assert result.steps == 2
    assert result.stopped == "end_turn"

    # Find the tool results message in the history
    tool_result_msgs = [
        m for m in sess.messages if m.role == "user" and _has_tool_results(m)
    ]
    assert len(tool_result_msgs) == 1, "All tool results should be in a single message"

    # Verify all three tool results are present
    result_blocks = tool_result_msgs[0].blocks
    tool_results = [b for b in result_blocks if isinstance(b, ToolResultBlock)]
    assert len(tool_results) == 3

    # Verify results contain our processed values
    contents = sorted(r.content for r in tool_results)
    assert contents == ["processed:a", "processed:b", "processed:c"]


async def test_concurrent_execution_is_parallel():
    """Tool calls within a single response actually run concurrently (not sequentially).

    Each slow_tool sleeps 0.01s. If run sequentially, 3 calls take >= 0.03s.
    If concurrent, they should complete in roughly 0.01s (+ overhead).
    """
    multi_tool_msg = Message(
        "assistant",
        [
            ToolUseBlock(name="slow_tool", input={"value": "x"}, id="call_xxx"),
            ToolUseBlock(name="slow_tool", input={"value": "y"}, id="call_yyy"),
            ToolUseBlock(name="slow_tool", input={"value": "z"}, id="call_zzz"),
        ],
    )
    script = [multi_tool_msg, "Done."]
    agent = _make_agent(script, tools=[slow_tool])
    h = Harness(agent)

    import time

    start = time.monotonic()
    result = await h.run("Go")
    elapsed = time.monotonic() - start

    # If sequential: ~0.03s. If concurrent: ~0.01s. Allow generous margin.
    # ponytail: Windows timer resolution is ~15ms; 0.05s still proves concurrency vs 0.03s sequential
    assert elapsed < 0.05, f"Tools should run concurrently, took {elapsed:.3f}s"
    assert result.stopped == "end_turn"


# ---------------------------------------------------------------------------
# Test: Message history grows correctly per iteration
# ---------------------------------------------------------------------------


async def test_message_history_growth_single_tool_step():
    """After one tool-call iteration, history grows by: +1 user prompt, +1 assistant (tool_use), +1 user (tool_results), +1 assistant (final)."""
    script = [
        ToolUseBlock(name="greet", input={"name": "Test"}),
        "Greeting done.",
    ]
    agent = _make_agent(script)
    h = Harness(agent)

    sess = h.session()
    async with sess:
        result = await sess.prompt("Greet Test")

    # Expected messages:
    # 1. user prompt ("Greet Test")
    # 2. assistant message with tool_use block
    # 3. user message with tool_result block
    # 4. assistant message with final text
    assert len(sess.messages) == 4
    assert sess.messages[0].role == "user"
    assert sess.messages[1].role == "assistant"
    assert sess.messages[2].role == "user"  # tool results are appended as user
    assert sess.messages[3].role == "assistant"


async def test_message_history_growth_multiple_tool_steps():
    """Each tool-call iteration adds exactly 2 messages: assistant (tool_use) + user (tool_results).

    For N tool steps + 1 final response:
    - 1 initial user prompt
    - N * (1 assistant + 1 user) for tool iterations
    - 1 final assistant
    Total = 1 + 2*N + 1 = 2*N + 2
    """
    # 3 sequential tool calls, then a final response
    script = [
        ToolUseBlock(name="greet", input={"name": "A"}),
        ToolUseBlock(name="greet", input={"name": "B"}),
        ToolUseBlock(name="greet", input={"name": "C"}),
        "All greeted.",
    ]
    agent = _make_agent(script)
    h = Harness(agent)

    sess = h.session()
    async with sess:
        result = await sess.prompt("Greet everyone")

    # N=3 tool steps: total = 2*3 + 2 = 8 messages
    assert len(sess.messages) == 8
    assert result.steps == 4  # 3 tool steps + 1 final

    # Verify the alternating pattern
    expected_roles = [
        "user",       # initial prompt
        "assistant",  # tool_use 1
        "user",       # tool_result 1
        "assistant",  # tool_use 2
        "user",       # tool_result 2
        "assistant",  # tool_use 3
        "user",       # tool_result 3
        "assistant",  # final text
    ]
    actual_roles = [m.role for m in sess.messages]
    assert actual_roles == expected_roles


async def test_message_history_no_tool_call():
    """When model returns END_TURN immediately, history has exactly 2 messages."""
    script = ["No tools needed."]
    agent = _make_agent(script)
    h = Harness(agent)

    sess = h.session()
    async with sess:
        result = await sess.prompt("Simple question")

    # 1 user prompt + 1 assistant response = 2 messages
    assert len(sess.messages) == 2
    assert sess.messages[0].role == "user"
    assert sess.messages[1].role == "assistant"


async def test_model_receives_accumulated_history():
    """Each model.generate call receives the full accumulated message history."""
    script = [
        ToolUseBlock(name="greet", input={"name": "X"}),
        ToolUseBlock(name="add", input={"a": 1, "b": 2}),
        "Done.",
    ]
    agent = _make_agent(script)
    h = Harness(agent)

    sess = h.session()
    async with sess:
        await sess.prompt("Do stuff")

    model = agent.model
    # First call: just the user prompt
    assert len(model.calls[0]) == 1
    assert model.calls[0][0].role == "user"

    # Second call: user + assistant(tool_use) + user(tool_result)
    assert len(model.calls[1]) == 3
    assert model.calls[1][0].role == "user"
    assert model.calls[1][1].role == "assistant"
    assert model.calls[1][2].role == "user"

    # Third call: user + assistant(tool_use1) + user(result1) + assistant(tool_use2) + user(result2)
    assert len(model.calls[2]) == 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_tool_results(msg: Message) -> bool:
    """Check if a message contains ToolResultBlock(s)."""
    return any(isinstance(b, ToolResultBlock) for b in msg.blocks)
