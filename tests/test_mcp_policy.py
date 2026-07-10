"""Tests for MCP policy enforcement (REQ-7) — tasks 11.2 and 11.3."""

import io
import json
import warnings

import pytest

from tvastar.errors import SecurityViolation
from tvastar.logging import StructuredLogger
from tvastar.mcp.client import MCPClient
from tvastar.mcp.transport import Transport
from tvastar.sandbox.base import SecurityPolicy


class FakeTransport(Transport):
    """Minimal transport stub for policy tests — no real server needed."""

    async def start(self) -> None:
        pass

    async def request(self, method: str, params: dict | None = None) -> dict | None:
        if method == "tools/call":
            return {"content": [{"type": "text", "text": "ok"}]}
        return {}

    async def notify(self, method: str, params: dict | None = None) -> None:
        pass

    async def close(self) -> None:
        pass


def _make_client(
    policy: SecurityPolicy | None = None,
    server_name: str = "test-server",
) -> MCPClient:
    """Create an MCPClient with fake transport and optional policy."""
    log_output = io.StringIO()
    logger = StructuredLogger(name="test", output=log_output)
    client = MCPClient(FakeTransport(), name="test", policy=policy, logger=logger)
    client._connected = True
    client.server_info = {"name": server_name}
    # Expose log output for assertions
    client._log_output = log_output  # type: ignore[attr-defined]
    return client


class TestMCPPolicyDenylist:
    """Tool in denied_mcp_tools → SecurityViolation raised, audit logged."""

    @pytest.mark.asyncio
    async def test_denied_tool_raises_security_violation(self):
        policy = SecurityPolicy(denied_mcp_tools={"dangerous_tool"})
        client = _make_client(policy=policy)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            with pytest.raises(SecurityViolation, match="denied_mcp_tools"):
                await client.call_tool("dangerous_tool", {})

    @pytest.mark.asyncio
    async def test_denied_tool_audit_log_contains_required_fields(self):
        policy = SecurityPolicy(denied_mcp_tools={"bad_tool"})
        client = _make_client(policy=policy, server_name="my-server")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            with pytest.raises(SecurityViolation):
                await client.call_tool("bad_tool", {})

        log_line = client._log_output.getvalue()  # type: ignore[attr-defined]
        entry = json.loads(log_line)
        assert entry["tool_name"] == "bad_tool"
        assert entry["server_name"] == "my-server"
        assert entry["denied_reason"] == "denylist_hit"
        assert entry["policy_rule"] == "denied_mcp_tools"


class TestMCPPolicyAllowlist:
    """Tool NOT in non-empty allowed_mcp_tools → SecurityViolation raised."""

    @pytest.mark.asyncio
    async def test_tool_not_in_allowlist_raises(self):
        policy = SecurityPolicy(allowed_mcp_tools={"safe_a", "safe_b"})
        client = _make_client(policy=policy)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            with pytest.raises(SecurityViolation, match="allowlist"):
                await client.call_tool("unknown_tool", {})

    @pytest.mark.asyncio
    async def test_allowlist_miss_audit_log(self):
        policy = SecurityPolicy(allowed_mcp_tools={"only_this"})
        client = _make_client(policy=policy, server_name="allow-srv")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            with pytest.raises(SecurityViolation):
                await client.call_tool("other", {})

        entry = json.loads(client._log_output.getvalue())  # type: ignore[attr-defined]
        assert entry["tool_name"] == "other"
        assert entry["server_name"] == "allow-srv"
        assert entry["denied_reason"] == "allowlist_miss"
        assert entry["policy_rule"] == "allowed_mcp_tools"

    @pytest.mark.asyncio
    async def test_tool_in_allowlist_passes(self):
        policy = SecurityPolicy(allowed_mcp_tools={"permitted"})
        client = _make_client(policy=policy)

        result = await client.call_tool("permitted", {})
        assert result == "ok"
        # No audit log emitted
        assert client._log_output.getvalue() == ""  # type: ignore[attr-defined]


class TestMCPPolicyNoRestrictions:
    """No policy or empty lists → all tools permitted."""

    @pytest.mark.asyncio
    async def test_no_policy_permits_all(self):
        client = _make_client(policy=None)
        result = await client.call_tool("anything", {})
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_empty_lists_permits_all(self):
        policy = SecurityPolicy()  # both lists empty by default
        client = _make_client(policy=policy)
        result = await client.call_tool("any_tool", {})
        assert result == "ok"


class TestMCPPolicyForwarding:
    """Permitted requests forwarded unmodified (AC7)."""

    @pytest.mark.asyncio
    async def test_permitted_tool_returns_server_result(self):
        policy = SecurityPolicy(allowed_mcp_tools={"add"})
        client = _make_client(policy=policy)
        result = await client.call_tool("add", {"a": 1, "b": 2})
        # FakeTransport returns "ok" for any tools/call
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_denylist_does_not_block_unlisted_tool(self):
        policy = SecurityPolicy(denied_mcp_tools={"blocked"})
        client = _make_client(policy=policy)
        result = await client.call_tool("safe_tool", {})
        assert result == "ok"
