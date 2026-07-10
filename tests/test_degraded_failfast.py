"""Tests for fail-fast behavior in Session and MCPClient (REQ-4 AC2, AC3, AC5)."""

import pytest

from tvastar.degraded import DegradedStateTracker
from tvastar.errors import DegradedState, ModelError, ToolError
from tvastar.logging import StructuredLogger
from tvastar.mcp.client import MCPClient
from tvastar.mcp.transport import MCPError


# ---------------------------------------------------------------------------
# Session fail-fast (REQ-4 AC2)
# ---------------------------------------------------------------------------


async def test_session_raises_model_error_when_model_unavailable():
    """Session raises ModelError immediately when model_unavailable is active."""
    from tvastar import Harness, create_agent
    from tvastar.model import MockModel

    logger = StructuredLogger(name="test")
    tracker = DegradedStateTracker(logger)
    await tracker.enter(DegradedState.model_unavailable, "provider down")

    agent = create_agent("a", model=MockModel(["hi"]), instructions="test")
    harness = Harness(agent)
    session = harness.session()
    session.degraded_tracker = tracker
    await session.start()

    with pytest.raises(ModelError, match="model_unavailable"):
        await session.prompt("hello")

    await session.close()


async def test_session_works_normally_without_degraded_tracker():
    """Session works fine when degraded_tracker is None (default)."""
    from tvastar import Harness, create_agent
    from tvastar.model import MockModel

    agent = create_agent("a", model=MockModel(["response"]), instructions="test")
    harness = Harness(agent)
    result = await harness.run("hi")
    assert result.text == "response"


async def test_session_works_when_model_available():
    """Session proceeds normally when tracker exists but model_unavailable is not active."""
    from tvastar import Harness, create_agent
    from tvastar.model import MockModel

    logger = StructuredLogger(name="test")
    tracker = DegradedStateTracker(logger)
    # Only mcp_disconnected is active, not model_unavailable
    await tracker.enter(DegradedState.mcp_disconnected, "some server down")

    agent = create_agent("a", model=MockModel(["response"]), instructions="test")
    harness = Harness(agent)
    session = harness.session()
    session.degraded_tracker = tracker
    await session.start()

    result = await session.prompt("hello")
    assert result.text == "response"

    await session.close()


# ---------------------------------------------------------------------------
# MCPClient disconnect handling (REQ-4 AC3)
# ---------------------------------------------------------------------------


class FakeTransport:
    """Minimal transport for testing MCPClient disconnect behavior."""

    def __init__(self):
        self._started = False
        self._closed = False
        self._tools = [
            {
                "name": "tool_a",
                "description": "A",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "tool_b",
                "description": "B",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]

    async def start(self):
        self._started = True

    async def close(self):
        self._closed = True

    async def request(self, method, params=None):
        if self._closed:
            raise MCPError("transport closed")
        if method == "initialize":
            return {"serverInfo": {"name": "fake"}}
        if method == "tools/list":
            return {"tools": self._tools}
        if method == "tools/call":
            return {"content": [{"type": "text", "text": "result"}]}
        return {}

    async def notify(self, method, params=None):
        pass


async def test_mcp_client_raises_tool_error_after_close():
    """MCPClient raises ToolError when calling a tool after disconnect."""
    client = MCPClient(FakeTransport(), name="test-server")
    await client.connect()
    assert client.tool_names() == ["tool_a", "tool_b"]

    await client.close()

    # Tools should be cleared
    assert client.tools == []

    # call_tool should raise ToolError with the unavailable tool names
    with pytest.raises(ToolError, match="disconnected"):
        await client.call_tool("tool_a", {})


async def test_mcp_client_reports_unavailable_tools_in_error():
    """ToolError message includes the names of tools that became unavailable."""
    client = MCPClient(FakeTransport(), name="my-server")
    await client.connect()
    await client.close()

    with pytest.raises(ToolError) as exc_info:
        await client.call_tool("tool_a", {})

    msg = str(exc_info.value)
    assert "tool_a" in msg
    assert "tool_b" in msg
    assert "my-server" in msg


async def test_mcp_client_handles_transport_error_as_disconnect():
    """MCPClient treats transport errors during call_tool as disconnect."""

    class FailingTransport(FakeTransport):
        async def request(self, method, params=None):
            if method == "tools/call":
                raise OSError("connection reset")
            return await super().request(method, params)

    client = MCPClient(FailingTransport(), name="flaky")
    await client.connect()
    assert len(client.tools) == 2

    with pytest.raises(ToolError, match="disconnected"):
        await client.call_tool("tool_a", {})

    # After error, tools are marked unavailable
    assert client.tools == []
    assert not client._connected


async def test_mcp_client_session_continues_after_disconnect():
    """Session continues (does not crash) when MCP tool raises ToolError."""
    # This verifies AC3: "continues session without crashing"
    # The session loop catches ToolError and feeds it back to the model.
    from tvastar import Harness, create_agent
    from tvastar.model import MockModel
    from tvastar.types import ToolUseBlock

    client = MCPClient(FakeTransport(), name="test-server")
    await client.connect()

    # Model calls tool_a, then after tool error, gives final response
    script = [
        ToolUseBlock(name="tool_a", input={}),
        "I couldn't use the tool, but I'm still here.",
    ]
    agent = create_agent(
        "mcp-agent",
        model=MockModel(script),
        instructions="test",
        tools=client.tools,
    )

    # Disconnect server before running the agent
    await client.close()

    # The agent should still complete (tool error is fed back as tool result)
    harness = Harness(agent)
    result = await harness.run("do something")
    # Session didn't crash — we got a final response
    assert result.text == "I couldn't use the tool, but I'm still here."


# ---------------------------------------------------------------------------
# Sandbox resource limits (REQ-4 AC5)
# ---------------------------------------------------------------------------


async def test_sandbox_returns_exec_result_on_timeout():
    """Sandbox returns ExecResult with failure status on timeout, remains available.

    REQ-4 AC5: return ExecResult with failure status and description of exceeded
    limit, remain available for subsequent executions within 1 second.
    """
    import time as _time

    from tvastar.sandbox import VirtualSandbox

    sandbox = VirtualSandbox(allow_python=True)
    await sandbox.start()

    # Write a script that sleeps, then execute with short timeout
    sandbox.fs.write("sleeper.py", "import time\ntime.sleep(10)\n")
    result = await sandbox.exec("python sleeper.py", timeout=0.5)
    assert not result.ok
    assert result.exit_code != 0
    # Description of exceeded limit is present in output
    assert "timed out" in (result.stdout + result.stderr).lower()

    # Sandbox remains available for subsequent executions within 1 second
    t0 = _time.monotonic()
    result2 = await sandbox.exec("echo hello")
    elapsed = _time.monotonic() - t0
    assert result2.ok
    assert "hello" in result2.stdout
    assert elapsed < 1.0  # remains available within 1 second

    await sandbox.stop()
