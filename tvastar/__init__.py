"""Tvastar — a programmable agent harness framework for Python.

    Agent = Model + Harness

Give a model instructions, tools, skills, memory, and a sandbox, and it does
autonomous, goal-driven work — the harness architecture behind modern coding
agents, brought to Python.

Quick start::

    import asyncio
    from tvastar import create_agent, Harness, default_toolset
    from tvastar.model import MockModel  # or AnthropicModel(...)

    agent = create_agent(
        "assistant",
        model=MockModel(),
        instructions="You are a helpful coding agent.",
        tools=default_toolset(),
    )
    harness = Harness(agent)
    print(asyncio.run(harness.run("List the files in the workspace.")))
"""

from __future__ import annotations

from .agent import AgentSpec, create_agent
from .durable import Checkpointer
from .errors import (
    ModelError,
    SandboxError,
    SecurityViolation,
    SkillError,
    ToolError,
    ToolNotFound,
    TvastarError,
)
from .detect import (
    Finding,
    RunContext,
    Severity,
    default_detectors,
    run_detectors,
)
from .harness import Harness
from .mcp import MCPClient, connect_mcp_server
from .memory import FileStore, InMemoryStore, Memory, Store
from .model import Model, MockModel
from .observability import (
    ConsoleExporter,
    JSONLExporter,
    OTelExporter,
    Tracer,
)
from .sandbox import (
    ExecResult,
    LocalSandbox,
    Sandbox,
    SecurityPolicy,
    VirtualSandbox,
)
from .session import RunResult, Session
from .skills import Skill, SkillLibrary, parse_skill
from .tools import Tool, ToolContext, ToolRegistry, default_toolset, tool
from .types import (
    Message,
    ModelResponse,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)

__version__ = "0.1.0"

__all__ = [
    # core
    "create_agent",
    "AgentSpec",
    "Harness",
    "Session",
    "RunResult",
    # model
    "Model",
    "MockModel",
    # tools
    "tool",
    "Tool",
    "ToolContext",
    "ToolRegistry",
    "default_toolset",
    # skills
    "Skill",
    "SkillLibrary",
    "parse_skill",
    # sandbox
    "Sandbox",
    "VirtualSandbox",
    "LocalSandbox",
    "SecurityPolicy",
    "ExecResult",
    # memory + durable
    "Store",
    "InMemoryStore",
    "FileStore",
    "Memory",
    "Checkpointer",
    # MCP
    "MCPClient",
    "connect_mcp_server",
    # failure detection
    "Finding",
    "Severity",
    "RunContext",
    "default_detectors",
    "run_detectors",
    # observability
    "Tracer",
    "ConsoleExporter",
    "JSONLExporter",
    "OTelExporter",
    # types
    "Message",
    "ModelResponse",
    "TextBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "StreamEvent",
    "Usage",
    # errors
    "TvastarError",
    "ModelError",
    "ToolError",
    "ToolNotFound",
    "SkillError",
    "SandboxError",
    "SecurityViolation",
]
