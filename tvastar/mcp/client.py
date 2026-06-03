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

from typing import Any, Optional

from ..tools.base import Tool
from .transport import (
    MCPError,
    StdioTransport,
    StreamableHttpTransport,
    Transport,
)

#: protocol version Tvastar advertises; servers negotiate down if needed.
PROTOCOL_VERSION = "2025-06-18"


class MCPClient:
    """A connected MCP session exposing its tools as Tvastar Tools."""

    def __init__(self, transport: Transport, *, name: str = "mcp"):
        self.transport = transport
        self.name = name
        self.server_info: dict[str, Any] = {}
        self._tools: list[Tool] = []
        self._connected = False

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

    async def close(self) -> None:
        await self.transport.close()
        self._connected = False

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
        """Invoke an MCP tool directly and return its text result."""
        result = await self.transport.request("tools/call", {"name": name, "arguments": arguments})
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
