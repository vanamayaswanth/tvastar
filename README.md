# Tvastar

**Build code-executing AI agents that run anywhere — no Docker required.**

> `Agent = Model + Harness`

Tvastar gives a language model everything it needs to do real, autonomous
work — tools, skills, memory, and a safe place to run code — and gets out of
your way. You describe the agent; Tvastar runs the loop.

Here's the whole idea in ten lines:

```python
import asyncio
from tvastar import create_agent, Harness, default_toolset
from tvastar.model import MockModel  # swap for a real model when you have a key

agent = create_agent(
    "assistant",
    model=MockModel(),                 # runs offline, no API key
    instructions="You are a helpful coding agent.",
    tools=default_toolset(),           # bash, read/write/edit, grep, glob
)
print(asyncio.run(Harness(agent).run("Create hello.py and run it.")))
```

### Why you might like it

- 🏖️ **Run code with no setup.** Agents can write *and run real code* in an
  in-memory sandbox using the Python you already have — **no Docker, no
  containers, nothing to install.** Want stronger isolation later? Switch to a
  local, Docker, or remote sandbox by changing **one line**.
- 🪶 **Tiny and fast.** The core has **zero third-party dependencies** and
  installs in about a second. Model providers and the web server are optional
  extras you add only if you want them.
- ♻️ **It remembers.** The conversation *and* the files are saved after every
  step, so a long-running agent can survive a crash and pick up where it left off.
- 🔌 **Swap any piece.** Model, sandbox, storage, and tracing are all
  pluggable — your agent code never changes.
- 🌐 **Talks to the MCP ecosystem.** Connect to any Model Context Protocol
  server — local or remote — and its tools just show up as your agent's tools.
- 🕵️ **Catches silent failures.** Tvastar notices when an agent *says* it
  succeeded but didn't (e.g. "all tests pass" over a failing run).
- 🚀 **Deploys anywhere.** The same agent runs as a web service, an AWS Lambda,
  a GitHub Action, a container, or any serverless function.
- 🛠️ **Ships a real app:** [`tvastar-fix`](#-tvastar-fix--auto-fix-failing-tests),
  a command + GitHub Action that auto-fixes your failing tests.

Want to see something fun? Watch an agent fix its own failing tests:

```bash
uv run python examples/self_healing_agent.py
```

---

## When should I use Tvastar?

Tvastar is a good fit when you want an agent that **does things** — runs code,
edits files, calls tools — not just chats. Reach for it when you value a small,
readable dependency-light core you can actually understand, want to run
code-executing agents **without standing up Docker**, or need crash-safe,
resumable runs.

It's probably **not** what you want if you only need a single chat completion
(call the model SDK directly), or if you need a large prebuilt ecosystem of
integrations and a managed platform today.

| If you want… | Tvastar's take |
|---|---|
| Run agent-written code with **no container/setup** | In-memory sandbox runs real Python out of the box; swap to Docker/remote with one line |
| A **tiny, auditable** core | Zero third-party deps in the core; everything else is an optional extra |
| **Pick any model** | Anthropic, OpenAI, or any OpenAI-compatible endpoint (Groq, Ollama, Cloudflare…) — often no new code |
| **Long-running / unattended** agents | Transcript + filesystem checkpointed every step; resume after a crash |
| Catch **silent failures** | Built-in detectors flag "claimed success but didn't," bad tool args, loops |
| **Deploy the same agent anywhere** | One definition → web service, Lambda, GitHub Action, container, FaaS |
| Use the **MCP** tool ecosystem | Built-in client for local stdio and remote HTTP MCP servers |

## Why a "harness," not an SDK?

Early LLM apps were a single API call wrapped around a chatbot. Modern agents
are different: you give them a **goal**, not step-by-step instructions, and they
figure out how to reach it using the tools and environment you provide. The
harness is everything around the model that makes that autonomy possible:

```
┌─────────────────────────────────────────┐
│  Harness   skills · memory · sessions    │
│ ┌───────────────────────────────────────┤
│ │ Model    tokens · tools · prompts      │
│ └───────────────────────────────────────┤
│  Sandbox   bash · security · networking  │
│  Filesystem  read · write · grep · glob  │
└─────────────────────────────────────────┘
```

## Install

Tvastar uses [uv](https://docs.astral.sh/uv/).

```bash
uv venv
uv pip install -e .            # core only, zero deps
uv pip install -e ".[anthropic]"   # + Claude
uv pip install -e ".[openai]"      # + OpenAI / OpenAI-compatible providers
uv pip install -e ".[serve]"       # + HTTP/WebSocket server
uv pip install -e ".[otel]"        # + OpenTelemetry tracing export
uv pip install -e ".[all,dev]"     # everything + test tooling
```

> The core has **no third-party dependencies**. Provider SDKs (`anthropic`,
> `openai`), the web server (`serve`), and OpenTelemetry (`otel`) are optional
> extras — imported lazily, so the import only fails if you actually use a
> feature whose extra isn't installed. (That "import could not be resolved"
> squiggle in your editor just means the optional package isn't in your venv.)

## Quick start

```python
import asyncio
from tvastar import create_agent, Harness, default_toolset, tool
from tvastar.model import MockModel  # swap for AnthropicModel(...) with a key

@tool
def add(a: int, b: int) -> int:
    "Add two numbers."
    return a + b

agent = create_agent(
    "assistant",
    model=MockModel(),                 # offline; no API key needed
    instructions="You are a helpful coding agent.",
    tools=[*default_toolset(), add],   # bash/read/write/edit/grep/glob + yours
)

harness = Harness(agent)
result = asyncio.run(harness.run("Create hello.py that prints hi, then run it."))
print(result.text)
```

### With a real model

```python
from tvastar.model import AnthropicModel
agent = create_agent("dev", model=AnthropicModel("claude-opus-4-8"), tools=default_toolset())
```

Set `ANTHROPIC_API_KEY` in your environment (or pass `api_key=`).

### Other providers (Cloudflare Workers AI, Groq, Ollama, …)

The `Model` interface is the single extension point. Two ways to use a provider
that isn't built in:

**1. OpenAI-compatible endpoint (easiest).** Cloudflare Workers AI, Groq,
Together, Fireworks, OpenRouter, Ollama, and vLLM all speak the OpenAI API —
just point the built-in `OpenAIModel` at their `base_url` (tool calling works on
models that support it):

```python
from tvastar.model import OpenAIModel

# Cloudflare Workers AI
model = OpenAIModel(
    model="@cf/meta/llama-3.1-8b-instruct",
    base_url=f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1",
    api_key=CF_API_TOKEN,
)

# Groq / Ollama / others — same pattern, different base_url:
OpenAIModel(model="llama-3.1-8b-instant", base_url="https://api.groq.com/openai/v1", api_key=...)
OpenAIModel(model="llama3.2", base_url="http://localhost:11434/v1", api_key="ollama")
```

**2. A custom `Model` subclass (works for *any* HTTP API).** Subclass `Model`
and implement `generate()`. See [examples/custom_provider.py](examples/custom_provider.py)
for a complete, zero-dependency native Cloudflare Workers AI adapter:

```python
from tvastar.model import Model
from tvastar import Message, ModelResponse
from tvastar.types import StopReason, TextBlock

class MyProvider(Model):
    name = "my-provider"
    async def generate(self, messages, *, system=None, tools=None,
                       max_tokens=4096, temperature=1.0, stop_sequences=None):
        text = await call_my_api(messages, system)        # your HTTP call
        return ModelResponse(Message("assistant", [TextBlock(text=text)]),
                             stop_reason=StopReason.END_TURN)
```

## Core concepts

| Concept       | What it is                                                        |
|---------------|-------------------------------------------------------------------|
| **Model**     | Provider-agnostic interface → Anthropic / OpenAI / Mock adapters. |
| **Tool**      | A typed Python function (`@tool`); JSON schema is auto-derived.    |
| **Skill**     | A Markdown file (frontmatter + instructions) loaded on demand.    |
| **Sandbox**   | Where bash runs. Virtual (in-memory), Local (subprocess), or external (Docker / E2B / Daytona). |
| **Session**   | One stateful conversation; runs the model↔tool loop.              |
| **Harness**   | Manages models, sessions, memory, durability, tracing.            |
| **Memory**    | Namespaced KV store (in-memory or JSON-on-disk).                  |

## Skills

Skills are reusable expertise packages — a Markdown file with a bit of
frontmatter that the agent loads on demand:

```markdown
---
name: code-reviewer
description: Review a diff for bugs and style issues
tools: [read_file, grep]
---

You are a meticulous code reviewer. Read the changed files, then report
concrete, actionable issues grouped by severity.
```

```python
from tvastar import SkillLibrary
agent = create_agent("dev", model=m, skills=SkillLibrary.from_dirs("skills/"))
# later, in a session:
await session.skill("code-reviewer", "Review the changes in src/")
```

## Sandboxes are pluggable

```python
from tvastar import VirtualSandbox, LocalSandbox, SecurityPolicy
from tvastar.sandbox import DockerSandbox, RemoteSandbox  # external providers

# in-memory, near-zero overhead (default)
create_agent("a", model=m, sandbox=VirtualSandbox)

# real bash, jailed to a dir, with an allowlist
policy = SecurityPolicy(allowed_commands={"python", "ls", "cat"}, network=False)
create_agent("a", model=m, sandbox=lambda: LocalSandbox("work", policy=policy))

# container isolation via the docker CLI
create_agent("a", model=m, sandbox=lambda: DockerSandbox("python:3.12-slim"))

# any external provider (E2B, Daytona, Modal, ...) via a ~20-line client shim
create_agent("a", model=m, sandbox=lambda: RemoteSandbox(MyProviderClient()))
```

## MCP — use the whole tool ecosystem

Connect an agent to any [Model Context Protocol](https://modelcontextprotocol.io)
server and its tools become native Tvastar tools — indistinguishable from ones you
wrote yourself. Works with **local stdio servers** and **remote HTTP servers**.

```python
from tvastar import create_agent, connect_mcp_server, default_toolset

# Local stdio server (Tvastar spawns it as a subprocess):
client = await connect_mcp_server(command="python", args=["my_server.py"])

# ...or a remote HTTP server with auth:
client = await connect_mcp_server(url="https://example.com/mcp",
                                  headers={"Authorization": "Bearer …"})

agent = create_agent("a", model=m, tools=[*default_toolset(), *client.tools])
# ... run the agent ...
await client.close()
```

Try it against a real (pure-stdlib) MCP server:

```bash
uv run python examples/mcp_agent.py
```

## Deploy anywhere

Write the agent once; pick an entrypoint per platform.

```python
from tvastar.deploy import asgi_app, lambda_handler, serverless_handler, run_github_action
from my_agent import agent

app = asgi_app(agent)                 # Render / Fly / Railway / Cloud Run / CF Python Workers
handler = lambda_handler(agent)       # AWS Lambda + API Gateway
fn = serverless_handler(agent)        # GCP/Azure/Vercel functions: fn({"prompt": "..."})
# GitHub Actions / GitLab CI: run_github_action(agent) reads INPUT_PROMPT, writes step outputs
```

Ready-to-use [`Dockerfile`](examples/deploy/Dockerfile) and
[GitHub Actions workflow](.github/workflows/agent.yml) are included.

## 🛠️ `tvastar-fix` — auto-fix failing tests

Tvastar ships a real, useful application built on itself: a command (and a
GitHub Action) that **fixes your failing test suite**. An agent reads the
failures, edits the source, and iterates — then Tvastar **re-runs the tests
itself** and reports success based on the real exit code, never the model's
word. (An agent that fixes tests is only trustworthy if it can't lie about it.)

```bash
pip install tvastar

# Pick a free model: Groq free tier, or local Ollama, or any provider key
export GROQ_API_KEY=...            # or run `ollama serve`

tvastar-fix                        # fixes ./ using `pytest -q`
tvastar-fix --test-cmd "pytest tests/ -q" --check   # CI gate
```

It auto-selects a model (Groq → OpenAI → Anthropic → local Ollama) or takes any
OpenAI-compatible endpoint via `--model/--base-url/--api-key`. It only touches
your code when the tests actually pass afterward.

**As a GitHub Action** — open a PR that fixes the build when CI goes red:

```yaml
- uses: vanamayaswanth/tvastar/action@v0.2.0
  with:
    test-command: "pytest -q"
    groq-api-key: ${{ secrets.GROQ_API_KEY }}
```

A complete PR-opening workflow is in
[examples/deploy/fix-tests-workflow.yml](examples/deploy/fix-tests-workflow.yml).

## Durable execution

The harness checkpoints the full transcript (and the virtual filesystem) after
every turn. If the process dies, resume exactly where you left off:

```python
from tvastar import Harness, FileStore
harness = Harness(agent, store=FileStore(".state"))   # survives restarts
sess = harness.resume("sess_abc123") or harness.session()
```

## Observability

```python
from tvastar import Harness, Tracer, ConsoleExporter, JSONLExporter
harness = Harness(agent, tracer=Tracer([ConsoleExporter(), JSONLExporter("trace.jsonl")]))
```

An `OTelExporter` bridges to OpenTelemetry when the SDK is installed
(`pip install tvastar[otel]`).

## Silent-failure detection

The hardest agent bugs are *silent*: the run raises no exception, looks
finished — but the agent quietly did the wrong thing (claimed "tests pass" over
a red run, called a tool with bad arguments, got stuck in a loop). Tvastar runs a
set of cheap, in-process detectors over every finished run and attaches what it
finds to `RunResult.findings` — **no extra infrastructure, no dependencies.**

```python
result = await harness.run("Make the tests pass.")

if not result.ok:                  # clean end_turn AND no warnings/errors
    for f in result.warnings:
        print(f)                   # [error] unverified_completion: claims success but last tool result shows failure
```

Built-in detectors (taxonomy informed by prior art in agent observability;
implementation original): `unknown_tool`, `schema_mismatch`, `thrash_loop`,
`ignored_tool_error`, `unverified_completion`, `empty_answer`, `step_limit`.

Tune or replace them per agent — `detect=True` (default), `detect=False`, or a
custom list:

```python
from tvastar.detect import default_detectors, thrash_loop
create_agent("a", model=m, detect=[thrash_loop])   # only this one
create_agent("a", model=m, detect=False)           # off (zero overhead)
```

Writing your own detector is a function from a `RunContext` to findings:

```python
from tvastar.detect import Finding, Severity

def slow_run(ctx):
    if ctx.stopped == "max_steps":
        return [Finding("slow_run", Severity.WARNING, "hit the step ceiling")]
    return []

create_agent("a", model=m, detect=[*default_detectors(), slow_run])
```

See it catch a lie:

```bash
uv run python examples/detect_silent_failure.py
```

## Serving

Expose an agent over HTTP + WebSocket (needs `[serve]`):

```bash
tvastar serve examples/coding_agent.py:agent --port 8000
```

Or the REPL:

```bash
tvastar chat examples/coding_agent.py:agent
```

## Project layout

```
tvastar/
  types.py          core dataclasses (Message, ToolUse, ...)
  model/            Model interface + Anthropic/OpenAI/Mock adapters
  tools/            @tool decorator, registry, schema gen, builtin tools
  filesystem/       read/write/grep/glob (local + virtual)
  sandbox/          base + virtual + local + external provider adapters
  skills/           Markdown skill loader
  memory/           KV stores + scoped Memory
  mcp/              Model Context Protocol client (stdio + HTTP transports)
  detect/           silent-failure detectors + mini JSON-schema validator
  durable.py        checkpoint/resume
  observability.py  tracing + exporters
  session.py        the agent loop
  harness.py        the top-level handle
  agent.py          create_agent / AgentSpec
  serving/          HTTP/WebSocket server + CLI
  deploy/           ASGI / Lambda / GitHub Actions / FaaS adapters
```

## Testing

```bash
uv run pytest
```

## License

MIT
