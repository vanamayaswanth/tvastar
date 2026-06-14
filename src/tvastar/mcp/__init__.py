"""MCP (Model Context Protocol) integration.

Connect Tvastar agents to the open MCP ecosystem — local stdio servers or remote
HTTP servers — and use their tools as native Tvastar tools.
"""

from .client import MCPClient, connect_mcp_server
from .transport import (
    MCPError,
    StdioTransport,
    StreamableHttpTransport,
    Transport,
)

__all__ = [
    "MCPClient",
    "connect_mcp_server",
    "Transport",
    "StdioTransport",
    "StreamableHttpTransport",
    "MCPError",
]
