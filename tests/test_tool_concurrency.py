"""Unit tests for tool_concurrency semaphore limiting (Requirement 11.2, 11.3)."""

import asyncio


from tvastar import Harness, create_agent
from tvastar.model import MockModel
from tvastar.tools.base import Tool
from tvastar.types import Message, ToolUseBlock


def _make_tracking_tool(peak_tracker: dict):
    """Create a tool that tracks peak concurrency via a shared dict."""

    async def _fn():
        async with peak_tracker["lock"]:
            peak_tracker["current"] += 1
            if peak_tracker["current"] > peak_tracker["peak"]:
                peak_tracker["peak"] = peak_tracker["current"]
        await asyncio.sleep(0.05)
        async with peak_tracker["lock"]:
            peak_tracker["current"] -= 1
        return "done"

    return Tool(
        name="slow",
        description="slow tool",
        fn=_fn,
        input_schema={"type": "object", "properties": {}},
    )


def _multi_tool_message(n: int) -> Message:
    """Create an assistant Message containing N tool-use requests."""
    blocks = [ToolUseBlock(name="slow", input={}, id=f"tu_{i}") for i in range(n)]
    return Message("assistant", blocks)


async def test_tool_concurrency_none_runs_all_concurrently():
    """When tool_concurrency is None, all tools run concurrently (current behavior)."""
    tracker = {"lock": asyncio.Lock(), "current": 0, "peak": 0}
    tracked_tool = _make_tracking_tool(tracker)

    agent = create_agent(
        "conc-test",
        model=MockModel([_multi_tool_message(5), "done"]),
        instructions="",
        tools=[tracked_tool],
        tool_concurrency=None,
    )
    h = Harness(agent)
    result = await h.run("go")
    assert result.text == "done"
    # All 5 should have run concurrently
    assert tracker["peak"] == 5


async def test_tool_concurrency_limits_parallel_execution():
    """When tool_concurrency=2, at most 2 tools run concurrently."""
    tracker = {"lock": asyncio.Lock(), "current": 0, "peak": 0}
    tracked_tool = _make_tracking_tool(tracker)

    agent = create_agent(
        "conc-limited",
        model=MockModel([_multi_tool_message(5), "done"]),
        instructions="",
        tools=[tracked_tool],
        tool_concurrency=2,
    )
    h = Harness(agent)
    result = await h.run("go")
    assert result.text == "done"
    # Peak concurrency must not exceed 2
    assert tracker["peak"] <= 2
    # And at least 2 ran concurrently (enough tools + sleep to guarantee)
    assert tracker["peak"] == 2


async def test_tool_concurrency_one_serializes_execution():
    """When tool_concurrency=1, tools execute one at a time."""
    tracker = {"lock": asyncio.Lock(), "current": 0, "peak": 0}
    tracked_tool = _make_tracking_tool(tracker)

    agent = create_agent(
        "serial",
        model=MockModel([_multi_tool_message(4), "done"]),
        instructions="",
        tools=[tracked_tool],
        tool_concurrency=1,
    )
    h = Harness(agent)
    result = await h.run("go")
    assert result.text == "done"
    # Only 1 should have been running at a time
    assert tracker["peak"] == 1
