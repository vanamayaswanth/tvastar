"""Unit tests for pre_tool_hook functionality.

Validates: Requirements 12.2, 12.3, 12.4, 12.5
"""

import warnings

import pytest

from tvastar import Harness, create_agent, default_toolset
from tvastar.model import MockModel
from tvastar.types import ToolUseBlock


async def test_pre_tool_hook_called_with_name_and_args():
    """Req 12.2: pre_tool_hook is invoked before each tool execution with tool name and args."""
    calls = []

    def hook(name: str, args: dict):
        calls.append((name, dict(args)))
        return None  # don't modify

    script = [
        ToolUseBlock(name="write_file", input={"path": "a.txt", "content": "hello"}),
        "Done.",
    ]
    agent = create_agent(
        "test",
        model=MockModel(script),
        instructions="go",
        tools=default_toolset(),
        pre_tool_hook=hook,
    )
    h = Harness(agent)
    r = await h.run("do it")
    assert r.stopped == "end_turn"
    assert len(calls) == 1
    assert calls[0][0] == "write_file"
    assert calls[0][1] == {"path": "a.txt", "content": "hello"}


async def test_pre_tool_hook_modifies_args():
    """Req 12.3: When pre_tool_hook returns a dict, the tool receives modified args."""
    def hook(name: str, args: dict):
        if name == "write_file":
            return {"path": "modified.txt", "content": "modified_content"}
        return None

    script = [
        ToolUseBlock(name="write_file", input={"path": "original.txt", "content": "original"}),
        "Done.",
    ]
    agent = create_agent(
        "test",
        model=MockModel(script),
        instructions="go",
        tools=default_toolset(),
        pre_tool_hook=hook,
    )
    h = Harness(agent)
    sess = h.session()
    async with sess:
        r = await sess.prompt("write")
        # The hook modified the path to "modified.txt"
        assert sess.sandbox.fs.read("modified.txt") == "modified_content"
        # Original file should NOT exist
        with pytest.raises(Exception):
            sess.sandbox.fs.read("original.txt")


async def test_pre_tool_hook_returns_none_uses_original():
    """Req 12.4: When pre_tool_hook returns None, original args are used."""
    def hook(name: str, args: dict):
        return None  # explicitly return None

    script = [
        ToolUseBlock(name="write_file", input={"path": "original.txt", "content": "data"}),
        "Done.",
    ]
    agent = create_agent(
        "test",
        model=MockModel(script),
        instructions="go",
        tools=default_toolset(),
        pre_tool_hook=hook,
    )
    h = Harness(agent)
    sess = h.session()
    async with sess:
        r = await sess.prompt("write")
        assert sess.sandbox.fs.read("original.txt") == "data"


async def test_pre_tool_hook_exception_does_not_break_run():
    """Req 12.5: If pre_tool_hook raises, Session logs a warning and uses original args."""
    def broken_hook(name: str, args: dict):
        raise ValueError("hook exploded")

    script = [
        ToolUseBlock(name="write_file", input={"path": "a.txt", "content": "data"}),
        "Done.",
    ]
    agent = create_agent(
        "test",
        model=MockModel(script),
        instructions="go",
        tools=default_toolset(),
        pre_tool_hook=broken_hook,
    )
    h = Harness(agent)
    sess = h.session()
    async with sess:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            r = await sess.prompt("do it")
            # Should have warned
            hook_warnings = [x for x in w if "pre_tool_hook" in str(x.message)]
            assert len(hook_warnings) >= 1
        # Run should complete successfully with original args
        assert r.stopped == "end_turn"
        assert sess.sandbox.fs.read("a.txt") == "data"


async def test_pre_tool_hook_called_for_each_tool_in_step():
    """Req 12.2: Hook is called before EACH tool execution, not just once per step."""
    from tvastar.types import Message

    calls = []

    def hook(name: str, args: dict):
        calls.append(name)
        return None

    multi_tool_msg = Message(
        "assistant",
        [
            ToolUseBlock(name="write_file", input={"path": "a.txt", "content": "aa"}, id="call_1"),
            ToolUseBlock(name="write_file", input={"path": "b.txt", "content": "bb"}, id="call_2"),
        ],
    )
    script = [
        multi_tool_msg,
        "Done.",
    ]
    agent = create_agent(
        "test",
        model=MockModel(script),
        instructions="go",
        tools=default_toolset(),
        pre_tool_hook=hook,
    )
    h = Harness(agent)
    r = await h.run("write both")
    assert r.stopped == "end_turn"
    assert len(calls) == 2
    assert all(c == "write_file" for c in calls)
