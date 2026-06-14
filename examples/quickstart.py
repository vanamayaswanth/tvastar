"""Minimal end-to-end demo — runs offline with the mock model.

uv run python examples/quickstart.py
"""

from __future__ import annotations

import asyncio

from tvastar import ConsoleExporter, Harness, Tracer, create_agent, default_toolset, tool
from tvastar.model import MockModel
from tvastar.types import ToolUseBlock


@tool
def add(a: int, b: int) -> int:
    "Add two integers."
    return a + b


async def main() -> None:
    # Scripted mock so the demo is deterministic and key-free.
    script = [
        ToolUseBlock(name="add", input={"a": 2, "b": 3}),
        ToolUseBlock(name="write_file", input={"path": "result.txt", "content": "5"}),
        "I computed 2 + 3 = 5 and saved it to result.txt.",
    ]
    agent = create_agent(
        "demo",
        model=MockModel(script),
        instructions="You are a helpful assistant.",
        tools=[*default_toolset(), add],
    )
    harness = Harness(agent, tracer=Tracer([ConsoleExporter()]))
    result = await harness.run("Add 2 and 3, then save the answer.")

    print("\n=== RESULT ===")
    print(result.text)
    print(f"(steps={result.steps}, stopped={result.stopped}, usage={result.usage})")


if __name__ == "__main__":
    asyncio.run(main())
