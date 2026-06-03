"""A coding agent with the full toolset, loadable by the CLI/server.

Run it:
    tvastar info  examples/coding_agent.py:agent
    tvastar run   examples/coding_agent.py:agent "Create fib.py and run it"
    tvastar chat  examples/coding_agent.py:agent
    tvastar serve examples/coding_agent.py:agent --port 8000

By default it uses MockModel so it runs with no API key. Set TVASTAR_REAL=1 and
ANTHROPIC_API_KEY to drive a real Claude model instead.
"""

from __future__ import annotations

import os
from pathlib import Path

from tvastar import LocalSandbox, SecurityPolicy, SkillLibrary, create_agent, default_toolset
from tvastar.model import MockModel

_SKILLS_DIR = Path(__file__).parent / "skills"


def _model():
    if os.environ.get("TVASTAR_REAL") == "1":
        from tvastar.model import AnthropicModel

        return AnthropicModel("claude-opus-4-8")
    # Offline default: a scripted demo run.
    from tvastar.types import ToolUseBlock

    return MockModel(
        [
            ToolUseBlock(
                name="write_file",
                input={
                    "path": "fib.py",
                    "content": "def fib(n):\n    return n if n < 2 else fib(n-1)+fib(n-2)\n\nprint([fib(i) for i in range(10)])\n",
                },
            ),
            ToolUseBlock(name="bash", input={"command": "python fib.py"}),
            "Created fib.py and ran it — it prints the first 10 Fibonacci numbers.",
        ]
    )


# A jailed local sandbox so `python` actually runs, but nothing escapes the dir.
def _sandbox() -> LocalSandbox:
    policy = SecurityPolicy(
        allowed_commands={"python", "python3", "ls", "cat", "echo", "pytest"},
        network=False,
        timeout_seconds=30,
    )
    return LocalSandbox(".tvastar-workspace", policy=policy)


agent = create_agent(
    "coding-agent",
    model=_model(),
    instructions=(
        "You are an autonomous coding agent. Given a goal, use your tools to "
        "write and run code in the workspace until the goal is met. Prefer small "
        "steps and verify your work by running it."
    ),
    tools=default_toolset(),
    skills=SkillLibrary.from_dirs(_SKILLS_DIR),
    sandbox=_sandbox,
    max_steps=15,
)
