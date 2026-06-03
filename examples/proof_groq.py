"""Real-model proof run — a live LLM drives Tvastar end to end (free, via Groq).

Groq exposes an OpenAI-compatible API, so Tvastar uses it with no new code: just
`OpenAIModel(base_url=..., api_key=...)`. The model is given a buggy `calc.py`
plus tests and must actually fix them — writing files and running pytest in the
in-memory VirtualSandbox (no Docker), iterating until the suite is green.

    export GROQ_API_KEY=...            # do NOT hardcode it
    uv run python examples/proof_groq.py
"""

from __future__ import annotations

import asyncio
import os
import sys

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from tvastar import Harness, VirtualSandbox, create_agent, default_toolset  # noqa: E402
from tvastar.model import OpenAIModel  # noqa: E402

BUGGY = """\
def add(a, b):
    return a - b      # BUG

def multiply(a, b):
    return a + b      # BUG
"""

TESTS = """\
from calc import add, multiply

def test_add():
    assert add(2, 3) == 5

def test_multiply():
    assert multiply(2, 3) == 6
"""


def sandbox():
    return VirtualSandbox({"calc.py": BUGGY, "test_calc.py": TESTS})


async def main() -> None:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        print("Set GROQ_API_KEY to run the live proof.")
        return

    model = OpenAIModel(
        model=os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
        base_url="https://api.groq.com/openai/v1",
        api_key=key,
    )
    agent = create_agent(
        "self-healer",
        model=model,
        instructions=(
            "You are a self-healing coding agent. Run the tests with the bash "
            "tool (`pytest -q`), read failures, fix calc.py with write_file or "
            "edit_file, and re-run until ALL tests pass. Then briefly summarize."
        ),
        tools=default_toolset(),
        sandbox=sandbox,
        max_steps=12,
    )

    print(f"Model: {model.name} (live via Groq)\n")
    sess = Harness(agent).session()
    async with sess:
        result = await sess.prompt("Make the test suite pass.")
        final_calc = sess.sandbox.fs.read("calc.py")

    print("=== Tool calls the model actually made ===")
    from tvastar.types import ToolUseBlock

    for m in result.messages:
        for b in m.blocks:
            if isinstance(b, ToolUseBlock):
                arg = b.input.get("command") or b.input.get("path") or ""
                print(f"  -> {b.name}({str(arg)[:60]})")

    print("\n=== Final answer ===")
    print(result.text)
    print(f"\nsteps={result.steps}  stopped={result.stopped}  ok={result.ok}")
    fixed = "+ b" in final_calc and "* b" in final_calc
    print(f"calc.py actually correct: {fixed}")
    if result.findings:
        print("findings:", [str(f) for f in result.findings])


if __name__ == "__main__":
    asyncio.run(main())
