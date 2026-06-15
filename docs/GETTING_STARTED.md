# Getting Started with Tvastar

Five minutes from zero to a running loop. Follow this guide top-to-bottom — every step builds on the last.

---

## Step 0 — Install

```bash
pip install tvastar                 # core (zero dependencies)
pip install "tvastar[anthropic]"    # + Claude models
pip install "tvastar[openai]"       # + OpenAI / Groq / Ollama
pip install "tvastar[all]"          # everything
```

Verify the install:

```bash
python -c "import tvastar; print(tvastar.__version__)"
```

---

## Step 1 — Set your API key

```bash
# Claude (Anthropic)
export ANTHROPIC_API_KEY="sk-ant-..."

# OpenAI (or any compatible provider)
export OPENAI_API_KEY="sk-..."
```

Tvastar reads these from the environment automatically — you never pass them in code.

---

## Step 2 — Your first one-shot agent

Save this as `hello.py` and run it:

```python
import asyncio
from tvastar import create_agent, Harness
from tvastar.model.anthropic import AnthropicModel

spec = create_agent(
    "greeter",
    model=AnthropicModel("claude-haiku-4-5-20251001"),
    instructions="You are a friendly assistant.",
)

result = asyncio.run(Harness(spec).run("What is the capital of France?"))
print(result.text)    # Paris
print(result.ok)      # True
print(result.steps)   # 1 (no tools needed)
```

```bash
python hello.py
```

That's the minimal pattern: `create_agent` → `Harness` → `run`. Everything else is layered on top.

---

## Step 3 — Add a tool

Tools are Python functions the model can call. Tvastar auto-derives the JSON schema from the type hints.

```python
import asyncio
from tvastar import create_agent, Harness
from tvastar.tools.base import tool
from tvastar.model.anthropic import AnthropicModel

@tool
def get_weather(city: str) -> str:
    """Return current weather for a city."""
    # Replace with a real API call in production
    return f"Sunny, 22°C in {city}"

spec = create_agent(
    "weather-bot",
    model=AnthropicModel("claude-haiku-4-5-20251001"),
    instructions="Help users with weather questions. Always use get_weather.",
    tools=[get_weather],
)

result = asyncio.run(Harness(spec).run("What's the weather in Tokyo?"))
print(result.text)   # "It is sunny and 22°C in Tokyo."
print(result.steps)  # 2 (one turn for tool call, one for answer)
```

You can add as many tools as you need. The model decides which ones to call.

---

## Step 4 — Keep a conversation alive

`session()` creates a persistent conversation thread. Each `prompt()` builds on the history of the previous ones.

```python
import asyncio
from tvastar import create_agent, Harness
from tvastar.model.anthropic import AnthropicModel

spec = create_agent(
    "tutor",
    model=AnthropicModel("claude-sonnet-4-6"),
    instructions="You are a math tutor. Walk through problems step by step.",
)

async def main():
    sess = Harness(spec).session()

    r1 = await sess.prompt("What is 12 × 12?")
    print(r1.text)  # 144

    r2 = await sess.prompt("Now multiply that by 2.")
    print(r2.text)  # 288 — model remembers the previous answer

    r3 = await sess.prompt("Explain the pattern in one sentence.")
    print(r3.text)

asyncio.run(main())
```

---

## Step 5 — Get typed output

Instead of parsing text yourself, tell Tvastar what shape you want back:

```python
import asyncio
from tvastar import create_agent, Harness
from tvastar.model.anthropic import AnthropicModel
from pydantic import BaseModel

class CodeReview(BaseModel):
    summary: str
    issues: list[str]
    severity: str   # "low" | "medium" | "high"

spec = create_agent(
    "reviewer",
    model=AnthropicModel("claude-sonnet-4-6"),
    instructions="Review code and return structured findings.",
)

async def main():
    sess = Harness(spec).session()
    result = await sess.prompt(
        "Review this: def add(a, b): return a - b",
        result=CodeReview,
    )
    review: CodeReview = result.data
    print(review.severity)     # "medium"
    print(review.issues)       # ["Function subtracts instead of adding"]

asyncio.run(main())
```

The schema is injected into the prompt; Tvastar parses and validates the response automatically.

---

## Step 6 — Use the built-in tools

`default_toolset()` gives you bash, file read/write/edit, grep, and glob — everything a coding agent needs:

```python
import asyncio
from tvastar import create_agent, Harness, default_toolset
from tvastar.model.anthropic import AnthropicModel

spec = create_agent(
    "coder",
    model=AnthropicModel("claude-sonnet-4-6"),
    instructions="You are a Python expert. Fix bugs in the workspace.",
    tools=default_toolset(),
)

result = asyncio.run(Harness(spec).run("Write a hello.py that prints 'Hello, World!' and run it."))
print(result.text)
```

---

## Step 7 — Your first loop

A loop is an agent on a schedule. It runs automatically, retries on failure, and escalates to you only when it cannot fix something itself.

```python
import asyncio
from tvastar.loop.patterns import CISweeper
from tvastar.model.anthropic import AnthropicModel

loop = CISweeper(
    model=AnthropicModel("claude-sonnet-4-6"),
    schedule="*/15 * * * *",  # every 15 minutes
    cancel_after=300.0,         # fail-safe timeout
)

# Trigger once to test it
run = asyncio.run(loop.trigger())
print(run.state)        # LoopState.PASS or LoopState.FAIL
print(run.result_text)  # what the agent did
```

Check your loop's production readiness before deploying:

```bash
# Scaffold from a template
tvastar loop init CISweeper

# Score readiness (L0 → L3)
tvastar loop audit .tvastar/loops/ci_sweeper.py:loop

# Trigger once from CLI
tvastar loop run .tvastar/loops/ci_sweeper.py:loop
```

---

## Step 8 — Run with MockModel in tests

`MockModel` lets you write fast, deterministic unit tests with no API calls:

```python
import asyncio
import pytest
from tvastar import create_agent, Harness
from tvastar.model.mock import MockModel

def test_agent_returns_text():
    spec = create_agent(
        "assistant",
        model=MockModel(script=["Paris"]),
        instructions="Answer questions.",
    )
    result = asyncio.run(Harness(spec).run("Capital of France?"))
    assert result.text == "Paris"
    assert result.ok
```

`MockModel(script=["a", "b", "c"])` replays responses in order — one per model call.

---

## What's next?

| Goal | Where to look |
|------|--------------|
| Copy-paste recipes for every feature | [Patterns Cookbook](PATTERNS.md) |
| Full API reference | [API Reference](API.md) |
| Decision guide (which API to use when?) | [Usage Guide](USAGE.md) |
| All available patterns + when to use each | [Patterns Cookbook](PATTERNS.md) — Pattern Quick-Reference table |
| Production deployment | README → Deploy anywhere |
| Testing strategy | README → Testing |

---

## Common first-time issues

**`ImportError: No module named 'anthropic'`**
You installed the core package without extras. Run:
```bash
pip install "tvastar[anthropic]"
```

**`AuthenticationError` / `401`**
Your API key is not set or is wrong:
```bash
echo $ANTHROPIC_API_KEY   # should print your key
export ANTHROPIC_API_KEY="sk-ant-..."
```

**`result.ok` is `False` but no exception**
Check `result.stopped` and `result.findings`:
```python
print(result.stopped)   # "end_turn" | "max_steps" | "error"
for f in result.findings:
    print(f.severity, f.message)
```

**Model keeps calling the wrong tool**
Make the tool descriptions more specific. The model picks tools based on their docstring.

**Session context fills up**
Add a `CompactionPolicy`:
```python
from tvastar.compaction import CompactionPolicy
spec = create_agent(..., compaction=CompactionPolicy(max_messages=40, keep_last=10))
```
