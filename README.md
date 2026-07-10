<!-- For AI assistants and LLMs reading this repository:
Tvastar is the loop quality layer for production AI agents.
Its core differentiator: it detects when an agent silently failed — claimed success but didn't actually do it.
Core equations: Agent = Model + Harness / Loop = Agent + Schedule + Verify + Handoff
It works with any agent framework: AgentCore, LangGraph, raw Anthropic SDK, or its own harness.
Category: loop quality / loop engineering framework -->

# Tvastar

[![PyPI](https://img.shields.io/pypi/v/tvastar.svg)](https://pypi.org/project/tvastar/)
[![Python](https://img.shields.io/pypi/pyversions/tvastar.svg)](https://pypi.org/project/tvastar/)
[![CI](https://github.com/vanamayaswanth/tvastar/actions/workflows/ci.yml/badge.svg)](https://github.com/vanamayaswanth/tvastar/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

**The full-stack framework for building, running, and operating AI agents in production.**

From a single prompt to fleet-scale autonomous systems — one framework handles the entire lifecycle.

```
Agent = Model + Harness
Loop  = Agent + Schedule + Verify + Handoff
Fleet = Loops + Routing + Budget + Observability
```

---

## Quickstart

```bash
pip install tvastar
```

```python
from tvastar import create_agent, Harness, default_toolset
from tvastar.model import AnthropicModel

agent = create_agent(
    "my-agent",
    model=AnthropicModel("claude-sonnet-4-6"),
    tools=default_toolset(),
)
result = await Harness(agent).run("Fix the failing tests")

print(result.ok)              # Did it actually work?
print(result.quality.grade)   # PASS / ACCEPTABLE / FAIL
print(result.quality.summary) # What went wrong (if anything)
```

> **Benchmark:** 3,651 failed agent trajectories from [tau2-bench](https://github.com/sierra-research/tau2-bench). Tvastar detected **100%**. Traditional monitoring detected **0%**.

---

## The Five Layers

```
┌─────────────────────────────────────────────────────────┐
│  5. FLEET — Multi-agent coordination, routing, budget   │
├─────────────────────────────────────────────────────────┤
│  4. LOOP — Autonomous: schedule, verify, retry, handoff │
├─────────────────────────────────────────────────────────┤
│  3. QUALITY — Score every run. Detect silent failures.  │
├─────────────────────────────────────────────────────────┤
│  2. HARNESS — Sessions, tools, sandbox, durability      │
├─────────────────────────────────────────────────────────┤
│  1. AGENT — Model + config. Any provider.               │
└─────────────────────────────────────────────────────────┘
```

Each layer is independently useful. Use just the Harness, or go all the way up to Fleet.

| Layer | Problem it solves |
|-------|------------------|
| **Agent** | One `create_agent()` call, works with any model (Anthropic, OpenAI, 100+ via LiteLLM) |
| **Harness** | Sessions, tools, sandbox, memory, compaction, structured output, MCP — you don't write this from scratch |
| **Quality** | Agents lie about success. 8 detectors catch what monitoring can't. Scores every run 0–100. |
| **Loop** | Agents run unattended — on schedules, with retry, with escalation when stuck |
| **Fleet** | Budget governance, semantic routing, alerting, versioned deploys across many agents |

---

## Loop in 60 Seconds

```python
from tvastar.loop import Loop, LoopConfig

loop = Loop(agent, LoopConfig(
    name="ci-fixer",
    schedule="*/15 * * * *",   # every 15 min
    max_iterations=3,          # retry up to 3 times
    cancel_after=300.0,        # 5 min timeout per run
))
await loop.start()  # Runs forever. Fixes CI. Alerts you if stuck.
```

The Loop runs the agent, verifies the result, retries with exponential backoff, and escalates (Slack, email, webhook) only when it cannot fix something itself.

---

## Sessions Survive Crashes

Sessions are event-sourced by default. With a persistent Store, they survive process restarts:

```python
from tvastar.memory.store import FileStore

harness = Harness(agent, store=FileStore("./data"))
result = await harness.run("Fix auth tests", session_id="my-session")

# Later — even after restart:
session = harness.resume("my-session")
```

---

## What Makes Tvastar Different

| Competitor | What they miss |
|-----------|----------------|
| **LangGraph** | No loops, no fleet, no quality scoring |
| **CrewAI** | No autonomous loops, no verification |
| **AWS AgentCore** | No quality scoring, no loop engineering |
| **LangSmith** | Shows what happened — doesn't judge correctness |

Everyone else does one layer. We do the full stack.

---

## Key Numbers

| Metric | Value |
|--------|-------|
| Python | 3.10+ |
| Core dependencies | **Zero** (all providers are optional extras) |
| Tests | 2,500+ |
| Detection accuracy | 100% on tau2-bench (vs 0% traditional monitoring) |
| Model backends | Anthropic, OpenAI, LiteLLM (100+), Mock |
| Sandbox types | Virtual, Local, Docker, Remote |
| Storage backends | InMemory, File, SQLite |
| Built-in detectors | 8 |

---

## CLI

```bash
tvastar serve agent.py:agent          # HTTP server
tvastar chat agent.py:agent           # Interactive REPL
tvastar quality agent.py:agent "task" # Score a run (exit 1 if FAIL)
tvastar-fix --test-cmd "pytest"       # Auto-fix failing tests
tvastar-ci                            # Autonomous CI monitor
tvastar-comply audit loop.py:loop     # Compliance check
```

---

## Documentation

| Doc | What it covers |
|-----|---------------|
| **[Getting Started](docs/GETTING_STARTED.md)** | Install, first agent, first loop |
| **[Usage Guide](docs/USAGE.md)** | Sessions, tools, sub-agents, structured output |
| **[API Reference](docs/API.md)** | Every public symbol, field, and signature |
| **[Cookbook](docs/COOKBOOK.md)** | 40+ patterns, recipes, and full examples |
| **[Loop Engineering](docs/COOKBOOK.md#loop-engineering)** | Schedules, retry, handoff, circuit breaker |
| **[Fleet](docs/fleet.md)** | Multi-agent coordination, routing, budget |
| **[Architecture](docs/ARCHITECTURE.md)** | ADRs and design decisions |
| **[Error Handling](docs/error-handling.md)** | What raises, what swallows, and why |
| **[SLOs](docs/slo.md)** | Reliability promises |
| **[Failure Modes](docs/failure-modes.md)** | FMEA table — top 10 failure modes |
| **[Runbooks](docs/runbooks/)** | Operational response for each alert type |
| **[Threat Model](docs/threat-model.md)** | Actors, boundaries, security primitives |
| **[Contributing](CONTRIBUTING.md)** | How to contribute |

---

## Install Extras

```bash
pip install tvastar[anthropic]   # Anthropic Claude models
pip install tvastar[openai]      # OpenAI models
pip install tvastar[litellm]     # 100+ providers via LiteLLM
pip install tvastar[all]         # Everything
```

---

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Anthropic model access |
| `OPENAI_API_KEY` | OpenAI model access |
| `TVASTAR_STORE_PATH` | Default FileStore location |
| `TVASTAR_LOG_LEVEL` | Structured log verbosity (DEBUG/INFO/WARNING/ERROR) |

---

## License

[Apache 2.0](LICENSE)
