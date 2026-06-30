"""Integration tests for MCPClient — validates tool discovery, invocation,
lifecycle management, and coexistence with native tools.

Driven against the real echo MCP server in examples/mcp_echo_server.py (no mocks,
no network). HTTP transport tests mock the network layer since we don't want to
depend on an external HTTP server.

Requirements: 20.1, 20.2, 20.3, 20.4, 20.5, 20.6
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tvastar import Harness, create_agent
from tvastar.mcp import (
    MCPClient,
    MCPError,
    StdioTransport,
    StreamableHttpTransport,
    connect_mcp_server,
)
from tvastar.model import MockModel
from tvastar.tools.base import Tool, tool as tool_decorator
from tvastar.types import ToolResultBlock, ToolUseBlock

SERVER = str(Path(__file__).parent.parent / "examples" / "mcp_echo_server.py")


# ────────────────────────────────────────────────────────────────────────────────
# Requirement 20.1: connect_mcp_server(command=...) starts subprocess and discovers tools
# ────────────────────────────────────────────────────────────────────────────────


class TestStdioConnection:
    """Validate that connect_mcp_server(command=...) starts a subprocess and
    performs MCP handshake to discover available tools."""

    async def test_connect_starts_subprocess_and_initializes(self):
        """connect_mcp_server(command=...) launches a subprocess and performs
        the initialize handshake."""
        client = await connect_mcp_server(command=sys.executable, args=[SERVER])
        try:
            # The subprocess was started and handshake completed
            assert client._connected is True
            assert client.server_info.get("name") == "tvastar-echo-mcp"
            assert client.server_info.get("version") == "1.0.0"
        finally:
            await client.close()

    async def test_stdio_transport_process_is_alive(self):
        """The underlying subprocess is running after connect."""
        client = await connect_mcp_server(command=sys.executable, args=[SERVER])
        try:
            transport = client.transport
            assert isinstance(transport, StdioTransport)
            assert transport._proc is not None
            assert transport._proc.returncode is None  # still running
        finally:
            await client.close()

    async def test_discovers_all_server_tools(self):
        """All tools declared by the MCP server are discovered during connect."""
        client = await connect_mcp_server(command=sys.executable, args=[SERVER])
        try:
            names = client.tool_names()
            assert "add" in names
            assert "upper" in names
            assert "weather" in names
            assert len(names) == 3
        finally:
            await client.close()

    async def test_connect_with_custom_args(self):
        """connect_mcp_server passes additional args to the subprocess."""
        # sys.executable + [SERVER] is effectively command=python args=[server.py]
        client = await connect_mcp_server(command=sys.executable, args=[SERVER])
        try:
            assert len(client.tools) == 3
        finally:
            await client.close()


# ────────────────────────────────────────────────────────────────────────────────
# Requirement 20.2: connect_mcp_server(url=...) connects via HTTP
# ────────────────────────────────────────────────────────────────────────────────


class TestHttpConnection:
    """Validate that connect_mcp_server(url=...) creates an HTTP transport and
    performs MCP handshake."""

    async def test_connect_via_url_creates_http_transport(self):
        """connect_mcp_server(url=...) creates a StreamableHttpTransport."""
        # We mock the HTTP layer to avoid needing a real HTTP MCP server
        with patch.object(StreamableHttpTransport, "_post") as mock_post:
            # First call: initialize request
            mock_post.side_effect = [
                # initialize response
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "http-mcp-server", "version": "1.0.0"},
                    },
                },
                # notifications/initialized (no response expected)
                None,
                # tools/list response
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "tools": [
                            {
                                "name": "search",
                                "description": "Search the web",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"query": {"type": "string"}},
                                    "required": ["query"],
                                },
                            }
                        ]
                    },
                },
            ]

            client = await connect_mcp_server(
                url="https://example.com/mcp",
                headers={"Authorization": "Bearer test-token"},
            )
            try:
                assert isinstance(client.transport, StreamableHttpTransport)
                assert client._connected is True
                assert client.server_info.get("name") == "http-mcp-server"
                assert len(client.tools) == 1
                assert client.tools[0].name == "search"
            finally:
                await client.close()

    async def test_http_transport_passes_headers(self):
        """Custom headers (e.g., Authorization) are passed through to HTTP transport."""
        client = MCPClient.http(
            "https://example.com/mcp",
            headers={"Authorization": "Bearer secret"},
            timeout=15.0,
        )
        transport = client.transport
        assert isinstance(transport, StreamableHttpTransport)
        assert transport.headers == {"Authorization": "Bearer secret"}
        assert transport.timeout == 15.0

    async def test_connect_requires_command_or_url(self):
        """connect_mcp_server raises MCPError when neither command nor url given."""
        with pytest.raises(MCPError, match="requires either"):
            await connect_mcp_server()


# ────────────────────────────────────────────────────────────────────────────────
# Requirement 20.3: MCPClient.tools returns Tool objects with correct attributes
# ────────────────────────────────────────────────────────────────────────────────


class TestToolDiscovery:
    """Validate that MCPClient.tools returns proper Tool objects with name,
    description, and input_schema from the MCP server."""

    async def test_tools_are_tool_instances(self):
        """Each tool from MCPClient.tools is a tvastar Tool instance."""
        client = await connect_mcp_server(command=sys.executable, args=[SERVER])
        try:
            for t in client.tools:
                assert isinstance(t, Tool)
        finally:
            await client.close()

    async def test_tool_name_matches_server_declaration(self):
        """Tool.name matches the name declared by the MCP server."""
        client = await connect_mcp_server(command=sys.executable, args=[SERVER])
        try:
            add_tool = next(t for t in client.tools if t.name == "add")
            assert add_tool.name == "add"
            upper_tool = next(t for t in client.tools if t.name == "upper")
            assert upper_tool.name == "upper"
        finally:
            await client.close()

    async def test_tool_description_from_server(self):
        """Tool.description comes from the server's tool declaration."""
        client = await connect_mcp_server(command=sys.executable, args=[SERVER])
        try:
            add_tool = next(t for t in client.tools if t.name == "add")
            assert add_tool.description == "Add two numbers and return the sum."
            upper_tool = next(t for t in client.tools if t.name == "upper")
            assert upper_tool.description == "Uppercase a string."
        finally:
            await client.close()

    async def test_tool_input_schema_from_server(self):
        """Tool.input_schema reflects the server's inputSchema for each tool."""
        client = await connect_mcp_server(command=sys.executable, args=[SERVER])
        try:
            add_tool = next(t for t in client.tools if t.name == "add")
            assert add_tool.input_schema["type"] == "object"
            assert "a" in add_tool.input_schema["properties"]
            assert "b" in add_tool.input_schema["properties"]
            assert add_tool.input_schema["required"] == ["a", "b"]

            weather_tool = next(t for t in client.tools if t.name == "weather")
            assert weather_tool.input_schema["required"] == ["city"]
        finally:
            await client.close()

    async def test_tool_spec_property(self):
        """Tool.spec returns a ToolSpec suitable for model.generate."""
        client = await connect_mcp_server(command=sys.executable, args=[SERVER])
        try:
            add_tool = next(t for t in client.tools if t.name == "add")
            spec = add_tool.spec
            assert spec.name == "add"
            assert spec.description == "Add two numbers and return the sum."
            assert spec.input_schema["required"] == ["a", "b"]
        finally:
            await client.close()


# ────────────────────────────────────────────────────────────────────────────────
# Requirement 20.4: Model invokes MCP tool and receives result
# ────────────────────────────────────────────────────────────────────────────────


class TestToolInvocation:
    """Validate that the agent loop can invoke MCP tools and receive results."""

    async def test_direct_tool_call_add(self):
        """call_tool invokes the MCP server's add tool and returns the result."""
        client = await connect_mcp_server(command=sys.executable, args=[SERVER])
        try:
            result = await client.call_tool("add", {"a": 10, "b": 32})
            assert result == "42"
        finally:
            await client.close()

    async def test_direct_tool_call_upper(self):
        """call_tool invokes the MCP server's upper tool."""
        client = await connect_mcp_server(command=sys.executable, args=[SERVER])
        try:
            result = await client.call_tool("upper", {"text": "hello world"})
            assert result == "HELLO WORLD"
        finally:
            await client.close()

    async def test_model_invokes_mcp_tool_in_agent_loop(self):
        """An agent using MCP tools can invoke them through the standard loop."""
        client = await connect_mcp_server(command=sys.executable, args=[SERVER])
        try:
            # Model decides to call the 'add' MCP tool
            script = [
                ToolUseBlock(name="add", input={"a": 5, "b": 7}),
                "The sum of 5 and 7 is 12.",
            ]
            agent = create_agent(
                "math-agent",
                model=MockModel(script),
                instructions="Use tools to compute.",
                tools=client.tools,
            )
            result = await Harness(agent).run("What is 5 + 7?")
            assert "12" in result.text

            # Verify the tool result flowed back through the loop
            tool_outputs = [
                b.content
                for m in result.messages
                for b in m.blocks
                if isinstance(b, ToolResultBlock)
            ]
            assert any("12" in o for o in tool_outputs)
        finally:
            await client.close()

    async def test_model_invokes_multiple_mcp_tools(self):
        """An agent can invoke multiple MCP tools in a single run."""
        client = await connect_mcp_server(command=sys.executable, args=[SERVER])
        try:
            script = [
                ToolUseBlock(name="add", input={"a": 3, "b": 4}),
                ToolUseBlock(name="upper", input={"text": "result"}),
                "The sum is 7 and RESULT in uppercase.",
            ]
            agent = create_agent(
                "multi-tool-agent",
                model=MockModel(script),
                instructions="Use tools.",
                tools=client.tools,
            )
            result = await Harness(agent).run("Compute and transform.")

            tool_outputs = [
                b.content
                for m in result.messages
                for b in m.blocks
                if isinstance(b, ToolResultBlock)
            ]
            assert any("7" in o for o in tool_outputs)
            assert any("RESULT" in o for o in tool_outputs)
        finally:
            await client.close()

    async def test_tool_error_returns_error_result(self):
        """Calling a nonexistent MCP tool returns an error indicator."""
        client = await connect_mcp_server(command=sys.executable, args=[SERVER])
        try:
            result = await client.call_tool("nonexistent", {})
            assert "unknown tool" in result.lower() or "error" in result.lower()
        finally:
            await client.close()

    async def test_tool_invoke_method(self):
        """Tool.invoke works correctly for MCP-wrapped tools."""
        client = await connect_mcp_server(command=sys.executable, args=[SERVER])
        try:
            add_tool = next(t for t in client.tools if t.name == "add")
            result = await add_tool.invoke({"a": 100, "b": 200})
            assert result == "300"
        finally:
            await client.close()


# ────────────────────────────────────────────────────────────────────────────────
# Requirement 20.5: client.close() terminates subprocess/HTTP connection
# ────────────────────────────────────────────────────────────────────────────────


class TestClientLifecycle:
    """Validate that client.close() properly terminates subprocess/HTTP connections."""

    async def test_close_terminates_subprocess(self):
        """client.close() terminates the MCP server subprocess."""
        client = await connect_mcp_server(command=sys.executable, args=[SERVER])
        transport = client.transport
        assert isinstance(transport, StdioTransport)
        proc = transport._proc

        # Process is running before close
        assert proc is not None
        assert proc.returncode is None

        await client.close()

        # Process is terminated after close
        assert client._connected is False
        # Give a moment for process to terminate
        await asyncio.sleep(0.1)
        assert proc.returncode is not None

    async def test_close_marks_client_disconnected(self):
        """After close(), the client is marked as not connected."""
        client = await connect_mcp_server(command=sys.executable, args=[SERVER])
        assert client._connected is True
        await client.close()
        assert client._connected is False

    async def test_close_http_transport(self):
        """close() on HTTP transport completes without error."""
        transport = StreamableHttpTransport("https://example.com/mcp")
        client = MCPClient(transport)
        # HTTP close is a no-op but should not raise
        await client.close()
        assert client._connected is False

    async def test_context_manager_connect_and_close(self):
        """MCPClient supports async context manager for lifecycle."""
        transport = StdioTransport(sys.executable, [SERVER])
        client = MCPClient(transport)

        async with client as c:
            assert c._connected is True
            assert len(c.tools) == 3

        # After exiting context, client is closed
        assert client._connected is False

    async def test_double_close_is_safe(self):
        """Calling close() twice does not raise."""
        client = await connect_mcp_server(command=sys.executable, args=[SERVER])
        await client.close()
        await client.close()  # Should not raise

    async def test_reconnect_after_close_via_connect(self):
        """A client can be reconnected after close by calling connect() again."""
        transport = StdioTransport(sys.executable, [SERVER])
        client = MCPClient(transport)

        await client.connect()
        assert client._connected is True
        assert len(client.tools) == 3

        await client.close()
        assert client._connected is False

        # Reconnect - need a fresh transport since subprocess was terminated
        fresh_transport = StdioTransport(sys.executable, [SERVER])
        fresh_client = MCPClient(fresh_transport)
        await fresh_client.connect()
        assert fresh_client._connected is True
        assert len(fresh_client.tools) == 3
        await fresh_client.close()


# ────────────────────────────────────────────────────────────────────────────────
# Requirement 20.6: MCP tools addable to AgentSpec alongside native tools
# ────────────────────────────────────────────────────────────────────────────────


class TestMCPWithNativeTools:
    """Validate that MCP tools can be used alongside native tools in an AgentSpec."""

    async def test_mcp_tools_and_native_tools_together(self):
        """An agent can be created with both MCP and native tools."""

        @tool_decorator
        def multiply(a: int, b: int) -> int:
            """Multiply two numbers."""
            return a * b

        client = await connect_mcp_server(command=sys.executable, args=[SERVER])
        try:
            # Combine native tool with MCP tools
            all_tools = [multiply, *client.tools]
            agent = create_agent(
                "hybrid-agent",
                model=MockModel(["I can use both kinds of tools."]),
                instructions="Use all available tools.",
                tools=all_tools,
            )
            # Verify all tools are registered
            tool_names = agent.tools.names()
            assert "multiply" in tool_names
            assert "add" in tool_names
            assert "upper" in tool_names
            assert "weather" in tool_names
        finally:
            await client.close()

    async def test_model_invokes_native_and_mcp_tools(self):
        """An agent can invoke both native and MCP tools in the same run."""

        @tool_decorator
        def greet(name: str) -> str:
            """Greet a person."""
            return f"Hello, {name}!"

        client = await connect_mcp_server(command=sys.executable, args=[SERVER])
        try:
            script = [
                ToolUseBlock(name="greet", input={"name": "Alice"}),
                ToolUseBlock(name="add", input={"a": 1, "b": 2}),
                "Alice says hello and 1+2=3.",
            ]
            agent = create_agent(
                "mixed-agent",
                model=MockModel(script),
                instructions="Use tools.",
                tools=[greet, *client.tools],
            )
            result = await Harness(agent).run("Greet Alice and add 1+2.")

            tool_outputs = [
                b.content
                for m in result.messages
                for b in m.blocks
                if isinstance(b, ToolResultBlock)
            ]
            assert any("Hello, Alice!" in o for o in tool_outputs)
            assert any("3" in o for o in tool_outputs)
        finally:
            await client.close()

    async def test_mcp_tool_specs_in_agent_spec(self):
        """MCP tools produce valid ToolSpec objects for model consumption."""
        client = await connect_mcp_server(command=sys.executable, args=[SERVER])
        try:
            agent = create_agent(
                "spec-agent",
                model=MockModel(["done"]),
                tools=client.tools,
            )
            specs = agent.tools.specs
            spec_names = [s.name for s in specs]
            assert "add" in spec_names
            assert "upper" in spec_names
            assert "weather" in spec_names

            # Verify specs have proper schemas
            add_spec = next(s for s in specs if s.name == "add")
            assert add_spec.input_schema["type"] == "object"
            assert "a" in add_spec.input_schema["properties"]
        finally:
            await client.close()

    async def test_mcp_tools_do_not_conflict_with_same_named_native_tools(self):
        """If an MCP tool has the same name as a native tool, the last one added wins
        (standard ToolRegistry behavior)."""

        @tool_decorator
        def add(x: int, y: int) -> int:
            """Custom add."""
            return x + y + 100  # intentionally different

        client = await connect_mcp_server(command=sys.executable, args=[SERVER])
        try:
            # Native tool first, then MCP tools override
            agent = create_agent(
                "override-agent",
                model=MockModel(["done"]),
                tools=[add, *client.tools],
            )
            # MCP's 'add' tool should have overridden the native 'add'
            registered_add = agent.tools.get("add")
            result = await registered_add.invoke({"a": 2, "b": 3})
            assert result == "5"  # MCP add, not our custom add that adds 100
        finally:
            await client.close()
