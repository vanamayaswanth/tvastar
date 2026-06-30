"""Property-based tests for agent loop execution.

Property 2: Tool execution grows message history
- For any ModelResponse with TOOL_USE containing T tool calls, Session appends
  one assistant message and one tool-results message per loop iteration.
- After N tool steps + 1 final END_TURN response, message history should have
  exactly 2*N + 2 messages (1 user prompt + N*(assistant+tool_result) + 1 final assistant).

**Validates: Requirements 1.2**
"""

from __future__ import annotations

import hypothesis.strategies as st
from hypothesis import given, settings

from tvastar import Harness, create_agent
from tvastar.model.mock import MockModel
from tvastar.tools.base import tool
from tvastar.types import ToolUseBlock


# ---------------------------------------------------------------------------
# Dummy tool — always returns a fixed string
# ---------------------------------------------------------------------------


@tool
def dummy_tool(value: str) -> str:
    """A dummy tool that always returns a string."""
    return f"result:{value}"


# ---------------------------------------------------------------------------
# Property 2: Tool execution grows message history
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(n=st.integers(min_value=1, max_value=10))
async def test_tool_execution_message_growth(n: int):
    """Property 2: Tool execution grows message history.

    For N tool-call steps followed by a final END_TURN text response,
    the message history should contain exactly 2*N + 2 messages:
      - 1 user prompt
      - N * (1 assistant with tool_use + 1 user with tool_result)
      - 1 final assistant with text

    Also verifies the alternating role pattern: user, assistant, user, assistant, ...

    **Validates: Requirements 1.2**
    """
    # Build a scripted sequence: N ToolUseBlocks followed by a text response
    script: list = [
        ToolUseBlock(name="dummy_tool", input={"value": f"step_{i}"})
        for i in range(n)
    ]
    script.append("Final response after tool calls.")

    agent = create_agent(
        "test-message-growth",
        model=MockModel(script),
        instructions="Test agent",
        tools=[dummy_tool],
        max_steps=n + 5,  # ensure we don't hit max_steps
        detect=False,
    )
    h = Harness(agent)

    sess = h.session()
    async with sess:
        result = await sess.prompt("Run tools")

    # Verify message count: 2*N + 2
    expected_count = 2 * n + 2
    assert len(sess.messages) == expected_count, (
        f"Expected {expected_count} messages for N={n} tool steps, "
        f"got {len(sess.messages)}"
    )

    # Verify alternating role pattern: user, assistant, user, assistant, ...
    expected_roles = ["user"]  # initial user prompt
    for _ in range(n):
        expected_roles.append("assistant")  # assistant with tool_use
        expected_roles.append("user")       # user with tool_result
    expected_roles.append("assistant")      # final assistant text

    actual_roles = [m.role for m in sess.messages]
    assert actual_roles == expected_roles, (
        f"Role pattern mismatch for N={n}.\n"
        f"Expected: {expected_roles}\n"
        f"Actual:   {actual_roles}"
    )

    # Verify the run completed normally
    assert result.stopped == "end_turn"
    assert result.steps == n + 1  # N tool steps + 1 final
