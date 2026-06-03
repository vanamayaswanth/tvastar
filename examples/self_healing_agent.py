"""Flagship demo — a self-healing code agent, portable across sandboxes.

This is the proof of Tvastar's core promise:

    Write a code-executing, self-correcting agent ONCE, run it ANYWHERE —
    in-memory (no Docker), on the local machine, or in a container —
    by changing a single line.

The task: the workspace contains a buggy `calc.py` and a `test_calc.py`. The
agent must run the tests, read the failure, fix the code, and re-run until the
suite passes. The *code execution and test verification are completely real*
(it actually runs pytest); only the model's decisions are scripted when running
offline. Set TVASTAR_REAL=1 + ANTHROPIC_API_KEY to let a real Claude model drive.

    uv run python examples/self_healing_agent.py
"""

from __future__ import annotations

import asyncio
import os

from tvastar import Harness, SecurityPolicy, VirtualSandbox, create_agent, default_toolset
from tvastar.sandbox import LocalSandbox

BUGGY_CALC = """\
def add(a, b):
    return a - b  # BUG: should add

def multiply(a, b):
    return a + b  # BUG: should multiply
"""

TESTS = """\
from calc import add, multiply

def test_add():
    assert add(2, 3) == 5

def test_multiply():
    assert multiply(2, 3) == 6
"""

FIXED_CALC = """\
def add(a, b):
    return a + b

def multiply(a, b):
    return a * b
"""

INSTRUCTIONS = (
    "You are a self-healing coding agent. Run the test suite with `bash` "
    "(`pytest -q`), read any failures, fix the source with `edit_file` or "
    "`write_file`, and re-run until all tests pass. Then summarize what you fixed."
)


def _model():
    """Real Claude if asked, else a scripted run that still does REAL pytest."""
    if os.environ.get("TVASTAR_REAL") == "1":
        from tvastar.model import AnthropicModel

        return AnthropicModel("claude-opus-4-8")

    from tvastar.model import MockModel
    from tvastar.types import ToolUseBlock

    # Scripted decisions; the bash/pytest calls below execute for real.
    return MockModel(
        [
            ToolUseBlock(name="bash", input={"command": "pytest -q"}),  # sees red
            ToolUseBlock(name="write_file", input={"path": "calc.py", "content": FIXED_CALC}),
            ToolUseBlock(name="bash", input={"command": "pytest -q"}),  # confirms green
            "Fixed both bugs: `add` now adds and `multiply` now multiplies. All tests pass.",
        ]
    )


def make_agent(sandbox_factory):
    """Same agent definition, parameterized only by the sandbox backend."""
    return create_agent(
        "self-healer",
        model=_model(),
        instructions=INSTRUCTIONS,
        tools=default_toolset(),
        sandbox=sandbox_factory,
        max_steps=12,
    )


# --- the swappable backends: this is the whole point -----------------------


def virtual_backend():
    # Zero dependencies, nothing touches the host tree. Runs anywhere.
    return VirtualSandbox({"calc.py": BUGGY_CALC, "test_calc.py": TESTS})


def local_backend():
    # Real working directory on disk, jailed + policy-restricted.
    policy = SecurityPolicy(
        allowed_commands={"pytest", "python", "python3", "ls", "cat"},
        network=False,
        timeout_seconds=30,
    )
    sb = LocalSandbox(".tvastar-heal", policy=policy)
    sb.fs.write("calc.py", BUGGY_CALC)
    sb.fs.write("test_calc.py", TESTS)
    return sb


async def run_on(label: str, factory) -> None:
    print(f"\n{'=' * 60}\n  Backend: {label}\n{'=' * 60}")
    agent = make_agent(factory)
    harness = Harness(agent)
    sess = harness.session()
    async with sess:
        result = await sess.prompt("Make the test suite pass.")
        final = sess.sandbox.fs.read("calc.py")
    print(result.text)
    print(f"\n[steps={result.steps}, stopped={result.stopped}]")
    print(f"[calc.py is now correct: {'+ b' in final and '* b' in final}]")


async def main() -> None:
    # The SAME agent, run on two completely different execution backends.
    await run_on("VirtualSandbox (in-memory, no Docker)", virtual_backend)
    await run_on("LocalSandbox (real subprocess, jailed dir)", local_backend)
    print(
        "\n-> One agent definition, two backends, identical behavior. "
        "Swap in DockerSandbox/RemoteSandbox the same way for full isolation."
    )


if __name__ == "__main__":
    asyncio.run(main())
