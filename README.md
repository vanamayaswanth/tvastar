# Tvastar

[![PyPI](https://img.shields.io/pypi/v/tvastar.svg)](https://pypi.org/project/tvastar/)
[![Python](https://img.shields.io/pypi/pyversions/tvastar.svg)](https://pypi.org/project/tvastar/)
[![CI](https://github.com/vanamayaswanth/tvastar/actions/workflows/ci.yml/badge.svg)](https://github.com/vanamayaswanth/tvastar/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Not another SDK. Build autonomous agents and powerful AI workflows.**

```bash
pip install tvastar
```

```python
import asyncio
from tvastar import create_agent, Harness, default_toolset
from tvastar.model import AnthropicModel

agent = create_agent(
    "assistant",
    model=AnthropicModel("claude-opus-4-6"),
    instructions="You are a helpful coding agent.",
    tools=default_toolset(),           # bash, read, write, edit, grep, glob
)
result = asyncio.run(Harness(agent).run("Write hello.py and run it."))
print(result.text)
```

No Docker. No containers. Zero core dependencies. Real code execution out of the box.

---

## Why Tvastar

Every junior AI developer hits the same wall. The Anthropic API tutorial works great — 50 lines, chatbot done. Then they try to add tools, and suddenly they're writing a while loop, parsing tool call blocks, handling errors, watching context blow up at 10k tokens, wondering why their agent loops forever. Nobody warned them about any of this.

Tvastar is the answer to that wall.

### The one-line difference

> Every other framework makes you learn the framework first.
> Tvastar makes you learn agents first.

`Agent = AgentSpec + Harness` — two things. Know what each one does, you understand the whole system.

### vs. LangGraph

LangGraph makes you think like a graph architect before you've built your first agent. Nodes, edges, state reducers, conditional routing. A junior dev spends two days learning LangGraph before writing a line of their actual product. In Tvastar, you write `harness.run("do something")` on day one and add complexity only when you need it.

### vs. LangChain

Abstraction on top of abstraction. When something breaks in LangChain, you're three layers deep trying to figure out which chain failed. The API changes every few months. Tvastar has no hidden magic — the loop is readable, the tools are plain Python functions, the retry is a dataclass.

### vs. Agno / Phidata

Packed with built-in opinions: memory, knowledge bases, built-in storage. Great if their opinions match yours. When they don't, you fight the framework. Tvastar is additive — start with zero opinions, add what you need.

### vs. CrewAI

Forces you to think in "roles" and "crews." A great metaphor for simple multi-agent demos, awkward for real production systems where your agents aren't playing characters.

### What you actually get

You already know Python functions. In Tvastar, a tool is just a function with `@tool`. An agent is just a spec with a model and some tools. A session is just a conversation thread. Everything else — retry, compaction, parallelism, streaming, workflows — is one line when you need it, invisible when you don't.

**The learning curve is the product.**

---

## How it works

```
create_agent(...)  →  AgentSpec          (what the agent is — immutable)
Harness(spec)      →  Harness            (how it runs — stateful)
harness.run(...)   →  RunResult          (one prompt, one answer)
harness.session()  →  Session            (multi-turn conversation)
```

Inside every `run()` or `prompt()`, the agent loop looks like this:

```
User message
    ↓
Model generates response
    ↓
  ┌─ stop_reason == TOOL_USE? ──────────────────────────────────┐
  │                                                             │
  │   Execute all requested tools (concurrently)               │
  │   Feed results back to model                               │
  │   Auto-compact context if policy threshold hit             │
  │   Checkpoint to durable store                              │
  │   Loop ────────────────────────────────────────────────────┘
  │
  └─ END_TURN → RunResult(.text, .messages, .usage, .steps, .data)
```

---

## Install

```bash
pip install tvastar                      # core only — zero deps
pip install "tvastar[anthropic]"         # + Claude models
pip install "tvastar[openai]"            # + OpenAI / Groq / Ollama / etc.
pip install "tvastar[serve]"             # + HTTP server (FastAPI)
pip install "tvastar[otel]"              # + OpenTelemetry tracing
pip install "tvastar[all]"              # everything
```

---

## Core concepts

| Thing | What it is |
|-------|-----------|
| `AgentSpec` | Immutable declaration: model + tools + instructions + policies |
| `Harness` | Stateful runtime: runs an AgentSpec across sessions |
| `Session` | One conversation thread with its own message history |
| `Tool` | A Python function the model can call (schema auto-derived) |
| `Skill` | A Markdown file of reusable expertise, loaded on demand |
| `Sandbox` | Where code runs — virtual (in-memory), local, or Docker |
| `RunResult` | What you get back: `.text`, `.data`, `.usage`, `.steps`, `.ok` |

---

## Models

### Anthropic (Claude)

```python
from tvastar.model import AnthropicModel

model = AnthropicModel("claude-opus-4-6")   # ANTHROPIC_API_KEY env var
model = AnthropicModel("claude-sonnet-4-6", api_key="sk-ant-...")
```

### OpenAI

```python
from tvastar.model import OpenAIModel

model = OpenAIModel("gpt-4o")               # OPENAI_API_KEY env var
```

### Any OpenAI-compatible provider (Groq, Ollama, Cloudflare, Together…)

```python
model = OpenAIModel(
    model="llama-3.1-8b-instant",
    base_url="https://api.groq.com/openai/v1",
    api_key="gsk_...",
)

# Local Ollama — completely free, no API key
model = OpenAIModel(model="llama3.2", base_url="http://localhost:11434/v1", api_key="ollama")
```

### Extended thinking (reasoning models)

```python
agent = create_agent(..., thinking_level="high")
# Anthropic: budget_tokens=16000  (low=1024, medium=8000, high=16000)
# OpenAI:    reasoning_effort='high'
```

### Mock (tests / offline dev)

```python
from tvastar.model import MockModel
from tvastar.types import ToolUseBlock

model = MockModel(["Hello!", ToolUseBlock(name="add", input={"a":1,"b":2}), "Done."])
```

### Custom provider

```python
from tvastar.model import Model
from tvastar.types import Message, ModelResponse, StopReason, TextBlock

class MyModel(Model):
    name = "my-provider"
    async def generate(self, messages, *, system=None, tools=None,
                       max_tokens=4096, temperature=1.0,
                       stop_sequences=None, thinking_level=None) -> ModelResponse:
        text = await my_api_call(messages)
        return ModelResponse(
            message=Message("assistant", [TextBlock(text=text)]),
            stop_reason=StopReason.END_TURN,
        )
```

---

## Tools

```python
from tvastar import tool, ToolRetryPolicy

@tool
def add(a: int, b: int) -> int:
    "Add two integers."
    return a + b

# With retry (for flaky network calls)
@tool(retry=ToolRetryPolicy(max_attempts=3, backoff_base=0.5))
async def call_api(url: str) -> str:
    "Fetch a URL."
    ...

# Access session context (sandbox, filesystem, memory)
@tool
async def save(path: str, content: str, ctx: ToolContext) -> str:
    "Save a file."
    ctx.filesystem.write(path, content)
    return "saved"
```

Built-in tools via `default_toolset()`: `bash`, `read_file`, `write_file`, `edit_file`, `grep`, `glob`, `list_files`.

**Harness-wide retry** — applies to all tools that don't have their own policy:

```python
agent = create_agent(..., tool_retry=ToolRetryPolicy(max_attempts=3))
```

---

## Sessions

```python
harness = Harness(agent)

# One-shot
result = await harness.run("Summarise this document.")

# Multi-turn (stateful)
sess = harness.session()
async with sess:
    await sess.prompt("Read report.txt")
    await sess.prompt("Now write a 3-bullet summary")
    result = await sess.prompt("Translate the summary to Spanish")

# Named sessions (for parallel branches)
branch_a = harness.session("review-api")
branch_b = harness.session("review-auth")
results = await asyncio.gather(
    branch_a.prompt("Review the API layer"),
    branch_b.prompt("Review the auth layer"),
)
```

---

## Structured output

Get back a typed object instead of raw text:

```python
from pydantic import BaseModel

class Report(BaseModel):
    summary: str
    issues: list[str]
    severity: str

result = await sess.prompt("Analyse this code and return a report.", result=Report)
report: Report = result.data          # validated Pydantic instance
print(report.severity)
```

Works with Pydantic v2, Pydantic v1, dataclasses, plain `dict`, or any callable validator.

---

## Delegating to specialist sub-agents

Define named specialist profiles, then delegate tasks to them:

```python
from tvastar import create_agent, define_agent_profile

reviewer = define_agent_profile(
    name="reviewer",
    description="Reviews code for security and correctness.",
    instructions="Report only issues with a reproducible failure scenario.",
    thinking_level="high",
    max_steps=10,
)

agent = create_agent(
    "coordinator",
    model=model,
    subagents=[reviewer],
    tools=default_toolset(),
)

sess = harness.session()
async with sess:
    result = await sess.task(
        "Review the auth package for security issues.",
        agent="reviewer",          # runs in isolated child session
        cancel_after=60.0,         # timeout in seconds
        result=ReviewReport,       # structured output
    )
```

Task delegation is capped at **4 levels deep** (`MAX_TASK_DEPTH`) to prevent runaway recursion.

---

## Parallel fan-out

Run multiple prompts concurrently with one call:

```python
results = await harness.fan_out([
    "Summarise chapter 1",
    "Summarise chapter 2",
    {
        "prompt": "Summarise chapter 3",
        "agent": "summariser",       # use a specialist profile
        "cancel_after": 30.0,
        "result": SummarySchema,
    },
], concurrency=4)                    # optional semaphore cap

for r in results:
    print(r.text)
```

---

## Workflows — durable, inspectable operations

Wrap multi-step agent work with a run ID, event log, and persistent history:

```python
from tvastar import workflow
from tvastar.workflow import WorkflowContext

@workflow
async def summarise_document(ctx: WorkflowContext) -> dict:
    ctx.log.info("Starting summarisation", doc=ctx.payload["path"])
    harness = await ctx.init(agent)
    sess = await harness.session()
    result = await sess.prompt(f"Summarise {ctx.payload['path']}")
    return {"summary": result.text, "steps": result.steps}

# Run it
run = await summarise_document.run({"path": "report.pdf"})
print(run.run_id)       # 'run_a3f9b2...'
print(run.status)       # RunStatus.COMPLETED
print(run.output)       # {'summary': '...', 'steps': 3}

# Inspect history
for past_run in summarise_document.list_runs():
    print(past_run.run_id, past_run.status, past_run.started_at)
```

Persist across restarts with a file-backed registry:

```python
from tvastar.workflow import RunRegistry
registry = RunRegistry.file_backed(".tvastar-runs")

@workflow(registry=registry)
async def my_flow(ctx): ...
```

---

## Event-driven / async dispatch

For chat bots, webhooks, and queue processors — respond immediately, run the agent in the background:

```python
from tvastar import dispatch, dispatch_and_wait, observe_dispatch, DispatchInput

# Fire and forget — returns a dispatch_id, agent runs in background
dispatch_id = await dispatch(
    agent,
    id="user_123",                          # identifies the conversation thread
    input=DispatchInput(text=message_text, type="chat.message"),
    on_complete=lambda r: send_reply(r.text),
    on_error=lambda e: send_error(str(e)),
    cancel_after=30.0,
)

# Fire and await (when you need the result in the same context)
result = await dispatch_and_wait(agent, id="job_456", text="Process this report.")

# Watch all dispatches globally (for logging, metrics, etc.)
observe_dispatch(lambda event: logger.info(event.type, extra=event.data))
```

Agents with the same `id` share a Harness — conversation history accumulates naturally across multiple dispatches.

---

## Context compaction

Prevent context window exhaustion in long-running sessions:

```python
from tvastar import CompactionPolicy

agent = create_agent(
    "long-runner",
    model=model,
    compaction=CompactionPolicy(
        max_messages=40,    # compact when history exceeds 40 messages
        keep_last=10,       # always keep the 10 most recent messages
        min_messages=20,    # don't compact below this floor
    ),
)
# Compaction fires automatically after tool turns — the model never notices.
```

Manual compaction:

```python
from tvastar import compact_session
await compact_session(session, force=True)
```

---

## Skills

Skills are reusable agent expertise defined in Markdown:

```markdown
<!-- skills/code-reviewer.md -->
---
name: code-reviewer
description: Review a diff for bugs and style
tools: [read_file, grep]
---

You are a meticulous code reviewer. Inspect changed files carefully.
Report only concrete, actionable issues with file+line references.
```

```python
from tvastar import SkillLibrary

agent = create_agent("dev", model=model, skills=SkillLibrary.from_dirs("skills/"))

async with sess:
    result = await sess.skill("code-reviewer", "Review changes in src/auth/")
```

---

## Application-level file access

Stage files before the agent runs, collect outputs after — without going through the model's tool layer:

```python
async with Harness(agent) as h:
    # Write inputs
    await h.fs.write_file("report.pdf", pdf_bytes)
    await h.fs.write_file("instructions.txt", "Summarise the PDF.")

    # Run agent
    result = await h.run("Follow instructions.txt")

    # Read outputs
    summary = await h.fs.read_file("summary.md")
    files = await h.fs.list_dir()
```

---

## Sandboxes

```python
from tvastar import VirtualSandbox, LocalSandbox, SecurityPolicy

# Default — in-memory, zero deps, near-zero overhead
create_agent(..., sandbox=VirtualSandbox)

# Real bash, jailed to a directory
policy = SecurityPolicy(allowed_commands={"python", "pytest", "ls"}, network=False)
create_agent(..., sandbox=lambda: LocalSandbox("./workspace", policy=policy))
```

---

## MCP — use any published tool server

```python
from tvastar import connect_mcp_server, default_toolset

# Spawn a local server
client = await connect_mcp_server(command="python", args=["my_mcp_server.py"])

# Or connect to a remote one
client = await connect_mcp_server(
    url="https://api.example.com/mcp",
    headers={"Authorization": "Bearer sk-..."},
)

agent = create_agent("a", model=model, tools=[*default_toolset(), *client.tools])
# ...
await client.close()
```

---

## Durable execution — survive crashes

```python
from tvastar import Harness, FileStore

harness = Harness(agent, store=FileStore(".tvastar-state"))
# Checkpoints transcript + filesystem after every tool turn

# On restart — pick up where you left off
sess = harness.resume("sess_abc123") or harness.session()
```

---

## Serving over HTTP

```bash
pip install "tvastar[serve]"
tvastar serve my_agent.py:agent --port 8000
```

Endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Agent info |
| `POST` | `/sessions` | Create session |
| `POST` | `/sessions/{id}/prompt` | Send a message |
| `WS` | `/sessions/{id}/stream` | WebSocket streaming |
| `GET` | `/sessions/{id}/stream?text=...` | SSE streaming (browser-friendly) |

SSE example — stream directly in the browser or with curl:

```bash
curl -N "http://localhost:8000/sessions/sess_abc/stream?text=Hello"
# data: {"type": "text_delta", "data": {"text": "Hello"}}
# data: {"type": "turn_end", "data": {"text": "Hello there!"}}
# data: [DONE]
```

---

## Observability and tracing

```python
from tvastar import Tracer, ConsoleExporter, JSONLExporter

harness = Harness(agent, tracer=Tracer([
    ConsoleExporter(),                  # human-readable to stderr
    JSONLExporter("trace.jsonl"),       # machine-readable log
]))
```

OpenTelemetry (Braintrust, Honeycomb, Datadog, Sentry, etc.):

```bash
pip install "tvastar[otel]"
```

```python
from tvastar import OTelExporter
harness = Harness(agent, tracer=Tracer([OTelExporter()]))
```

---

## Silent-failure detection

Agents can silently do the wrong thing — claim success over a failing run, loop forever, call a tool with bad arguments. Tvastar detects these automatically:

```python
result = await harness.run("Fix all test failures.")

if not result.ok:                       # end_turn AND no warnings/errors
    for finding in result.warnings:
        print(f"[{finding.severity}] {finding.detector}: {finding.message}")
# → [WARNING] unverified_completion: model claimed success but last tool result shows failures
```

Built-in detectors: `unknown_tool`, `schema_mismatch`, `thrash_loop`, `ignored_tool_error`, `unverified_completion`, `empty_answer`, `step_limit`.

Write your own:

```python
from tvastar.detect import Finding, Severity

def slow_run(ctx):
    if ctx.stopped == "max_steps":
        return [Finding("slow_run", Severity.WARNING, "hit the step ceiling")]
    return []

create_agent(..., detect=[*default_detectors(), slow_run])
```

---

## CLI

```bash
tvastar run   my_agent.py:agent "Write hello.py and run it"
tvastar chat  my_agent.py:agent          # interactive REPL
tvastar serve my_agent.py:agent          # HTTP + WebSocket server
tvastar info  my_agent.py:agent          # print config
tvastar logs  run_abc123                 # inspect a workflow run
```

---

## `tvastar-fix` — auto-fix failing tests

A real product built on Tvastar. An agent reads your test failures, edits the code, and iterates — then Tvastar **re-runs the suite itself** and only reports success on the real exit code. The agent can't lie.

```bash
pip install tvastar
export GROQ_API_KEY=...       # free tier works; or use local Ollama

tvastar-fix                   # auto-detects test framework, fixes, verifies
tvastar-fix --check           # CI mode — exit 1 if still failing
```

**GitHub Action:**

```yaml
- uses: vanamayaswanth/tvastar/action@v0.2.0
  with:
    test-command: "pytest -q"
    groq-api-key: ${{ secrets.GROQ_API_KEY }}
```

---

## Deploy anywhere

```python
from tvastar.deploy import asgi_app, lambda_handler, serverless_handler
from my_agent import agent

app     = asgi_app(agent)           # FastAPI / Starlette — Fly, Render, Cloud Run
handler = lambda_handler(agent)     # AWS Lambda + API Gateway
fn      = serverless_handler(agent) # GCP / Azure / Vercel
```

---

## Project layout

```
tvastar/
  types.py          Core dataclasses — Message, ToolUse, ModelResponse, ...
  agent.py          AgentSpec + create_agent()
  harness.py        Harness + HarnessFS + fan_out()
  session.py        Session + RunResult + the agent loop
  model/            Model ABC + Anthropic / OpenAI / Mock adapters
  tools/            @tool, ToolRegistry, ToolRetryPolicy, default_toolset()
  skills/           Markdown skill loader
  sandbox/          VirtualSandbox / LocalSandbox / external adapters
  memory/           InMemoryStore / FileStore / Memory (scoped KV)
  profiles.py       AgentProfile, define_agent_profile(), MAX_TASK_DEPTH
  workflow.py       @workflow, WorkflowContext, WorkflowRun, RunRegistry
  dispatch.py       dispatch(), dispatch_and_wait(), observe_dispatch()
  compaction.py     CompactionPolicy, compact_session()
  durable.py        Checkpointer (checkpoint / resume)
  observability.py  Tracer, Span, exporters
  detect/           Silent-failure detectors
  mcp/              MCP client (stdio + HTTP transports)
  serving/          HTTP/WebSocket/SSE server + CLI
  deploy/           ASGI / Lambda / GitHub Actions adapters
  fix/              tvastar-fix application
```

---

## Full API reference

See [docs/API.md](docs/API.md) for every public function, class, and field with full type signatures.

See [docs/PATTERNS.md](docs/PATTERNS.md) for copy-paste recipes.

See [CLAUDE.md](CLAUDE.md) for the AI-optimised codebase map (module contracts, data flow, dependency graph).

---

## Testing

```bash
pip install "tvastar[dev]"
pytest
```

---

## License

MIT — [vanamayaswanth/tvastar](https://github.com/vanamayaswanth/tvastar)
