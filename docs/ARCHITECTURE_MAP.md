# Tvastar architecture map

This page explains what each part of Tvastar does, how the parts connect, and when to use each one. For the decisions behind the design, see [Architecture Decision Records](ARCHITECTURE.md). For signatures, see the [API Reference](API.md).

## The system in one picture

```text
Your application
    │
    ▼
┌────────────────────────────────────────────────────────────────────┐
│  1. Agent declaration                                               │
│     create_agent() → AgentSpec: model, instructions, tools, limits │
└────────────────────────────────────────────────────────────────────┘
    │
    ▼
┌────────────────────────────────────────────────────────────────────┐
│  2. Runtime                                                         │
│     Harness → Session → model / tool loop → RunResult               │
└────────────────────────────────────────────────────────────────────┘
    │                         │                         │
    │                         │                         │
    ▼                         ▼                         ▼
 Models                    Capabilities               State
 providers                 tools + sandbox            Store + Memory
    │                         │                         │
    └─────────────────────────┴─────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│  3. Control (optional)                                              │
│     Workflow · Dispatch · Loop                                      │
└────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│  4. Coordination (optional)                                         │
│     Fleet: registry · routing · shared state · budgets · events     │
└────────────────────────────────────────────────────────────────────┘

Across every layer: quality findings · verification · governance · approvals
                     assurance receipts · budgets · tracing · security policy
```

The core path is small: declare an agent, run it through a harness, and receive a `RunResult`. Everything above that path is an opt-in composition for a more demanding operating need.

## Layer map

| Layer | Responsibility | Main parts | Use it when… |
|---|---|---|---|
| **0. Contracts and models** | Normalize messages, tool calls, usage, and provider responses. | `Model`, `Message`, `ModelResponse`, `Usage`; Anthropic, OpenAI-compatible, LiteLLM, and `MockModel` adapters | You need to connect a provider, switch providers, or test without network calls. |
| **1. Agent declaration** | Describe what an agent is allowed and configured to do; it contains no run-specific state. | `create_agent()`, `AgentSpec`, instructions, skills, tools, limits, policies | You are defining a reusable agent profile. |
| **2. Runtime** | Execute one task or conversation, manage the model/tool loop, and return a result. | `Harness`, `Session`, `RunResult` | You need one prompt, a multi-turn conversation, or bounded parallel runs. |
| **3. Capabilities and state** | Let an agent act and remember while keeping execution/storage choices explicit. | `Tool`, `ToolRegistry`, sandboxes, `Store`, `Memory`, `FileStore`, `SQLiteStore` | The agent needs tools, files, commands, persistent session history, or scoped memory. |
| **4. Control** | Turn a harness run into a bounded workflow, an asynchronous dispatch, or a recurring operator. | `@workflow`, `Workflow`, `dispatch`, `DispatchPool`, `Loop` | The work has steps, should start in the background, or must repeat/retry/escalate. |
| **5. Coordination** | Route work across multiple registered operational loops. | `Fleet`, registry, gateway, shared state, event bus, fleet budget, observer | Multiple loops need routing, dependency state, shared budgets, or fleet-wide observation. |
| **6. Trust and operations** | Observe work, enforce capability boundaries, evaluate defined outcomes, and retain evidence. | findings/quality, `VerificationContract`, `GovernancePolicy`, approval gates, `AssurancePolicy`, `TrustLog`, `Tracer` | A run has cost, safety, audit, reliability, or acceptance requirements. |

## 0. Contracts and models

Tvastar keeps provider-specific behavior at the edge. A provider adapter implements the `Model` contract: it receives messages, a system prompt, visible tool schemas, and generation settings; it returns a normalized `ModelResponse`.

**Key parts**

- `Model` — common model interface.
- Provider adapters — Anthropic, OpenAI-compatible providers, LiteLLM, and other optional integrations.
- `MockModel` — scripted responses for deterministic, offline tests.
- Core message types — text, image, tool-use, tool-result, usage, and stream events.

**Use this layer directly** only when adding a provider adapter or writing deterministic tests. Most applications pass a configured model to `create_agent()` and stay above this layer.

## 1. Agent declaration

`create_agent()` builds an `AgentSpec`. The specification is the reusable description of an agent:

```text
AgentSpec
├── model and instructions
├── skills and ToolRegistry
├── sandbox factory
├── limits: steps, tokens, concurrency, memory
├── optional policies: budget, approvals, masking, governance
├── optional behavior: detection, compaction, retries, hooks, fallbacks
└── optional assurance and sub-agent profiles
```

An `AgentSpec` is configuration, not an active worker. Keep operational state in a `Session`, `Loop`, or `Fleet` instead of mutating a shared agent definition.

**Choose it when:** you need a named, repeatable capability such as a test fixer, researcher, classifier, or review agent.

## 2. Runtime: Harness and Session

`Harness` owns an `AgentSpec`, a `Store`, and a `Tracer`. It provides the top-level execution entry points:

| API | Use | Result |
|---|---|---|
| `await harness.run(prompt)` | One independent task | One `RunResult` |
| `harness.session()` then `await session.prompt(...)` | A multi-turn conversation or coding session | A continuing message history |
| `await harness.fan_out(prompts, concurrency=N)` | Independent batch work | A list of `RunResult` values |
| `harness.resume(session_id)` | Continue a persistent session after restart | Restored `Session` or `None` |

A `Session` performs the runtime loop:

```text
user message
  → build system prompt from AgentSpec + active skill
  → show the model the currently visible tool schemas
  → model response
  → if tool calls: enforce governance, invoke tools, append results
  → repeat until end turn, configured stop, limit, timeout, or error
  → build RunResult
  → run post-hoc detection and optional assurance
```

A `RunResult` records text, messages, token usage, cost, number of steps, stop reason, findings, and any configured receipt. The final agent text is useful output, but it is not automatically a task acceptance proof.

## 3. Capabilities and state

### Tools

A `Tool` is a typed Python callable, normally registered with `@tool`. Tvastar derives a JSON schema for the model, invokes the selected tool, returns its result to the model, and records the result in the session history.

`default_toolset()` supplies common coding capabilities such as shell execution and file operations. `ToolContext` can give tools access to the current sandbox, filesystem, session-scoped memory, and approval gate.

**Use tools** for capabilities selected by the model. Keep deterministic acceptance checks outside the model loop where possible.

### Sandboxes

A sandbox supplies the execution/filesystem boundary for tools.

| Sandbox | What it is for | Important boundary |
|---|---|---|
| `VirtualSandbox` | Fast in-memory work, tests, trusted development | Not a security or operating-system isolation boundary. |
| `LocalSandbox` | A constrained local subprocess workspace | Apply a restrictive `SecurityPolicy`; it is not a substitute for container or host isolation. |
| Container/remote backends | Higher-isolation or remote execution needs | Choose and operate them according to the deployment threat model. |
| Durable lifecycle backends | Long-lived container state, checkpointing, hibernation, or scaling where supported | Capability varies by backend. |

### Stores and memory

`Store` is the persistence abstraction. `InMemoryStore` is the default and is process-local. `FileStore` and `SQLiteStore` provide persistent backing for supported runtime state. `Memory` scopes data under a session identifier.

| Need | Choose |
|---|---|
| Fast local test or disposable run | Default `InMemoryStore` |
| Resume session history after a restart | `FileStore` or `SQLiteStore` |
| Long-lived application facts/search | An appropriate memory or LTM component, configured explicitly |
| Fleet-shared operational state | Fleet `SharedStateStore` with a suitable backend |

Durability applies only to data that has been successfully persisted. It does not make an in-memory run restart-safe or recover work that was never written.

## 4. Control: workflow, dispatch, and loop

These components wrap the Harness; they do not replace it.

### Workflow — named bounded work

`@workflow` and `Workflow` are for application-defined async flows with named phases, run records, and optional checkpoints. Use a workflow when you know the steps and dependencies in advance: ingest → validate → transform → publish, for example.

### Dispatch — background request handling

`dispatch()` and `DispatchPool` start work and expose events/status without making the caller wait for completion. Use dispatch for webhook, queue, chat, or service entry points where the request lifecycle is separate from the agent lifecycle.

### Loop — recurring operational objective

`Loop` runs an agent on a schedule or event trigger. It adds a state machine around the harness:

```text
IDLE → TRIGGERED → RUNNING → VERIFYING → PASS
                                │
                                └→ FAIL → RETRY → HANDOFF → SUSPENDED
```

A loop can use timeouts, exponential backoff, handoff policies, a circuit breaker, a budget/fuel limit, quality gates, and a task-specific verification contract. Loops use file-backed state by default; pass a different store only when you understand the durability trade-off.

**Use a Loop** when the goal repeats without a user waiting for every run: keeping a check green, polling a source, processing scheduled work, or operating a controlled remediation cycle.

## 5. Coordination: Fleet

`Fleet` coordinates multiple registered `Loop` instances. It is the highest-level composition and is unnecessary for a single agent or loop.

```text
Fleet
├── Registry       identity, lifecycle, versions, dependencies
├── Gateway        explicit or semantic task routing and rate limits
├── Shared state   fleet-wide operational data
├── Event bus      pub/sub events and alerts
├── Budget         fleet and per-agent cost limits
└── Observer       quality, error-rate, and cost signals
```

**Use Fleet** when several operational loops need centralized routing, shared state, budgets, dependency awareness, and fleet-level observation. Do not introduce it merely to run several independent prompts; `Harness.fan_out()` is smaller for that job.

## 6. Trust and operations

These concerns cross the runtime rather than forming a second agent loop.

| Concern | Mechanism | What it does | What it does not do |
|---|---|---|---|
| **Detection and quality** | Detectors, findings, quality report | Inspects a completed run and exposes behavioral signals | Prevent an action that already occurred or prove correctness |
| **Verification** | `VerificationContract` / `VerificationVerdict` on `LoopConfig` | Accepts or rejects a loop result against an explicit operator-provided condition | Validate anything beyond the condition the verifier checks |
| **Masking** | `ToolPolicy` | Reduces the tool schemas shown to the model | Enforce authorization; a policy failure exposes the available tools |
| **Governance** | `GovernancePolicy`, approval gates | Enforces phase-based tool permission at invocation time and can request approval | Replace host, network, or identity security controls |
| **Assurance** | `AssurancePolicy`, `ExecutionReceipt`, `TrustLog` | Produces integrity/audit evidence and can enforce configured quality SLA rules | Provide independent third-party attestation |
| **Cost** | `BudgetPolicy`, usage and cost records | Limits or accounts for configured spend | Guarantee task correctness |
| **Observability** | `Tracer`, console/JSONL/OTel exporters | Emits diagnostic spans and events | Change a run's result; exporter failures do not stop work |

A practical rule: use governance before an action, verification after a run for a named acceptance condition, and assurance/observability to retain evidence about what occurred.

## End-to-end data flow

```text
1. Application creates AgentSpec with create_agent().
2. Harness opens or resumes a Session and starts its sandbox.
3. Session records the user message, then composes the system prompt.
4. The model receives messages and visible tool schemas.
5. A tool request passes governance checks before invocation.
6. Tool results return to the session as tool-result messages; the model can continue.
7. Session ends with a RunResult, then runs detectors and optional assurance.
8. A Loop may apply a VerificationContract, retry/backoff, or handoff.
9. A Fleet may route future tasks to an appropriate registered Loop.
```

## Choose the smallest architecture that fits

| Situation | Start with | Add only if needed |
|---|---|---|
| One answer or one action | `create_agent()` + `Harness.run()` | Tools, sandbox, a named check |
| Ongoing conversation | `Harness.session()` | Persistent store for restart recovery |
| Independent batch jobs | `Harness.fan_out()` | Budget, tracing, or dispatch |
| Named multi-phase process | `@workflow` | Checkpoints and a durable registry |
| Background request from a service | `dispatch()` / `DispatchPool` | Shared store and event observers |
| Recurring objective with retry/handoff | `Loop` | Verification contract, budget, handoff policy |
| Several operational loops | `Fleet` | Persistent state/event backends and fleet budgets |
| High-impact action | Suitable sandbox + governance | Approval gate, task-specific verifier, assurance log |

## Source map

| Area | Primary package paths |
|---|---|
| Public surface | `src/tvastar/__init__.py` |
| Agent declaration | `src/tvastar/agent.py` |
| Runtime/session loop | `src/tvastar/harness.py`, `src/tvastar/session.py` |
| Tools | `src/tvastar/tools/` |
| Execution boundaries | `src/tvastar/sandbox/` |
| Storage and memory | `src/tvastar/memory/`, `src/tvastar/conversation/` |
| Workflow and dispatch | `src/tvastar/workflow.py`, `src/tvastar/dispatch.py` |
| Recurring loops | `src/tvastar/loop/` |
| Fleet coordination | `src/tvastar/fleet/` |
| Quality and verification | `src/tvastar/detect/`, `src/tvastar/quality.py`, `src/tvastar/verification.py` |
| Governance and approval | `src/tvastar/masking.py`, `src/tvastar/approval.py` |
| Assurance and audit evidence | `src/tvastar/assurance/` |
| Tracing and metrics | `src/tvastar/observability.py` |

## Related documents

- [Getting Started](GETTING_STARTED.md) — first agent and verified CI repair
- [Usage Guide](USAGE.md) — choose an API for a concrete application flow
- [API Reference](API.md) — public interfaces and signatures
- [Threat Model](threat-model.md) — actors, trust boundaries, and residual risks
- [SLOs](slo.md), [Failure Modes](failure-modes.md), and [Runbooks](runbooks/) — operating the system
- [Architecture Decision Records](ARCHITECTURE.md) — why key trade-offs were made
