"""Unit tests for tool_order_fn (Requirements 18.2, 18.3)."""

import pytest

from tvastar import Harness, create_agent
from tvastar.model import MockModel
from tvastar.tools.base import Tool
from tvastar.types import Message, ToolUseBlock


_EMPTY_SCHEMA = {"type": "object", "properties": {}}


def _make_tracking_tools(execution_order: list):
    """Create a, b, c tracking tools that record execution order."""

    async def fn_a():
        execution_order.append("a")
        return "a"

    async def fn_b():
        execution_order.append("b")
        return "b"

    async def fn_c():
        execution_order.append("c")
        return "c"

    return [
        Tool(name="tool_a", description="A", fn=fn_a, input_schema=_EMPTY_SCHEMA),
        Tool(name="tool_b", description="B", fn=fn_b, input_schema=_EMPTY_SCHEMA),
        Tool(name="tool_c", description="C", fn=fn_c, input_schema=_EMPTY_SCHEMA),
    ]


def _multi_tool_message(names: list[str]) -> Message:
    """Create an assistant Message containing tool-use requests for the given names."""
    blocks = [ToolUseBlock(name=n, input={}, id=f"tu_{n}") for n in names]
    return Message("assistant", blocks)


async def test_tool_order_fn_not_configured_uses_model_order():
    """When tool_order_fn is None, tools execute in model-returned order."""
    execution_order = []
    tools = _make_tracking_tools(execution_order)

    agent = create_agent(
        "order-test",
        model=MockModel([_multi_tool_message(["tool_a", "tool_b", "tool_c"]), "done"]),
        instructions="",
        tools=tools,
        tool_order_fn=None,
        tool_concurrency=1,  # serialize so order is deterministic
    )
    h = Harness(agent)
    result = await h.run("go")
    assert result.text == "done"
    # Original model order: a, b, c
    assert execution_order == ["a", "b", "c"]


async def test_tool_order_fn_reverses_order():
    """When tool_order_fn reverses the list, execution uses reversed order."""
    execution_order = []
    tools = _make_tracking_tools(execution_order)

    def reverse_order(uses):
        return list(reversed(uses))

    agent = create_agent(
        "order-reverse",
        model=MockModel([_multi_tool_message(["tool_a", "tool_b", "tool_c"]), "done"]),
        instructions="",
        tools=tools,
        tool_order_fn=reverse_order,
        tool_concurrency=1,
    )
    h = Harness(agent)
    result = await h.run("go")
    assert result.text == "done"
    # With concurrency=1 and reversed order, execution should be c, b, a
    assert execution_order == ["c", "b", "a"]


async def test_tool_order_fn_custom_priority():
    """tool_order_fn can implement custom priority-based ordering."""
    execution_order = []
    tools = _make_tracking_tools(execution_order)

    # Custom ordering: b first, then c, then a
    priority = {"tool_b": 0, "tool_c": 1, "tool_a": 2}

    def priority_order(uses):
        return sorted(uses, key=lambda u: priority.get(u.name, 99))

    agent = create_agent(
        "order-priority",
        model=MockModel([_multi_tool_message(["tool_a", "tool_b", "tool_c"]), "done"]),
        instructions="",
        tools=tools,
        tool_order_fn=priority_order,
        tool_concurrency=1,
    )
    h = Harness(agent)
    result = await h.run("go")
    assert result.text == "done"
    assert execution_order == ["b", "c", "a"]


async def test_tool_order_fn_exception_falls_back_to_original_order():
    """When tool_order_fn raises, fall back to model-returned order with a warning."""
    execution_order = []
    tools = _make_tracking_tools(execution_order)

    def broken_order(uses):
        raise ValueError("ordering exploded")

    agent = create_agent(
        "order-broken",
        model=MockModel([_multi_tool_message(["tool_a", "tool_b"]), "done"]),
        instructions="",
        tools=tools,
        tool_order_fn=broken_order,
        tool_concurrency=1,
    )
    h = Harness(agent)
    with pytest.warns(UserWarning, match="tool_order_fn raised"):
        result = await h.run("go")
    assert result.text == "done"
    # Should fall back to original model order: a, b
    assert execution_order == ["a", "b"]
