# Tvastar — AI Codebase Map

**Central equation:** `Agent = AgentSpec + Harness`

An `AgentSpec` is a pure, immutable declaration of what an agent is (model, tools, instructions, skills, sandbox, policies). A `Harness` is the stateful runtime that runs it across sessions. A `Session` is one conversation thread. This separation means you define once and run anywhere.

---

## Mental model in one diagram

```
create_agent(...)
      │
      ▼
  AgentSpec  ──────────────────────────────────────────┐
  (immutable)                                          │
  .model          AnthropicModel | OpenAIModel | Mock  │
  .tools          ToolRegistry (name → Tool)           │
  .skills         SkillLibrary (name → Skill)          │
  .instructions   str (system prompt base)             │
  .sandbox_factory() → Sandbox                         │
  .compaction     CompactionPolicy | None              │
  .tool_retry     ToolRetryPolicy | None               │
  .thinking_level 'low'|'medium'|'high'|None           │
  .subagents      dict[str, AgentProfile]              │
      │                                                │
      ▼                                                │
  Harness(spec)  ─────────────────────────────────────┘
  .run(prompt)           → RunResult       one-shot convenience
  .session(name?)        → Session         named/parallel sessions
  .fan_out([prompts])    → list[RunResult] concurrent multi-prompt
  .fs                    → HarnessFS       app-level file access
  .shell(cmd)            → str             app-level shell
  .resume(session_id)    → Session|None    reload from checkpoint
      │
      ▼
  Session  (one conversation thread)
  .prompt(text, *, result=None)    → RunResult
  .skill(name, text, *, result=None) → RunResult
  .task(text, *, agent=None, ...)  → RunResult   child session
  .stream(text)                    → AsyncIterator[StreamEvent]
  .messages                        list[Message]  full history
```

---

## Package layout

```
tvastar/
├── types.py          ← ALL core dataclasses live here (read this first)
├── agent.py          ← AgentSpec dataclass + create_agent() factory
├── harness.py        ← Harness class + HarnessFS
├── session.py        ← Session + RunResult + the agent loop
├── model/
│   ├── base.py       ← Model ABC: generate(messages,...) → ModelResponse
│   ├── anthropic.py  ← AnthropicModel — maps thinking_level → budget_tokens
│   ├── openai.py     ← OpenAIModel — maps thinking_level → reasoning_effort
│   └── mock.py       ← MockModel(script=[]) for tests
├── tools/
│   ├── base.py       ← Tool, ToolRegistry, ToolContext, ToolRetryPolicy, @tool
│   ├── builtin.py    ← default_toolset() — bash, read/write/edit/grep/glob
│   └── schema.py     ← Python signature → JSON schema auto-derivation
├── skills/
│   └── loader.py     ← Skill, SkillLibrary — Markdown file parser
├── sandbox/
│   ├── base.py       ← Sandbox ABC: start/stop/exec/fs
│   ├── virtual.py    ← VirtualSandbox — in-memory, zero deps (default)
│   └── local.py      ← LocalSandbox — real subprocess with SecurityPolicy
├── memory/
│   └── store.py      ← Store ABC, InMemoryStore, FileStore, Memory (scoped KV)
├── profiles.py       ← AgentProfile, define_agent_profile(), MAX_TASK_DEPTH=4
├── workflow.py       ← @workflow, Workflow, WorkflowContext, WorkflowHarness,
│                       WorkflowRun, RunRegistry, RunEvent, RunStatus
├── dispatch.py       ← dispatch(), dispatch_and_wait(), DispatchInput,
│                       DispatchEvent, observe_dispatch(), cancel_dispatch()
├── compaction.py     ← CompactionPolicy, should_compact(), compact_messages(),
│                       compact_session()
├── durable.py        ← Checkpointer — save/load session checkpoints
├── observability.py  ← Tracer, Span, ConsoleExporter, JSONLExporter, OTelExporter
├── detect/           ← Silent-failure detectors (Finding, Severity, RunContext)
├── mcp/              ← MCPClient, connect_mcp_server() — MCP protocol client
├── serving/
│   ├── http.py       ← FastAPI app: POST /prompt, WS /stream, GET /stream (SSE)
│   └── cli.py        ← tvastar chat|serve|run|info|logs
├── deploy/           ← ASGI / Lambda / serverless / GitHub Action adapters
├── fix/              ← tvastar-fix: auto-repair failing test suites
└── __init__.py       ← re-exports everything public (see __all__)
```

---

## Key types (tvastar/types.py)

```python
# A single turn in a conversation
@dataclass
class Message:
    role: 'system'|'user'|'assistant'|'tool'
    content: str | list[TextBlock | ToolUseBlock | ToolResultBlock]
    id: str          # auto-generated
    created_at: float
    metadata: dict

    .blocks  → list[ContentBlock]   # always a list, normalises str content
    .text    → str                  # concatenated TextBlock text
    .tool_uses → list[ToolUseBlock]

@dataclass
class TextBlock:     text: str
@dataclass
class ToolUseBlock:  name: str; input: dict; id: str
@dataclass
class ToolResultBlock: tool_use_id: str; content: str; is_error: bool

@dataclass
class ModelResponse:
    message: Message
    stop_reason: StopReason   # END_TURN | TOOL_USE | MAX_TOKENS | STOP_SEQUENCE
    usage: Usage              # .input_tokens, .output_tokens
    raw: Any                  # provider's raw response object

@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    def __add__(self, other) → Usage   # supports total = sum(usages)

@dataclass
class ToolSpec:   # what gets sent to the model
    name: str; description: str; input_schema: dict

@dataclass
class StreamEvent:
    type: 'text_delta'|'tool_call'|'tool_result'|'turn_start'|'turn_end'|'error'
    data: dict
    at: float
```

---

## The agent loop (session.py `_run_loop`)

```
while steps < max_steps:
    resp = await model.generate(
        messages,
        system=system_prompt,
        tools=tool_specs,
        max_tokens=..., temperature=...,
        thinking_level=spec.thinking_level,   # ← new in 0.2.0
    )
    messages.append(resp.message)

    if resp.stop_reason != TOOL_USE:
        break                                 # done — return RunResult

    # Execute all requested tools concurrently
    results = await asyncio.gather(*[tool.invoke(use.input, ctx,
                                     default_retry=spec.tool_retry)  # ← retry
                                     for use in resp.tool_uses])
    messages.append(Message("user", results))

    await _maybe_compact()                    # ← compaction if policy set
    _checkpoint()                             # ← durable save after each turn

return RunResult(text, messages, usage, steps, stopped, findings, data)
```

---

## AgentSpec fields (agent.py)

```python
@dataclass
class AgentSpec:
    name: str
    model: Model
    instructions: str = ""
    tools: ToolRegistry = ToolRegistry()
    skills: SkillLibrary = SkillLibrary()
    sandbox_factory: Callable[[], Sandbox] = VirtualSandbox
    max_steps: int = 20
    max_tokens: int = 4096
    temperature: float = 1.0
    thinking_level: str|None = None      # 'low'|'medium'|'high' — maps to provider
    detectors: list = []                 # silent-failure detectors
    compaction: CompactionPolicy|None = None
    tool_retry: ToolRetryPolicy|None = None
    subagents: dict[str, AgentProfile] = {}
    metadata: dict = {}
```

`create_agent(name, *, model, instructions, tools, skills, sandbox, max_steps,
              max_tokens, temperature, thinking_level, detect, subagents,
              compaction, tool_retry, **metadata) → AgentSpec`

---

## RunResult fields (session.py)

```python
@dataclass
class RunResult:
    text: str                        # final assistant text
    messages: list[Message]          # full conversation history
    usage: Usage
    steps: int
    stopped: 'end_turn'|'max_steps'|'error'
    findings: list[Finding]          # from silent-failure detectors
    data: Any                        # populated when result= schema used

    .ok        → bool   # stopped=='end_turn' and no warnings
    .warnings  → list[Finding]
```

---

## Model interface (model/base.py)

Every model adapter implements exactly one method:

```python
class Model(ABC):
    name: str

    async def generate(
        self,
        messages: list[Message],
        *,
        system: str | None,
        tools: list[ToolSpec] | None,
        max_tokens: int,
        temperature: float,
        stop_sequences: list[str] | None,
        thinking_level: str | None,      # 'low'|'medium'|'high'|None
    ) -> ModelResponse: ...

    async def stream(...) -> AsyncIterator[StreamEvent]: ...  # optional override
```

**thinking_level mapping:**
- `AnthropicModel`: `low→budget_tokens=1024`, `medium→8000`, `high→16000` + beta header
- `OpenAIModel`: passed as `reasoning_effort='low'|'medium'|'high'`
- `MockModel`: accepted, ignored (echoed in text for test visibility)

---

## Tool system (tools/base.py)

```python
@dataclass
class Tool:
    name: str
    description: str
    fn: Callable
    input_schema: dict       # JSON Schema — auto-derived by schema.py
    wants_ctx: bool          # True if fn has a 'ctx: ToolContext' param
    retry: ToolRetryPolicy | None

    async def invoke(args, ctx, *, default_retry=None) → str

@dataclass
class ToolRetryPolicy:
    max_attempts: int = 3
    backoff_base: float = 0.5    # sleep = base * 2^attempt + jitter
    backoff_max: float = 10.0
    jitter: float = 0.1
    retryable: Callable[[Exception], bool] | None = None

@dataclass
class ToolContext:
    sandbox: Sandbox | None
    filesystem: FileSystem | None
    memory: Memory
    session: Session
    extra: dict
```

`@tool(fn=None, *, name=None, description=None, retry=None)` — decorator

Tool-level retry > harness-wide `tool_retry` > no retry.

---

## Profiles and task delegation (profiles.py, session.py)

```python
@dataclass
class AgentProfile:
    name: str
    description: str = ""
    instructions: str | None = None     # None → inherit parent
    model: Model | None = None          # None → inherit parent
    tools: list | None = None           # None → inherit parent
    skills: list | None = None          # None → inherit parent
    thinking_level: str | None = None   # None → inherit parent
    max_steps: int | None = None        # None → inherit parent
    subagents: list[AgentProfile] = []
    metadata: dict = {}
```

Resolution order for `session.task(agent='name')`:
`task override > profile field > parent spec`

`MAX_TASK_DEPTH = 4` — raises `RuntimeError` if exceeded.

---

## Workflow (workflow.py)

```python
@workflow
async def my_flow(ctx: WorkflowContext) -> dict:
    harness = await ctx.init(agent_spec)  # → WorkflowHarness
    sess    = await harness.session()     # → Session (started)
    result  = await sess.prompt("...")
    ctx.log.info("done", steps=result.steps)
    return {"result": result.text}

run: WorkflowRun = await my_flow.run(payload={"key": "value"})
run.run_id      # 'run_abc123'
run.status      # RunStatus.COMPLETED | FAILED | RUNNING | PENDING
run.output      # the returned dict
run.error       # str | None
run.events      # list[RunEvent]  — lifecycle log

# Persistent registry
my_flow.list_runs()         → list[WorkflowRun]
my_flow.get_run(run_id)     → WorkflowRun | None
RunRegistry.file_backed(".tvastar-runs")  → persistent across restarts
```

---

## Dispatch (dispatch.py) — fire-and-observe

```python
# Fire and forget — returns dispatch_id immediately
dispatch_id = await dispatch(
    spec,
    id="thread_42",               # instance identity (harness reuse key)
    session="thread_42",          # session identity (default = id)
    input=DispatchInput(text="hi", type="chat.message"),
    on_complete=lambda r: send_reply(r.text),
    on_error=lambda e: log(e),
    cancel_after=30.0,
)

# Fire and await
result: RunResult = await dispatch_and_wait(spec, id="u1", text="hello")

# Observe all dispatches globally
observe_dispatch(lambda event: print(event.type, event.dispatch_id))

# Cancel / inspect
cancel_dispatch(dispatch_id)   → bool
list_active_dispatches()       → list[str]

@dataclass
class DispatchEvent:
    type: 'dispatch_start'|'dispatch_end'|'dispatch_error'
    dispatch_id: str; agent_id: str; session_id: str
    at: float; data: dict
```

---

## Compaction (compaction.py)

```python
@dataclass
class CompactionPolicy:
    max_messages: int = 60         # compact when len(messages) > this
    max_tokens_estimate: int = 80_000
    keep_last: int = 10            # always preserve this many recent messages
    min_messages: int = 20         # don't compact below this floor
    summary_instruction: str = "..."
    token_estimator: Callable | None = None

# Auto-fires inside session._run_loop() after each tool turn
# Returns [compact_notice_msg, summary_msg, *tail(keep_last)]
```

Attach to agent: `create_agent(..., compaction=CompactionPolicy(max_messages=40))`

---

## Structured results

Pass a schema to `prompt()`, `skill()`, or `task()`:

```python
result = await session.prompt("Return user data as JSON", result=dict)
result.data    # parsed dict

# Pydantic v2
class User(BaseModel): name: str; age: int
result = await session.prompt("...", result=User)
result.data    # User instance
```

JSON schema is injected into the prompt. Falls back to raw text on parse failure.

---

## fan_out (harness.py)

```python
results = await harness.fan_out([
    "Summarise chapter 1",
    "Summarise chapter 2",
    {"prompt": "Summarise chapter 3", "agent": "summariser",
     "cancel_after": 20.0, "result": SummarySchema},
], concurrency=4)   # optional semaphore cap
# returns list[RunResult] in same order
```

---

## HTTP server (serving/http.py)

Routes (needs `pip install tvastar[serve]`):

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | health + agent info |
| GET | `/sessions` | list session ids |
| POST | `/sessions` | create session → `{session_id}` |
| POST | `/sessions/{id}/prompt` | `{text}` → `{text, usage, steps, stopped}` |
| WS | `/sessions/{id}/stream` | send `{text}`, receive `StreamEvent` JSON |
| GET | `/sessions/{id}/stream?text=...` | SSE — `data: {type,data}\n\n` … `data: [DONE]` |

---

## Silent-failure detectors (detect/)

```python
@dataclass
class Finding:
    detector: str      # 'thrash_loop', 'unverified_completion', ...
    severity: Severity # INFO | WARNING | ERROR
    message: str
    context: dict

class RunContext:
    messages: list[Message]
    tools: ToolRegistry
    stopped: str        # 'end_turn' | 'max_steps'
    final_text: str

# A detector is just: Callable[[RunContext], list[Finding]]
```

Built-in detectors: `unknown_tool`, `schema_mismatch`, `thrash_loop`,
`ignored_tool_error`, `unverified_completion`, `empty_answer`, `step_limit`.

---

## Observability (observability.py)

```python
tracer = Tracer([ConsoleExporter(), JSONLExporter("trace.jsonl")])
harness = Harness(agent, tracer=tracer)

# Spans emitted automatically: model.generate, tool.invoke, session.prompt,
# session.skill, session.task, event.*, context_compacted
```

---

## MCP client (mcp/)

```python
client = await connect_mcp_server(command="python", args=["server.py"])
# or: connect_mcp_server(url="https://...", headers={"Authorization": "Bearer ..."})
agent = create_agent("a", model=m, tools=[*default_toolset(), *client.tools])
await client.close()
```

`client.tools` — list of `Tool` objects wrapping each MCP tool. Fully transparent to the model.

---

## Durable execution (durable.py)

```python
harness = Harness(agent, store=FileStore(".state"))
# Every session.prompt() checkpoints to store after each tool turn

# Resume:
sess = harness.resume("sess_abc") or harness.session()
```

---

## Dependency map

```
__init__.py
    ├── agent.py          → model/base, sandbox/base, tools/base, skills/loader, compaction
    ├── harness.py        → agent, session, durable, memory, observability
    ├── session.py        → types, errors, profiles, compaction, tools/base, sandbox
    ├── workflow.py       → agent, harness, memory
    ├── dispatch.py       → agent, harness, session, memory
    ├── compaction.py     → types, session (TYPE_CHECKING only)
    ├── profiles.py       → (no tvastar imports — leaf node)
    ├── types.py          → (stdlib only — leaf node)
    └── errors.py         → (stdlib only — leaf node)
```

Zero third-party dependencies in core. Provider SDKs (`anthropic`, `openai`),
server (`fastapi`, `uvicorn`), and OTel are lazy-imported behind optional extras.

---

## Common patterns (quick reference)

```python
# 1. One-shot run
result = await Harness(agent).run("do something")

# 2. Multi-turn session
sess = harness.session("my-thread")
async with sess:
    r1 = await sess.prompt("step 1")
    r2 = await sess.prompt("step 2")

# 3. Delegate to specialist
result = await sess.task("review auth code", agent="reviewer", cancel_after=30.0)

# 4. Parallel fan-out
results = await harness.fan_out(["task A", "task B", "task C"])

# 5. Structured output
from pydantic import BaseModel
class Report(BaseModel): summary: str; issues: list[str]
result = await sess.prompt("analyse this code", result=Report)
report: Report = result.data

# 6. Workflow with run history
@workflow
async def pipeline(ctx):
    h = await ctx.init(agent)
    s = await h.session()
    out = await s.prompt(ctx.payload["input"])
    return {"result": out.text}
run = await pipeline.run({"input": "hello"})

# 7. Event-driven / webhook
await dispatch(agent, id=user_id, text=message, on_complete=send_reply)

# 8. App-level file staging
async with Harness(agent) as h:
    await h.fs.write_file("input.md", content)
    result = await h.run("process input.md")
    output = await h.fs.read_file("output.md")

# 9. Auto-compaction
agent = create_agent(..., compaction=CompactionPolicy(max_messages=40, keep_last=10))

# 10. Tool retry
@tool(retry=ToolRetryPolicy(max_attempts=3, backoff_base=0.5))
async def call_api(url: str) -> str: ...

# 11. Extended thinking
agent = create_agent(..., thinking_level="high")  # Anthropic: budget=16000 tokens
```
