# Welcome to Tvastar — Your First Day as Junior PM

*Read this in 15 minutes. After that, you'll understand what we build, who it's for, and why every folder exists.*

---

## The One-Sentence Pitch

**Tvastar is the full-stack framework for building, running, and operating AI agents in production.**

From a single prompt to fleet-scale autonomous systems — one framework handles the entire lifecycle.

---

## What We Actually Build (The Full Stack)

Tvastar is not one thing. It's five layers that work together:

```
┌─────────────────────────────────────────────────────────┐
│  5. FLEET ENGINEERING                                    │
│     Manage many agents as one system. Routing, budget,  │
│     versioned rollouts, shared state, alerting.         │
├─────────────────────────────────────────────────────────┤
│  4. LOOP ENGINEERING                                    │
│     Run agents autonomously. Schedule, verify, retry,   │
│     circuit breaker, exponential backoff, handoff.      │
├─────────────────────────────────────────────────────────┤
│  3. OBSERVABILITY & QUALITY                             │
│     Score every run 0-100. 8 failure detectors. Cost    │
│     tracking. Tracing. Audit trail. Compliance.         │
├─────────────────────────────────────────────────────────┤
│  2. HARNESS (Agent Runtime)                             │
│     Sessions, tools, sandbox, durability, compaction,   │
│     structured output, MCP, sub-agents, fan-out.       │
├─────────────────────────────────────────────────────────┤
│  1. AGENT DEFINITION                                    │
│     Agent = Model + Harness. Declarative spec.         │
│     Any model: Anthropic, OpenAI, LiteLLM (100+).     │
└─────────────────────────────────────────────────────────┘
```

**The core equations:**
```
Agent = Model + Harness
Loop  = Agent + Schedule + Verify + Handoff
Fleet = Loops + Routing + Budget + Observability
```

Each layer is valuable on its own. You can use just the Harness, or go all the way up to Fleet.

---

## The Problem Space (Not Just One Problem)

| Layer | Problem it solves | Why it matters |
|-------|------------------|----------------|
| **Agent definition** | Configuring an AI agent is boilerplate-heavy and fragile | One `create_agent()` call, works with any model |
| **Harness** | Running agents safely — tools, sandboxes, memory, compaction, durability | You shouldn't write session management from scratch every time |
| **Quality & detection** | Agents fail silently — say "done" when they didn't finish | Tvastar catches what traditional monitoring can't (100% vs 0% benchmark) |
| **Loop** | Agents need to run unattended — on schedules, with retry, with escalation | Without loops, someone babysits every run manually |
| **Fleet** | Managing 10+ agent loops is chaos without coordination | Budget governance, routing, alerting, versioned deploys |
| **Observability** | You can't improve what you can't see | Traces, cost, quality scores, compliance, audit trail |

---

## Our Differentiator (What Nobody Else Does)

**Silent failure detection** is the sharpest differentiator — the thing that no competitor does. But it's ONE feature in a complete platform.

The real differentiator at the platform level:

> **We are the only framework that covers Agent → Loop → Fleet → Quality as one integrated stack.**

| Competitor | What they cover | What they miss |
|-----------|----------------|----------------|
| LangGraph | Agent orchestration (graphs) | No loops, no fleet, no quality |
| CrewAI | Multi-agent collaboration | No autonomous loops, no verification |
| AgentCore | Agent hosting/deploy | No quality scoring, no loop engineering |
| LangSmith | Observability/tracing | Shows what happened, doesn't judge correctness |

Everyone else does one layer. We do the full stack.

---

## Who Uses This

| Person | What they want | Which layer they enter |
|--------|---------------|----------------------|
| **Developer building an AI feature** | "Give me tools, sandbox, sessions — I'll handle the rest" | Layer 2 (Harness) |
| **Developer who wants quality** | "Did my agent actually do the thing?" | Layer 3 (Quality) |
| **Platform engineer running agents in prod** | "Set it and forget it — alert me only when it fails" | Layer 4 (Loop) |
| **Team managing many agents** | "Coordinate budgets, routing, versioning across agents" | Layer 5 (Fleet) |
| **Compliance/security** | "Prove what the agent did, redact PII, regulatory audits" | Layer 3 (Assurance + Comply) |
| **Anyone using LangGraph/AgentCore** | "Add quality + loops to my existing framework" | Adapters (Layer 3 plugged into their stack) |

---

## The Product Layers (How Everything Connects)

Think of it as a stack. Each layer adds capability on top of the one below. **A user can enter at ANY layer** — they don't need to use all five:

```
┌────────────────────────────────────────────────────┐
│  5. FLEET ENGINEERING (fleet/)                     │
│     Multi-agent coordination. Routing, budget,     │
│     alerting, versioned rollouts, shared state.    │
├────────────────────────────────────────────────────┤
│  4. LOOP ENGINEERING (loop/)                       │
│     Autonomous operation. Schedule, verify, retry, │
│     circuit breaker, backoff, handoff.             │
├────────────────────────────────────────────────────┤
│  3. OBSERVABILITY & QUALITY                        │
│     detect/ — 8 failure detectors (core magic)     │
│     quality.py — score 0-100 every run             │
│     assurance/ — signed audit trail                │
│     comply/ — regulatory monitoring                │
│     observability.py — traces, spans               │
│     cost.py — budget tracking                      │
│     ui/ — local trace viewer                       │
├────────────────────────────────────────────────────┤
│  2. HARNESS — Agent Runtime (harness.py)           │
│     session.py — multi-turn conversations          │
│     tools/ — file, bash, web tools                 │
│     sandbox/ — safe code execution (4 types)       │
│     mcp/ — plug into any MCP tool server           │
│     conversation/ — event-sourced state            │
│     memory/ — durable persistence (3 backends)     │
│     compaction.py — context window management      │
│     skills/ — reusable prompt packages             │
│     approval.py — human-in-the-loop gates          │
├────────────────────────────────────────────────────┤
│  1. AGENT DEFINITION (agent.py)                    │
│     model/ — Anthropic, OpenAI, LiteLLM, Mock      │
│     AgentSpec — immutable config for one agent     │
│     create_agent() — the factory function          │
├────────────────────────────────────────────────────┤
│  INFRASTRUCTURE & DEPLOY                           │
│     deploy/ — Lambda, ASGI, GitHub Action           │
│     serving/ — HTTP server + CLI                    │
│     adapters/ — plug into LangGraph/AgentCore       │
│     filesystem/ — jailed file I/O                   │
└────────────────────────────────────────────────────┘
```

**Key insight:** Each layer is independently useful. A developer using just the Harness still gets sessions, tools, sandbox, compaction, and durability without ever touching Loops or Fleet. That's already more than most frameworks offer.

---

## Every Folder Explained (Plain Language)

### The Core Engine (what makes Tvastar work)

| Folder | What it does | Analogy |
|--------|-------------|---------|
| `session.py` | Manages a conversation between the agent and the model (multi-turn) | A phone call |
| `harness.py` | Wires everything together — creates sessions, manages sandboxes, detects failures | The pit crew for a race car |
| `agent.py` | The recipe card — says which model, tools, and rules to use | A job description |
| `detect/` | Inspects what the agent did and flags problems ("you said success but the test still fails") | Quality inspector on the factory floor |
| `quality.py` | Scores every run 0-100 with a letter grade (PASS/ACCEPTABLE/FAIL) | A teacher grading a paper |

### The Infrastructure (stuff that supports the core)

| Folder | What it does | Analogy |
|--------|-------------|---------|
| `model/` | Connects to AI models — Anthropic Claude, OpenAI, LiteLLM (100+ providers) | Different engines for the same car |
| `tools/` | Things the agent can do — read files, write files, run shell commands, search the web | A toolbox |
| `sandbox/` | Safe environment where agent-produced code runs (won't wreck your real computer) | A padded playroom |
| `memory/` | Where data is stored between runs (3 backends: RAM, file, SQLite) | A filing cabinet |
| `conversation/` | Records every event as a typed log (like a flight recorder) | The airplane black box |
| `filesystem/` | Restricted file access the agent can use (jailed — can't escape its workspace) | A sandbox for files |
| `mcp/` | Connects to external tool servers using the standard Model Context Protocol | USB ports for plugins |
| `skills/` | Reusable prompt packages (markdown files with instructions the agent can load) | Recipe books |

### The Automation Layer (running agents without human involvement)

| Folder | What it does | Analogy |
|--------|-------------|---------|
| `loop/` | Runs the agent on a schedule, verifies the result, retries or escalates | A cron job with a brain |
| `fleet/` | Manages multiple loops as one system — routing, budgets, alerting | Air traffic control |
| `workflow.py` | Durable pipelines — define multi-step flows with checkpoints | An assembly line |
| `dispatch.py` | Fire-and-forget async — send a task and don't wait | Putting a letter in the mailbox |
| `graph.py` | DAG execution — run tasks in parallel when they don't depend on each other | A project Gantt chart |

### Trust & Safety

| Folder | What it does | Analogy |
|--------|-------------|---------|
| `assurance/` | Cryptographic audit trail — tamper-proof receipts of what the agent did | A notarized document |
| `comply/` | Regulatory monitoring — checks agents against HIPAA, SOX, GDPR rules continuously | The compliance department |
| `approval.py` | Human-in-the-loop — requires approval before dangerous actions | The "Are you sure?" dialog |
| `boundary.py` | Detects and blocks prompt injection attacks | A spam filter |
| `masking.py` | Controls which tools are available at which phase (governance) | Level-locked access |
| `cost.py` | Tracks and caps spending per agent/run/fleet | The budget department |

### Applications (built ON the framework — shows what you can do with it)

| Folder | What it does | Analogy |
|--------|-------------|---------|
| `fix/` | Auto-fixes failing test suites (flagship demo: `tvastar-fix`) | A self-healing CI |
| `ci/` | Autonomous CI monitor — watches builds, fixes them, reports results | A dedicated bot on your repo |
| `outbound/` | AI-powered cold email campaigns (research, score, write, send) | A sales development bot |
| `bench/` | Benchmarks agent quality against real-world test suites | A standardized exam |
| `planning/` | Decomposes goals into requirements, designs, and tasks | A project manager |

### Deployment & Serving

| Folder | What it does | Analogy |
|--------|-------------|---------|
| `deploy/` | Same agent → Lambda, Docker, GitHub Action, serverless (zero rewrites) | "Build once, deploy anywhere" |
| `serving/` | HTTP/WebSocket server + CLI entry point | The front door |
| `adapters/` | Quality layer for OTHER frameworks (LangGraph, AgentCore, OpenAI) | An adapter plug |
| `ui/` | Local trace viewer — see what the agent did in your browser | The black box playback screen |

### Experimental / Empty

| Folder | What it does | Status |
|--------|-------------|--------|
| `canvas/` | Visual workspace (static assets) | Mostly empty |
| `studio/` | Visual agent builder | Empty / planned |
| `contrib/` | Optional extensions (currently: Long-Term Memory) | One module (ltm) |

---

## How a User Actually Experiences Tvastar

### The "Hello World" (30 seconds)

```python
from tvastar import create_agent, Harness, default_toolset
from tvastar.model import AnthropicModel

agent = create_agent("coder", model=AnthropicModel("claude-sonnet-4-6"), tools=default_toolset())
result = await Harness(agent).run("Fix the failing tests")

print(result.ok)              # Did it actually work?
print(result.quality.grade)   # PASS / ACCEPTABLE / FAIL
print(result.quality.summary) # What went wrong (if anything)
```

### The "Running Forever" (Loop Engineering)

```python
from tvastar.loop import Loop, LoopConfig

loop = Loop(agent, LoopConfig(
    name="ci-fixer",
    schedule="*/15 * * * *",   # every 15 min
    max_iterations=3,          # retry up to 3 times
    cancel_after=300.0,        # 5 min timeout
))
await loop.start()  # Runs forever. Fixes CI. Alerts you if stuck.
```

### The "Fleet" (Multiple Agents Coordinated)

```python
from tvastar.fleet import Fleet, FleetConfig

fleet = Fleet(FleetConfig(name="production"))
fleet.register(ci_loop, name="ci-fixer")
fleet.register(triage_loop, name="daily-triage")
fleet.register(security_loop, name="cve-scanner")

# Submit tasks — fleet routes to the best agent
await fleet.submit("Fix the auth test failure")
```

---

## The CLI Tools

| Command | What it does |
|---------|-------------|
| `tvastar serve agent.py:agent` | Start an HTTP server for your agent |
| `tvastar chat agent.py:agent` | Interactive REPL in the terminal |
| `tvastar quality agent.py:agent "task"` | Score a single run (exit 1 if FAIL) |
| `tvastar-fix --test-cmd "pytest"` | Auto-fix failing tests in current repo |
| `tvastar-ci` | Autonomous CI monitoring |
| `tvastar-outbound --csv leads.csv` | Run an email campaign |
| `tvastar-comply audit loop.py:loop` | Compliance check |

---

## Key Numbers to Know

| Metric | Value |
|--------|-------|
| Version | 0.23.0 |
| Python requirement | 3.10+ |
| Core dependencies | **Zero** (all providers are optional extras) |
| Test count | 2,500+ |
| Detection accuracy (benchmark) | 100% vs 0% for traditional monitoring |
| Model backends | 4 (Anthropic, OpenAI, LiteLLM=100+ providers, Mock) |
| Sandbox types | 4 (Virtual, Local, Docker, Remote) |
| Storage backends | 3 (InMemory, File, SQLite) |
| Built-in failure detectors | 8 |
| Pre-built loop patterns | 6 (CISweeper, PRBabysitter, DailyTriage, etc.) |

---

## What Makes Us Different from Competitors

| Product | What they do | What we do that they don't |
|---------|-------------|-------------------------------|
| **LangGraph** | Agent orchestration (graphs, state machines) | No loops, no fleet, no quality scoring, no failure detection |
| **CrewAI** | Multi-agent collaboration | No autonomous loops, no verification, no durability |
| **AWS AgentCore** | Enterprise agent hosting | No quality scoring, no loop engineering, no fleet coordination |
| **LangSmith** | Observability/tracing | Shows what happened — doesn't judge whether it succeeded |
| **Temporal** | Durable workflows | Not agent-aware — no model integration, no quality, no tools |

**Our position:** We're the full-stack framework. Everyone else does one layer.

- Need an agent runtime? We have Harness.
- Need it to run autonomously? We have Loop.
- Need many agents coordinated? We have Fleet.
- Need to know if it actually worked? We have Quality + Detection.
- Need compliance proof? We have Assurance.
- Need to deploy anywhere? We have Deploy.

One framework, the entire agent lifecycle.

---

## Questions You Should Ask This Week

1. "Who is our most active user right now, and what feature do they actually use?"
2. "Which of the 27 modules has zero usage outside our own tests?"
3. "If we could only keep 5 folders, which 5 would we keep?"
4. "What's our distribution strategy — PyPI only, or something else?"
5. "What's the path from `pip install tvastar` to someone paying us?"

---

## Your First Week Checklist

- [ ] Install Tvastar and run the quickstart example
- [ ] Run `tvastar-fix --test-cmd "pytest"` on a repo with a failing test
- [ ] Read the `examples/detect_silent_failure.py` — this is our core magic
- [ ] Read `examples/self_healing_agent.py` — this is Loop engineering
- [ ] Try `tvastar chat` with a real model to experience multi-turn
- [ ] Look at `src/tvastar/detect/` — understand the 8 detectors
- [ ] Ask the founder: "Which 5 users have told you this changed how they work?"

Welcome aboard.
