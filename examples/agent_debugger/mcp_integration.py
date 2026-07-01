"""MCP integration for the Agent Debugger pipeline.

Provides optional MCP server connectivity for enhanced code analysis.
When an MCP server URL is configured, the module discovers and registers
available tools at session startup. When not configured, all functions
gracefully return empty results so the pipeline continues with built-in
tools only.

Requirements: 11.1, 11.2, 11.3
"""

from __future__ import annotations

import logging
from typing import Any

from .schemas import FailureMode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def discover_mcp_tools(server_url: str) -> list[dict]:
    """Connect to an MCP server and discover available tools.

    Connects to the given MCP server URL, performs the MCP handshake,
    and returns metadata dicts for each tool the server exposes.

    Args:
        server_url: The HTTP(S) URL of the MCP server.

    Returns:
        A list of tool metadata dicts with keys: name, description, input_schema.
        Returns an empty list on any connection or protocol failure.
    """
    try:
        from tvastar.mcp import connect_mcp_server

        client = await connect_mcp_server(url=server_url)
        tools_meta: list[dict] = []
        for tool in client.tools:
            tools_meta.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": getattr(tool, "input_schema", {}),
                }
            )
        logger.info(
            "Discovered %d MCP tools from %s: %s",
            len(tools_meta),
            server_url,
            [t["name"] for t in tools_meta],
        )
        return tools_meta
    except Exception as exc:
        logger.warning(
            "MCP tool discovery failed for %s: %s. Continuing with built-in tools only.",
            server_url,
            exc,
        )
        return []


def register_mcp_tools(session: Any, tools: list[dict]) -> None:
    """Register discovered MCP tools with a Tvastar session.

    Integrates MCP tool metadata into the session so that sub-agents
    can invoke them during analysis. If registration fails, logs a
    warning and continues without MCP tools.

    Args:
        session: A Tvastar session or harness instance.
        tools: List of tool metadata dicts from discover_mcp_tools().
    """
    if not tools:
        return

    try:
        from tvastar.tools.base import Tool

        for tool_meta in tools:
            name = tool_meta["name"]
            description = tool_meta.get("description", name)
            schema = tool_meta.get("input_schema", {"type": "object", "properties": {}})

            # Create a placeholder tool that logs invocation attempts.
            # In a real integration the tool's fn would route through the
            # MCP client; here we register metadata so governance and
            # masking layers are aware of the tools.
            async def _placeholder(**kwargs: Any) -> str:
                return f"[MCP tool '{name}' invoked with {kwargs}]"

            tool = Tool(
                name=name,
                description=f"[MCP] {description}",
                fn=_placeholder,
                input_schema=schema,
                wants_ctx=False,
            )

            # Attach to session if it has a tools/tool registry attribute
            if hasattr(session, "tools") and isinstance(session.tools, list):
                session.tools.append(tool)
            elif hasattr(session, "_tools") and isinstance(session._tools, list):
                session._tools.append(tool)

        logger.info("Registered %d MCP tools with session.", len(tools))
    except Exception as exc:
        logger.warning(
            "Failed to register MCP tools: %s. Continuing without MCP tools.",
            exc,
        )


async def run_mcp_analysis(
    session: Any,
    messages: list[Any],
    tools: list[dict],
) -> list[FailureMode]:
    """Run MCP-based analysis and return findings as FailureMode instances.

    Uses registered MCP tools (e.g. static analysis, linting) to analyze
    the trajectory messages and produce additional failure mode findings
    that complement the built-in Tvastar detectors.

    Args:
        session: A Tvastar session or harness instance.
        messages: The trajectory messages to analyze.
        tools: List of tool metadata dicts from discover_mcp_tools().

    Returns:
        A list of FailureMode instances from MCP-based analysis.
        Returns an empty list if no MCP tools are available or on failure.
    """
    if not tools:
        return []

    findings: list[FailureMode] = []

    try:
        # Look for analysis-capable tools (common patterns: lint, analyze, check)
        analysis_tools = [
            t
            for t in tools
            if any(
                keyword in t["name"].lower()
                for keyword in ("lint", "analyze", "check", "scan", "review", "audit")
            )
        ]

        if not analysis_tools:
            logger.debug(
                "No analysis-capable MCP tools found among: %s",
                [t["name"] for t in tools],
            )
            return []

        # Attempt to invoke each analysis tool against the trajectory content
        for tool_meta in analysis_tools:
            try:
                tool_name = tool_meta["name"]

                # Build a summary of the trajectory for analysis
                trajectory_text = "\n".join(getattr(m, "text", str(m))[:500] for m in messages[:20])

                # If session has a call_tool or similar interface, use it
                result_text: str | None = None
                if hasattr(session, "call_tool"):
                    result_text = await session.call_tool(tool_name, {"content": trajectory_text})
                elif hasattr(session, "_mcp_client") and session._mcp_client:
                    result_text = await session._mcp_client.call_tool(
                        tool_name, {"content": trajectory_text}
                    )

                if result_text:
                    findings.append(
                        FailureMode(
                            detector=f"mcp:{tool_name}",
                            severity="info",
                            message=f"MCP analysis ({tool_name}): {result_text[:300]}",
                            evidence=[result_text[:500]],
                            line_range=(0, max(len(messages) - 1, 0)),
                        )
                    )
            except Exception as tool_exc:
                logger.debug(
                    "MCP tool '%s' analysis failed: %s",
                    tool_meta["name"],
                    tool_exc,
                )
                continue

    except Exception as exc:
        logger.warning(
            "MCP analysis failed: %s. Continuing without MCP findings.",
            exc,
        )

    return findings


async def setup_mcp(session: Any, server_url: str | None) -> list[dict]:
    """Convenience function: discover and register MCP tools if configured.

    If server_url is None, this function does nothing and returns an empty
    list. Otherwise it discovers tools from the MCP server and registers
    them with the session.

    Args:
        session: A Tvastar session or harness instance.
        server_url: The MCP server URL, or None to skip MCP setup.

    Returns:
        List of discovered tool metadata dicts (empty if URL is None or
        discovery fails).
    """
    if server_url is None:
        logger.debug("No MCP server URL configured; skipping MCP setup.")
        return []

    tools = await discover_mcp_tools(server_url)
    if tools:
        register_mcp_tools(session, tools)
    return tools
