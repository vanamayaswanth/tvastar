# Getting started with Tvastar

Tvastar is a Python harness for agents that use tools and act on real systems. Start with a one-shot harness run, then make a concrete task acceptable only when its named check passes.

## Requirements

- Python **3.11+**
- A model provider for real-model runs, or `MockModel` for offline tests

Install the core package and the provider you plan to use:

```bash
pip install "tvastar[anthropic]"
export ANTHROPIC_API_KEY="..."
```

Other options include `OPENAI_API_KEY`, `GROQ_API_KEY`, a running local Ollama server, or an OpenAI-compatible endpoint. The core package has no runtime dependencies; provider integrations are optional.

Verify the install:

```bash
python -c "import tvastar; print(tvastar.__version__)"
```

## First harness run

Save this as `hello.py`:

```python
import asyncio

from tvastar import Harness, create_agent
from tvastar.model import AnthropicModel

agent = create_agent(
    "greeter",
    model=AnthropicModel("claude-haiku-4-5-20251001"),
    instructions="You are a concise, friendly assistant.",
)

async def main() -> None:
    result = await Harness(agent).run("What is the capital of France?")
    print(result.text)
    print(result.ok)

asyncio.run(main())
```

Run it:

```bash
python hello.py
```

The basic shape is always the same: `create_agent()` declares the agent, `Harness` supplies the runtime, and `run()` executes one task. `result.ok` is the runtime/quality outcome, not a proof that an arbitrary real-world task is correct.

## First verified action: CI repair

The fastest way to understand Tvastar's acceptance contract is to use its reference workflow in a project with a failing test suite:

```bash
tvastar-fix --path . --test-cmd "pytest -q" --check
```

The workflow is deliberately simple:

1. Tvastar runs the named test command to establish that it is failing.
2. The agent reads the failure and may edit files in the selected project directory.
3. Tvastar reruns the **same** test command itself.
4. The result is `already-green`, `fixed`, or `unfixed`; `--check` exits non-zero if the command is still failing.

The agent's final text does not decide success. The rerun test command does. Review the produced diff before committing it; `tvastar-fix` does not push changes or open a pull request.

If no model can be resolved, run `tvastar-fix --help`. It can use `GROQ_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, a running Ollama server, or an explicit `--model` / `--base-url` / `--api-key` combination.

For a fully inspectable offline demo, run:

```bash
python examples/self_healing_agent.py
```

That example executes real `pytest` commands while using scripted model decisions by default. Set `TVASTAR_REAL=1` and configure Anthropic to use a real model.

## Add a tool

Tools are typed Python functions that the model may call.

```python
import asyncio

from tvastar import Harness, create_agent
from tvastar.model import AnthropicModel
from tvastar.tools.base import tool

@tool
def get_weather(city: str) -> str:
    """Return the current weather for a city."""
    return f"Sunny, 22°C in {city}"

agent = create_agent(
    "weather",
    model=AnthropicModel("claude-haiku-4-5-20251001"),
    instructions="Answer weather questions. Always use get_weather.",
    tools=[get_weather],
)

async def main() -> None:
    result = await Harness(agent).run("What is the weather in Tokyo?")
    print(result.text)

asyncio.run(main())
```

For a coding agent, use `default_toolset()` and choose an execution boundary appropriate to the task.

## Persist a session across restarts

`InMemoryStore` is the default and lasts only for the current process. Use a persistent store when restart recovery matters:

```python
from tvastar import Harness
from tvastar.memory.store import FileStore

harness = Harness(agent, store=FileStore(".tvastar-state"))
await harness.run("Review the failing tests.", session_id="repair-42")

# In a later process:
resumed = harness.resume("repair-42")
if resumed:
    result = await resumed.prompt("Continue from the last completed step.")
```

Tvastar records event-sourced session history. Recovery is limited by the configured store's last successful write; it is not guaranteed by an in-memory session.

## Choose a sandbox safely

`create_agent()` defaults to `VirtualSandbox`, which is convenient for unit tests and trusted development. It is **not** an operating-system isolation boundary.

`LocalSandbox` rejects absolute `cwd` values and resolves relative `cwd` values beneath its configured root. That contains the process's starting directory, not the process itself: commands still run on the host and can escape the workspace through normal host capabilities. For untrusted model-generated code or meaningful side effects, use a `LocalSandbox` with a restrictive `SecurityPolicy` only as a control layer, or choose a container/remote sandbox when isolation is required:

```python
from tvastar import LocalSandbox, SecurityPolicy, create_agent

policy = SecurityPolicy(
    allowed_commands={"pytest", "python"},
    network=False,
    timeout_seconds=30,
)
agent = create_agent(
    "bounded-coder",
    model=model,
    tools=tools,
    sandbox=lambda: LocalSandbox("./workspace", policy=policy),
)
```

Read the [Threat Model](threat-model.md) before connecting untrusted inputs, external MCP tools, or production credentials.

## Next steps

| Goal | Read |
|---|---|
| Understand each system layer and when to use it | [Architecture Map](ARCHITECTURE_MAP.md) |
| Decide between a harness, session, workflow, dispatch, or loop | [Usage Guide](USAGE.md) |
| Copy a focused working recipe or explore a capability | [Cookbook](COOKBOOK.md) |
| Look up a signature | [API Reference](API.md) |
| Run recurring verified work | [Loop and verification guidance](COOKBOOK.md#loop-engineering) |
| Understand detector evidence and its limits | [Benchmarks](BENCHMARKS.md) |
| Inspect runnable demonstrations | [Examples](../examples/README.md) |
