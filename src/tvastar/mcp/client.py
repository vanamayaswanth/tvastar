"""MCP client — connect an agent to the open Model Context Protocol ecosystem.

This is the piece that lets Tvastar agents use tools the *whole world* already
publishes (GitHub, Slack, Postgres, filesystem servers, ...) instead of only
tools you hand-write. It speaks the MCP handshake, lists the server's tools, and
exposes each one as a native Tvastar :class:`Tool` — so MCP tools and local tools
are indistinguishable to the model.

Usage (local stdio server)::

    from tvastar.mcp import connect_mcp_server
    client = await connect_mcp_server(command="python", args=["my_server.py"])
    agent = create_agent("a", model=m, tools=[*default_toolset(), *client.tools])
    ...
    await client.close()

Usage (remote HTTP server)::

    client = await connect_mcp_server(url="https://example.com/mcp",
                                      headers={"Authorization": "Bearer ..."})
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from ..errors import SecurityViolation, ToolError
from ..logging import StructuredLogger
from ..tools.base import Tool
from .transport import (
    MCPError,
    StdioTransport,
    StreamableHttpTransport,
    Transport,
)

if TYPE_CHECKING:
    from ..sandbox.base import SecurityPolicy

#: protocol version Tvastar advertises; servers negotiate down if needed.
PROTOCOL_VERSION = "2025-06-18"


class MCPClient:
    """A connected MCP session exposing its tools as Tvastar Tools."""

    def __init__(
        self,
        transport: Transport,
        *,
        name: str = "mcp",
        policy: "SecurityPolicy | None" = None,
        logger: StructuredLogger | None = None,
    ):
        self.transport = transport
        self.name = name
        self.server_info: dict[str, Any] = {}
        self._tools: list[Tool] = []
        self._tool_names_at_connect: list[str] = []  # names before disconnect
        self._connected = False
        self._policy = policy
        self._logger = logger or StructuredLogger(name=f"mcp.{name}")

    # ---- factories ------------------------------------------------------

    @classmethod
    def stdio(cls, command: str, args: Optional[list[str]] = None, **kw: Any) -> "MCPClient":
        return cls(StdioTransport(command, args, **kw), name=kw.get("name", command))

    @classmethod
    def http(cls, url: str, **kw: Any) -> "MCPClient":
        headers = kw.pop("headers", None)
        timeout = kw.pop("timeout", 30.0)
        return cls(StreamableHttpTransport(url, headers=headers, timeout=timeout))

    # ---- lifecycle ------------------------------------------------------

    async def connect(self) -> "MCPClient":
        """Run the MCP handshake and load the tool list."""
        if self._connected:
            return self
        await self.transport.start()
        result = await self.transport.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "tvastar", "version": "0.1.0"},
            },
        )
        self.server_info = (result or {}).get("serverInfo", {})
        # Per spec, confirm initialization before issuing further requests.
        await self.transport.notify("notifications/initialized")
        await self._load_tools()
        self._connected = True
        return self

    async def _load_tools(self) -> None:
        result = await self.transport.request("tools/list")
        self._tools = [self._wrap(t) for t in (result or {}).get("tools", [])]
        self._tool_names_at_connect = [t.name for t in self._tools]

    async def close(self) -> None:
        """Close connection and mark tools as unavailable (REQ-4 AC3)."""
        await self.transport.close()
        self._tools = []  # mark tools unavailable on disconnect
        self._connected = False

    async def handle_disconnect(self) -> None:
        """Handle an unexpected server disconnect.

        Marks all tools as unavailable and reports via ToolError without
        crashing the session. Callers should catch ToolError and continue.
        """
        tool_names = self._tool_names_at_connect
        self._tools = []
        self._connected = False
        try:
            await self.transport.close()
        except Exception:
            pass  # best-effort cleanup
        raise ToolError(f"MCP server '{self.name}' disconnected; unavailable tools: {tool_names}")

    async def __aenter__(self) -> "MCPClient":
        return await self.connect()

    async def __aexit__(self, *exc) -> None:
        await self.close()

    # ---- tools ----------------------------------------------------------

    @property
    def tools(self) -> list[Tool]:
        return self._tools

    def tool_names(self) -> list[str]:
        return [t.name for t in self._tools]

    async def call_tool(self, name: str, arguments: dict) -> str:
        """Invoke an MCP tool directly and return its text result.

        Raises SecurityViolation if blocked by policy (REQ-7).
        Raises ToolError if the server is disconnected (REQ-4 AC3).
        """
        if self._policy is not None:
            self._check_mcp_policy(name)
        if not self._connected:
            raise ToolError(
                f"MCP server '{self.name}' is disconnected; "
                f"unavailable tools: {self._tool_names_at_connect}"
            )
        try:
            result = await self.transport.request(
                "tools/call", {"name": name, "arguments": arguments}
            )
        except (OSError, MCPError) as exc:
            # Transport failure → treat as disconnect
            self._tools = []
            self._connected = False
            raise ToolError(
                f"MCP server '{self.name}' disconnected during call to '{name}'; "
                f"unavailable tools: {self._tool_names_at_connect}"
            ) from exc
        return _render_tool_result(result or {})

    def _wrap(self, spec: dict) -> Tool:
        name = spec["name"]
        description = spec.get("description", name)
        schema = spec.get("inputSchema") or {"type": "object", "properties": {}}

        async def _invoke(**kwargs: Any) -> str:
            return await self.call_tool(name, kwargs)

        # Construct the Tool directly: the schema comes from the server, not
        # from Python introspection.
        return Tool(
            name=name,
            description=description,
            fn=_invoke,
            input_schema=schema,
            wants_ctx=False,
        )

    # ---- MCP policy enforcement (REQ-7) --------------------------------

    def _check_mcp_policy(self, tool_name: str) -> None:
        """Evaluate tool name against MCP-specific policy fields."""
        policy = self._policy
        if policy is None:
            return
        if tool_name in policy.denied_mcp_tools:
            self._audit_blocked(tool_name, "denylist_hit")
            raise SecurityViolation(f"MCP tool '{tool_name}' blocked by denied_mcp_tools policy")
        if policy.allowed_mcp_tools and tool_name not in policy.allowed_mcp_tools:
            self._audit_blocked(tool_name, "allowlist_miss")
            raise SecurityViolation(f"MCP tool '{tool_name}' not in allowed_mcp_tools allowlist")

    def _audit_blocked(self, tool_name: str, denied_reason: str) -> None:
        """Emit structured audit log for a blocked MCP tool invocation."""
        server_name = self.server_info.get("name", "unknown")
        self._logger.emit(
            "WARNING",
            f"MCP tool invocation blocked: {tool_name}",
            tool_name=tool_name,
            server_name=server_name,
            denied_reason=denied_reason,
            policy_rule="denied_mcp_tools"
            if denied_reason == "denylist_hit"
            else "allowed_mcp_tools",
        )


def _render_tool_result(result: dict) -> str:
    """Flatten an MCP tools/call result into a string for the model."""
    is_error = result.get("isError", False)
    parts: list[str] = []
    for item in result.get("content", []):
        itype = item.get("type")
        if itype == "text":
            parts.append(item.get("text", ""))
        elif itype == "resource":
            res = item.get("resource", {})
            parts.append(res.get("text") or res.get("uri", "[resource]"))
        else:
            parts.append(f"[{itype}]")
    # Some servers return structuredContent instead of/in addition to content.
    if not parts and "structuredContent" in result:
        import json

        parts.append(json.dumps(result["structuredContent"], default=str))
    text = "\n".join(p for p in parts if p) or "[no content]"
    return f"[error] {text}" if is_error else text


async def connect_mcp_server(
    *,
    command: Optional[str] = None,
    args: Optional[list[str]] = None,
    url: Optional[str] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: float = 30.0,
    **kw: Any,
) -> MCPClient:
    """Connect to an MCP server (local stdio or remote HTTP) and return a ready
    :class:`MCPClient` whose ``.tools`` can be handed straight to ``create_agent``.

    Provide either ``command`` (+ optional ``args``) for a local stdio server,
    or ``url`` (+ optional ``headers``) for a remote HTTP server.
    """
    if command:
        client = MCPClient.stdio(command, args, timeout=timeout, **kw)
    elif url:
        client = MCPClient.http(url, headers=headers, timeout=timeout)
    else:
        raise MCPError("connect_mcp_server requires either 'command' or 'url'")
    return await client.connect()
