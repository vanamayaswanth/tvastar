"""An agent that uses tools from a real MCP server — no Docker, no network.

It spawns the local stdio MCP server in examples/mcp_echo_server.py, mounts its
tools (add / upper / weather) as native Tvastar tools, and runs a task that uses
them. Set TVASTAR_REAL=1 + ANTHROPIC_API_KEY to let Claude choose the tools.

    uv run python examples/mcp_agent.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from tvastar import Harness, create_agent
from tvastar.mcp import connect_mcp_server

SERVER = str(Path(__file__).parent / "mcp_echo_server.py")


def _model():
    if os.environ.get("TVASTAR_REAL") == "1":
        from tvastar.model import AnthropicModel

        return AnthropicModel("claude-opus-4-8")
    from tvastar.model import MockModel
    from tvastar.types import ToolUseBlock

    return MockModel(
        [
            ToolUseBlock(name="weather", input={"city": "Tokyo"}),
            ToolUseBlock(name="add", input={"a": 40, "b": 2}),
            "Tokyo is 22°C and clear, and 40 + 2 = 42 — both answered using MCP tools.",
        ]
    )


async def main() -> None:
    # Connect to the MCP server and mount its tools.
    client = await connect_mcp_server(command=sys.executable, args=[SERVER])
    print(f"Connected to MCP server '{client.server_info.get('name')}'")
    print(f"Discovered MCP tools: {client.tool_names()}\n")

    agent = create_agent(
        "mcp-agent",
        model=_model(),
        instructions="You answer questions using the available tools.",
        tools=client.tools,  # <- MCP tools, used like any native tool
    )
    try:
        result = await Harness(agent).run("What's the weather in Tokyo, and what is 40 + 2?")
        print(result.text)
        print(f"\n[steps={result.steps}, used MCP tools from a live server]")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
