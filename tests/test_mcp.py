"""MCP integration tests — driven against the real stdio server in
examples/mcp_echo_server.py (no mocks, no network)."""

import sys
from pathlib import Path

import pytest

from tvastar import Harness, create_agent
from tvastar.mcp import connect_mcp_server
from tvastar.model import MockModel
from tvastar.types import ToolUseBlock

SERVER = str(Path(__file__).parent.parent / "examples" / "mcp_echo_server.py")


async def _client():
    return await connect_mcp_server(command=sys.executable, args=[SERVER])


async def test_handshake_and_tool_discovery():
    client = await _client()
    try:
        assert client.server_info.get("name") == "tvastar-echo-mcp"
        assert set(client.tool_names()) == {"add", "upper", "weather"}
        # schema came from the server, not Python introspection
        add = next(t for t in client.tools if t.name == "add")
        assert add.input_schema["required"] == ["a", "b"]
    finally:
        await client.close()


async def test_call_tool_directly():
    client = await _client()
    try:
        assert await client.call_tool("add", {"a": 2, "b": 3}) == "5"
        assert await client.call_tool("upper", {"text": "hi"}) == "HI"
    finally:
        await client.close()


async def test_mcp_tools_drive_an_agent():
    client = await _client()
    try:
        # The model decides to call the MCP-provided `weather` tool.
        script = [
            ToolUseBlock(name="weather", input={"city": "Paris"}),
            "The weather in Paris is clear and mild.",
        ]
        agent = create_agent(
            "mcp-agent",
            model=MockModel(script),
            instructions="Use tools to answer.",
            tools=client.tools,  # MCP tools used exactly like native ones
        )
        result = await Harness(agent).run("What's the weather in Paris?")
        assert "Paris" in result.text
        # the MCP tool result flowed back through the loop (lives in tool_result blocks)
        from tvastar.types import ToolResultBlock

        tool_outputs = [
            b.content for m in result.messages for b in m.blocks if isinstance(b, ToolResultBlock)
        ]
        assert any("22" in o for o in tool_outputs)
    finally:
        await client.close()


async def test_tool_error_surfaces():
    client = await _client()
    try:
        out = await client.call_tool("nonexistent", {})
        assert "unknown tool" in out
    finally:
        await client.close()


async def test_connect_requires_command_or_url():
    from tvastar.mcp import MCPError

    with pytest.raises(MCPError):
        await connect_mcp_server()
