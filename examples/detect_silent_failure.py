"""Demo: Tvastar catches a silent failure that standard error handling misses.

The agent is asked to fix failing tests. It edits the code, runs pytest — which
*still fails* — but then confidently reports "all tests pass." No exception is
raised; to normal error handling this looks like a successful run. Tvastar's
`unverified_completion` detector catches the lie.

    uv run python examples/detect_silent_failure.py
"""

from __future__ import annotations

import asyncio
import sys

# Be safe on legacy consoles (Windows cp1252) since model output may contain emoji.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from tvastar import Harness, Severity, VirtualSandbox, create_agent, default_toolset  # noqa: E402
from tvastar.model import MockModel  # noqa: E402
from tvastar.types import ToolUseBlock  # noqa: E402

# A real (still-buggy) calc + tests: the "fix" the agent applies is wrong.
STILL_BUGGY = "def add(a, b):\n    return a * b  # still wrong\n"
TESTS = "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"


def sandbox():
    return VirtualSandbox({"calc.py": "def add(a,b): return 0\n", "test_calc.py": TESTS})


def model():
    # The agent applies a bad fix, runs the (real) tests, sees them fail,
    # but still claims success. Classic silent failure.
    return MockModel(
        [
            ToolUseBlock(name="write_file", input={"path": "calc.py", "content": STILL_BUGGY}),
            ToolUseBlock(name="bash", input={"command": "pytest -q"}),
            "Fixed it — all tests pass now! ✅",
        ]
    )


async def main() -> None:
    agent = create_agent(
        "buggy-healer",
        model=model(),
        instructions="Fix the failing tests.",
        tools=default_toolset(),
        sandbox=sandbox,
    )
    result = await Harness(agent).run("Make the tests pass.")

    print("Agent's final answer:")
    print(f"  {result.text}\n")
    print(f"Looks successful? stopped={result.stopped!r}  (no exception raised)")
    print(f"Actually ok?      {result.ok}\n")

    if result.findings:
        print("Tvastar detected silent failures:")
        for f in result.findings:
            mark = "‼" if f.severity == Severity.ERROR else "•"
            print(f"  {mark} {f}")
    else:
        print("No issues detected.")


if __name__ == "__main__":
    asyncio.run(main())
