# Tvastar API Reference

Complete API reference for Tvastar v0.23.0. Every public symbol, field, and signature.

---

## `create_agent()` — the entry point

```python
from tvastar import create_agent

def create_agent(
    name: str,
    *,
    model: Model,
    instructions: str = "",
    tools: list[Tool | ToolRegistry] | None = None,
    skills: list[Skill] | SkillLibrary | None = None,
    sandbox: SandboxFactory | Sandbox | None = None,   # default: VirtualSandbox
    max_steps: int = 20,
    max_tokens: int = 4096,
    temperature: float = 1.0,
    thinking_level: str | None = None,    # 'low' | 'medium' | 'high'
    detect: bool | list | None = True,    # True=built-in suite, False=off
    subagents: list[AgentProfile] | None = None,
    compaction: CompactionPolicy | None = None,
    tool_retry: ToolRetryPolicy | None = None,
    assurance: AssurancePolicy | None = None,
    budget: BudgetPolicy | None = None,
    approval_gate: ApprovalGate | None = None,
    tool_policy: ToolPolicy | None = None,
    governance: GovernancePolicy | None = None,
    system_prompt_hook: Callable[..., str] | None = None,
    memory_cap_mb: float | None = None,   # session memory ceiling in MB
    pruner: AgentPruner | None = None,
    scrub_after_run: bool = False,
    # v0.20.0 — Maximum Dynamism Audit
    structured_retries: int = 2,          # retries on structured-output parse failure
    max_task_depth: int = 4,              # maximum task() delegation depth
    tool_concurrency: int | None = None,  # semaphore limit for parallel tools (None=unlimited)
    pre_tool_hook: Callable[[str, dict], dict | None] | None = None,
    post_tool_hook: Callable[[str, dict, str], str | None] | None = None,
    step_callback: Callable[[int, Any, list], None] | None = None,
    stop_predicate: Callable[[Any], bool] | None = None,
    middleware: list[Callable[[list], list]] | None = None,
    fallback_models: list[Model] | None = None,
    tool_order_fn: Callable[[list], list] | None = None,
    **metadata,                           # arbitrary key=value stored on spec
) -> AgentSpec
```

---

## `AgentSpec` — immutable agent declaration

```python
@dataclass
class AgentSpec:
    name: str
    model: Model
    instructions: str
    tools: ToolRegistry
    skills: SkillLibrary
    sandbox_factory: Callable[[], Sandbox]
    max_steps: int                         # default 20
    max_tokens: int                        # default 4096 — passed to model.generate() as token budget
    temperature: float                     # default 1.0
    thinking_level: str | None             # 'low'|'medium'|'high'|None
    detectors: list[Detector]              # silent-failure detectors (Protocol type)
    compaction: CompactionPolicy | None
    tool_retry: ToolRetryPolicy | None     # Protocol type
    subagents: dict[str, AgentProfile]
    budget: BudgetPolicy | None            # Protocol type (None=unlimited)
    approval_gate: ApprovalGate | None     # Protocol type
    tool_policy: ToolPolicy | None         # Protocol type (per-turn masking, advisory)
    governance: GovernancePolicy | None    # Protocol type (invocation-layer, tamper-proof)
    system_prompt_hook: Callable[..., str] | None
    memory_cap_mb: float | None            # session memory ceiling in MB (None=unlimited)
    assurance: AssurancePolicy | None      # Protocol type
    pruner: AgentPruner | None             # Protocol type
    scrub_after_run: bool                  # default False — SHA-256 hash messages after run

    # v0.20.0 — Configurable parameters (formerly hardcoded)
    structured_retries: int                # default 2 — retries on structured-output parse failure
    max_task_depth: int                    # default 4 — max task() delegation depth
    tool_concurrency: int | None           # None=unlimited; set N for semaphore

    # v0.20.0 — Extension points (hooks, middleware, fallbacks)
    pre_tool_hook: Callable[[str, dict], dict | None] | None
    post_tool_hook: Callable[[str, dict, str], str | None] | None
    step_callback: Callable[[int, Any, list], None] | None
    stop_predicate: Callable[[Any], bool] | None
    middleware: list[Callable[[list], list]] | None
    fallback_models: list[Model] | None
    tool_order_fn: Callable[[list], list] | None

    metadata: dict[str, Any]

    def build_system_prompt(self, *, last_user_text: str = "") -> str
    def get_subagent(self, name: str) -> AgentProfile | None
```

---

## `Harness` — the runtime

```python
class Harness:
    def __init__(
        self,
        spec: AgentSpec,
        *,
        store: Store | None = None,       # default: InMemoryStore
        tracer: Tracer | None = None,     # default: NULL_TRACER
        durable: bool = True,             # checkpoint after each turn
    )

    # Properties
    spec: AgentSpec
    store: Store
    tracer: Tracer
    checkpointer: Checkpointer | None

    # Run
    async def run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
    ) -> RunResult

    async def fan_out(
        self,
        prompts: list[str | dict],
        *,
        concurrency: int = 8,   # None = all at once (thundering-herd risk)
    ) -> list[RunResult]
    # dict form keys: prompt, agent, result, cancel_after, thinking_level, max_steps

    # Transactional sandbox — atomic rollback on exception
    @asynccontextmanager
    async def transaction(self, session: Session) -> AsyncIterator[Session]
    # Takes a sandbox snapshot before yielding, restores it if the body raises.
    # Emits workspace_rollback / workspace_rollback_failed tracer spans.
    # Silently skips snapshotting for sandboxes that don't support it.

    # Sessions
    def session(
        self,
        name: str | None = None,
        *,
        spec: AgentSpec | None = None,
        session_id: str | None = None,
    ) -> Session

    def resume(self, session_id: str) -> Session | None
    def list_sessions(self) -> list[str]

    # Application-level file access
    @property
    def fs(self) -> HarnessFS

    async def shell(self, cmd: str, *, timeout: float | None = None) -> str

    # Context manager (manages sandbox lifecycle)
    async def __aenter__(self) -> Harness
    async def __aexit__(self, *exc) -> None
```

### `HarnessFS`

```python
class HarnessFS:
    async def write_file(self, path: str, content: str) -> None
    async def read_file(self, path: str) -> str           # raises FileNotFoundError
    async def exists(self, path: str) -> bool
    async def list_dir(self, path: str = ".") -> list[str]
    async def delete_file(self, path: str) -> None
```

---

## `Session` — one conversation thread

```python
@dataclass
class Session:
    spec: AgentSpec
    harness: Harness
    id: str                                # 'sess_<12hex>'
    messages: list[Message]                # full history — readable/writable
    sandbox: Sandbox | None

    # Lifecycle
    async def start(self) -> Session
    async def close(self) -> None
    async def __aenter__(self) -> Session
    async def __aexit__(self, *exc) -> None

    # Prompt
    async def prompt(
        self,
        text: str,
        *,
        result: Any | None = None,         # schema for structured output
    ) -> RunResult

    # Skill
    async def skill(
        self,
        name: str,
        text: str,
        *,
        result: Any | None = None,
    ) -> RunResult

    # Task delegation (child session)
    async def task(
        self,
        prompt: str,
        *,
        agent: str | None = None,          # AgentProfile name
        instructions: str | None = None,   # anonymous task override
        result: Any | None = None,
        cwd: str | None = None,            # injected as [Working directory: ...]
        cancel_after: float | None = None, # asyncio.TimeoutError if exceeded
        model: Model | None = None,
        thinking_level: str | None = None,
        max_steps: int | None = None,
        router: AgentRouter | None = None, # auto-picks agent when agent= is None
    ) -> RunResult

    # Streaming
    async def stream(self, text: str) -> AsyncIterator[StreamEvent]

    # Memory (namespaced to this session)
    @property
    def memory(self) -> Memory
```

---

## `RunResult` — what you get back

```python
@dataclass
class RunResult:
    text: str                        # final assistant message text
    messages: list[Message]          # complete conversation history
    usage: Usage                     # token counts
    steps: int                       # number of model calls
    stopped: str                     # 'end_turn'|'max_steps'|'error'|'memory_cap'
    findings: list[Finding]          # from silent-failure detectors
    data: Any | None                 # populated when result= schema used
    cost: Cost | None = None         # populated when BudgetPolicy is configured

    @property
    def ok(self) -> bool             # stopped=='end_turn' and no warnings
    @property
    def warnings(self) -> list[Finding]

    def __str__(self) -> str         # returns .text
```

---

## Types (`tvastar/types.py`)

```python
@dataclass
class Message:
    role: 'system' | 'user' | 'assistant' | 'tool'
    content: str | list[TextBlock | ToolUseBlock | ToolResultBlock]
    id: str
    created_at: float
    metadata: dict[str, Any]

    @property def blocks(self) -> list[ContentBlock]
    @property def text(self) -> str                  # concatenated TextBlock text
    @property def tool_uses(self) -> list[ToolUseBlock]

@dataclass
class TextBlock:
    text: str
    type: str = "text"

@dataclass
class ToolUseBlock:
    name: str
    input: dict[str, Any]
    id: str                          # auto-generated 'call_<12hex>'
    type: str = "tool_use"

@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False
    type: str = "tool_result"

@dataclass
class ImageBlock:
    data: str                        # base64-encoded bytes or URL
    media_type: str = "image/jpeg"   # e.g. "image/png", "image/webp"
    source_type: str = "base64"      # "base64" | "url"
    type: str = "image"
    # Pass to session.prompt(images=[image_block]) — works with Anthropic vision models

@dataclass
class ModelResponse:
    message: Message
    stop_reason: StopReason
    usage: Usage = field(default_factory=Usage)
    raw: Any | None = None           # provider's raw response
    @property def tool_uses(self) -> list[ToolUseBlock]

class StopReason(str, Enum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    STOP_SEQUENCE = "stop_sequence"
    ERROR = "error"

@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    def __add__(self, other: Usage) -> Usage

@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]

@dataclass
class StreamEvent:
    type: Literal[
        'text_delta', 'tool_call', 'tool_result',
        'turn_start', 'turn_end',
        'skill_loaded', 'task_spawned',   # emitted by skill/task delegation
        'error',
    ]
    data: dict[str, Any]
    at: float
```

---

## Models (`tvastar/model/`)

```python
class Model(ABC):
    name: str

    @abstractmethod
    async def generate(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        stop_sequences: list[str] | None = None,
        thinking_level: str | None = None,
    ) -> ModelResponse

    async def stream(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        stop_sequences: list[str] | None = None,
        thinking_level: str | None = None,
    ) -> AsyncIterator[StreamEvent]
```

```python
@dataclass
class ModelRetryPolicy:
    """Exponential-backoff retry for transient model API errors (429, 5xx)."""
    max_attempts: int = 3
    backoff_base: float = 1.0         # full-jitter: sleep = uniform(0, min(base*2^n, max))
    backoff_max: float = 60.0
    jitter: float = 0.25
    retryable: Callable[[Exception], bool] | None = None
    # Default retryable: status 429 / 5xx / network errors; not 4xx (bad request)

class AnthropicModel(Model):
    def __init__(
        self,
        model: str = "claude-opus-4-8",
        *,
        api_key: str | None = None,    # default: ANTHROPIC_API_KEY env
        client: Any | None = None,     # inject pre-built AsyncAnthropic
        retry: ModelRetryPolicy | None = None,
    )
    # thinking_level mapping:
    #   'low'    → budget_tokens=1024
    #   'medium' → budget_tokens=8000
    #   'high'   → budget_tokens=16000
    #   'xhigh'  → budget_tokens=32000
    # All extended-thinking levels add the interleaved-thinking beta header
    # and force temperature=1.0.

class OpenAIModel(Model):
    def __init__(
        self,
        model: str = "gpt-4o",
        *,
        api_key: str | None = None,    # default: OPENAI_API_KEY env
        base_url: str | None = None,   # for compatible providers (Groq, Ollama, …)
        client: Any | None = None,
        retry: ModelRetryPolicy | None = None,
    )
    # thinking_level: passed as reasoning_effort='low'|'medium'|'high'
    # 'xhigh' is capped to 'high' (not supported by OpenAI API)

class MockModel(Model):
    name = "mock"
    def __init__(self, script: list[str | ToolUseBlock | Message] | None = None)
    calls: list[list[Message]]         # inspect after test
    # thinking_level: accepted and noted in echoed text for test visibility
```

### `LiteLLMModel` (`tvastar.model.litellm`)

Wraps `litellm.acompletion` and `litellm.Router` inside Tvastar's `Model` ABC. Covers 100+ providers with a single import. Requires `pip install tvastar[litellm]`.

```python
class LiteLLMModel(Model):
    system = "litellm"

    def __init__(
        self,
        model: str = "gpt-4o",
        *,
        model_list: list[dict] | None = None,   # if set, creates a litellm.Router
        routing_strategy: str = "usage-based-routing-v2",
        fallbacks: list[dict] | None = None,    # [{"fast": ["smart"]}]
        api_key: str | None = None,
        **router_kwargs,
    )
```

When `model_list` is provided, all calls go through a `litellm.Router` for load-balancing and fallback. Otherwise calls go directly to `litellm.acompletion`. The `model` string is the default/primary model passed to every completion call; router entries use their own `litellm_params.model`.

```python
from tvastar.model import LiteLLMModel

# Simple — one provider
model = LiteLLMModel("anthropic/claude-sonnet-4-6")

# Router — route between fast (cheap) and smart (expensive)
model = LiteLLMModel(
    "fast",
    model_list=[
        {"model_name": "fast",  "litellm_params": {"model": "claude-haiku-4-5-20251001"}},
        {"model_name": "smart", "litellm_params": {"model": "claude-sonnet-4-6"}},
    ],
    routing_strategy="usage-based-routing-v2",
    fallbacks=[{"fast": ["smart"]}],
)
```

---

## Tools (`tvastar/tools/`)

```python
def tool(
    fn: Callable | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    retry: ToolRetryPolicy | None = None,
) -> Tool
# Decorator — auto-derives JSON schema from type annotations

@dataclass
class Tool:
    name: str
    description: str
    fn: Callable
    input_schema: dict[str, Any]
    wants_ctx: bool               # True when fn has a 'ctx: ToolContext' param
    retry: ToolRetryPolicy | None

    async def invoke(
        self,
        args: dict[str, Any],
        ctx: ToolContext | None = None,
        *,
        default_retry: ToolRetryPolicy | None = None,
    ) -> str

@dataclass
class ToolRetryPolicy:
    max_attempts: int = 3
    backoff_base: float = 0.5       # sleep = base * 2^attempt + jitter
    backoff_max: float = 10.0
    jitter: float = 0.1
    retryable: Callable[[Exception], bool] | None = None
    # Default: retry everything except ToolNotFound and TypeError

    def should_retry(self, exc: Exception) -> bool
    def sleep_for(self, attempt: int) -> float

@dataclass
class ToolContext:
    sandbox: Sandbox | None
    filesystem: FileSystem | None
    memory: Memory
    session: Session
    approval_gate: Any | None        # ApprovalGate — available inside tool via ctx
    extra: dict[str, Any]

class ToolRegistry:
    def add(self, t: Tool) -> None
    def extend(self, tools: list[Tool]) -> None
    def get(self, name: str) -> Tool            # raises ToolNotFound
    def names(self) -> list[str]
    @property specs: list[ToolSpec]
    def __contains__(self, name: str) -> bool
    def __len__(self) -> int

def default_toolset() -> list[Tool]:
    # bash, read_file, write_file, edit_file, grep, glob, list_files
```

---

## AgentProfile (`tvastar/profiles.py`)

```python
MAX_TASK_DEPTH: int = 4

@dataclass
class AgentProfile:
    name: str
    description: str = ""
    instructions: str | None = None    # None → inherit parent
    model: Model | None = None         # None → inherit parent
    tools: list | None = None          # None → inherit parent
    skills: list | None = None         # None → inherit parent
    thinking_level: str | None = None  # None → inherit parent
    max_steps: int | None = None       # None → inherit parent
    subagents: list[AgentProfile] = []
    detect: bool | list | None = None  # v0.20.0: None=inherit, False=disable, True/list=configure
    metadata: dict[str, Any] = {}

def define_agent_profile(
    name: str,
    *,
    description: str = "",
    instructions: str | None = None,
    model: Model | None = None,
    tools: list | None = None,
    skills: list | None = None,
    thinking_level: str | None = None,
    max_steps: int | None = None,
    subagents: list[AgentProfile] | None = None,
    detect: bool | list | None = None,
    **metadata,
) -> AgentProfile
```

---

## `AgentRouter` (`tvastar/router.py`)

Routes a task prompt to the best-matching `AgentProfile`. Uses semantic-router (embedding-based) when installed (`pip install tvastar[router]`), falls back to difflib word-overlap with zero deps.

```python
class AgentRouter:
    def __init__(
        self,
        profiles: Iterable[AgentProfile],
        *,
        threshold: float = 0.3,   # minimum match score to accept a route
        scoring_fn: Callable[[str, AgentProfile], float] | None = None,  # v0.20.0
        encoder = None,           # optional semantic-router encoder instance
    )

    def route(self, text: str) -> str | None
    # When scoring_fn is provided, calls it for each profile and picks the highest scorer.
    # Otherwise uses semantic-router (if installed) or difflib word-overlap.
    # Returns None if no match exceeds threshold.
```

Wiring into `task()`:

```python
router = AgentRouter(spec.subagents.values())
result = await sess.task("Review auth.py for security issues", router=router)
# router.route() called with the prompt; resolved name passed as agent=
```

---

## `AgentPruner` (`tvastar/router.py`)

Tracks per-profile quality scores and drops underperformers from the active pool. Inspired by the AgentDropout paper.

```python
class AgentPruner:
    def __init__(
        self,
        threshold: float = 50.0,  # min average score (0–100) to keep a profile
        *,
        min_runs: int = 1,         # minimum runs before a profile is eligible for pruning
    )

    def update(self, profile_name: str, result: RunResult) -> None
    # Record a RunResult. Calls score_run(result) internally.

    def avg_score(self, profile_name: str) -> float | None
    # Rolling average score for the profile, or None if not yet seen.

    def should_prune(self, profile_name: str) -> bool
    # True if the profile has min_runs and avg_score < threshold.

    def active(self, profiles: Iterable[AgentProfile]) -> list[AgentProfile]
    # Return profiles NOT pruned. Unseen profiles always included.

    def pruned(self, profiles: Iterable[AgentProfile]) -> list[AgentProfile]
    # Return only profiles that would be dropped.
```

Typical usage pattern:

```python
pruner = AgentPruner(threshold=60.0, min_runs=3)

# After every task result, record it against the profile that ran it
pruner.update(agent_name, result)

# Rebuild the router — prune before the next round
router = AgentRouter(pruner.active(all_profiles))
```

---

## `auto_topology()` (`tvastar/topology.py`)

Decomposes a natural-language goal into a `TaskGraph` + `list[AgentProfile]`. The planner uses the harness's existing model — no extra configuration.

```python
async def auto_topology(
    goal: str,
    *,
    harness: Harness,
    max_subtasks: int = 6,
    cancel_after: float = 60.0,
) -> tuple[TaskGraph, list[AgentProfile]]
```

Returns `(graph, profiles)` where `graph` is ready to `run()` and `profiles` is one `AgentProfile` per subtask role.

Raises `ValueError` if the planner returns invalid JSON or an unknown dependency. Raises `asyncio.TimeoutError` if planning exceeds `cancel_after`.

```python
graph, profiles = await auto_topology(
    "Research competitors, score pricing, write a strategy deck.",
    harness=harness,
    max_subtasks=5,
)
results = await graph.run()
print(results["strategy_deck"].text)
```

---

## Workflow (`tvastar/workflow.py`)

```python
def workflow(
    fn: WorkflowFn | None = None,
    *,
    name: str | None = None,
    registry: RunRegistry | None = None,
) -> Workflow
# Decorator — wraps an async function into a Workflow

class Workflow:
    name: str
    registry: RunRegistry

    async def run(
        self,
        payload: Any = None,
        *,
        run_id: str | None = None,
    ) -> WorkflowRun

    def get_run(self, run_id: str) -> WorkflowRun | None
    def list_runs(self) -> list[WorkflowRun]
    def logs(self, run_id: str) -> None    # prints human-readable event log

@dataclass
class WorkflowContext:
    run_id: str
    payload: Any

    @property
    def log(self) -> Logger        # .info(msg, **kw), .warn(), .error()

    async def init(
        self,
        spec: AgentSpec,
        *,
        store: Store | None = None,
        durable: bool = False,
    ) -> WorkflowHarness

class WorkflowHarness:
    async def session(self, name: str = "default") -> Session
    async def session_async(self, name: str = "default") -> Session
    @property fs: _WorkflowFS
    async def shell(self, cmd: str, *, timeout: float | None = None) -> str
    async def close(self) -> None

@dataclass
class WorkflowRun:
    run_id: str
    workflow_name: str
    status: RunStatus
    payload: Any
    output: Any | None
    error: str | None
    started_at: float
    ended_at: float | None
    events: list[RunEvent]

class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class RunEvent:
    type: str            # 'run_start'|'run_end'|'log'|'operation'|'error'
    at: float
    data: dict[str, Any]

class RunRegistry:
    def __init__(self, store: Store | None = None)
    def save(self, run: WorkflowRun) -> None
    def get(self, run_id: str) -> WorkflowRun | None
    def list_runs(self, workflow_name: str | None = None) -> list[WorkflowRun]
    def events(self, run_id: str) -> list[RunEvent]

    @classmethod
    def file_backed(cls, path: str = ".tvastar-runs") -> RunRegistry
```

---

## Dispatch (`tvastar/dispatch.py`)

```python
@dataclass
class DispatchInput:
    text: str = ""
    type: str = "chat.message"
    message_id: str | None = None
    metadata: dict[str, Any] = {}

@dataclass
class DispatchEvent:
    type: 'dispatch_start' | 'dispatch_end' | 'dispatch_error'
    dispatch_id: str
    agent_id: str
    session_id: str
    at: float
    data: dict[str, Any]

# v0.20.0 — DispatchPool: encapsulated dispatch state
class DispatchPool:
    """Isolated dispatch state — multiple pools run independently."""
    def __init__(self, max_harness_cache: int = 500)

    async def dispatch(self, spec, *, id, session=None, input=None, text=None,
                       store=None, on_complete=None, on_error=None,
                       cancel_after=None, tracer=None) -> str
    async def dispatch_and_wait(self, spec, *, id, session=None, input=None,
                                text=None, store=None, cancel_after=None,
                                tracer=None) -> RunResult
    def cancel(self, dispatch_id: str) -> bool
    def list_active(self) -> list[str]
    def observe(self, callback: Callable[[DispatchEvent], Any]) -> None
    def unobserve(self, callback: Callable[[DispatchEvent], Any]) -> bool
    def close(self) -> None   # release all cached harnesses and cancel active tasks

# Module-level functions (delegate to default pool for backward compat)
async def dispatch(
    spec: AgentSpec,
    *,
    id: str,
    session: str | None = None,
    input: DispatchInput | None = None,
    text: str | None = None,
    store: Store | None = None,
    on_complete: Callable[[RunResult], Any] | None = None,
    on_error: Callable[[Exception], Any] | None = None,
    cancel_after: float | None = None,
) -> str    # returns dispatch_id

async def dispatch_and_wait(
    spec: AgentSpec,
    *,
    id: str,
    session: str | None = None,
    input: DispatchInput | None = None,
    text: str | None = None,
    store: Store | None = None,
    cancel_after: float | None = None,
) -> RunResult

def observe_dispatch(callback: Callable[[DispatchEvent], Any]) -> None
def cancel_dispatch(dispatch_id: str) -> bool
def list_active_dispatches() -> list[str]
```

---

## Governance (`tvastar/masking.py`)

```python
@dataclass
class GovernancePolicy:
    """Phase-based capability enforcement at tool invocation time.

    Unlike masking (advisory, shapes what the model *sees*), governance
    intercepts inside Session._execute_tools and is tamper-proof against
    prompt injection.
    """
    phases: dict[str, set[str]]      # phase_name → set of allowed tool names; "*" = all
    current_phase: str = "default"
    approval_gate: ApprovalGate | None = None

    # Raises ValueError if phases={} — empty policies are rejected at construction.

    def set_phase(self, name: str) -> None
    # Raises ValueError for unknown names.

    def is_allowed(self, tool_name: str) -> bool
    # Fails closed: returns False for an unknown or uninitialised current_phase.

    def as_tool_policy(self) -> ToolPolicy
    # Returns a live ToolPolicy that mirrors current_phase on every call.
    # Wire as: create_agent(..., governance=gov, tool_policy=gov.as_tool_policy())

    def copy(self) -> GovernancePolicy
    # Shallow copy with independent current_phase — Harness.session() calls this
    # automatically so concurrent sessions cannot race on set_phase().
```

```python
# Masking helpers (ToolPolicy factories)
ToolPolicy = Callable[[MaskContext], Iterable[str]]

def allow_only(*names: str) -> ToolPolicy
def deny(*names: str) -> ToolPolicy
def phases(by_step: dict[int, Iterable[str]], *, default=None) -> ToolPolicy

@dataclass
class MaskContext:
    step: int
    available: list[str]
    messages: list[Message]
    active_skill: str | None
    @property last_tool_used: str | None
```

---

## Sandboxes (`tvastar/sandbox/`)

```python
class Sandbox(ABC):
    async def exec(self, cmd: str, *, env=None, cwd=None, timeout=None) -> ExecResult
    @property fs: FileSystem
    async def start(self) -> None
    async def stop(self) -> None

    # Snapshot / restore (v0.10.0)
    def snapshot(self) -> Any
    # Returns sandbox state; raises NotImplementedError if unsupported.

    def restore(self, snap: Any) -> None
    # Restores to a previous snapshot; raises NotImplementedError if unsupported.

class VirtualSandbox(Sandbox):
    # In-memory. snapshot() → dict[str, str]; restore() replaces fs contents.
    # < 150 ms on ~1 MB. Supports exec() via VirtualPython (no real subprocess).

class LocalSandbox(Sandbox):
    def __init__(
        self,
        root: str | Path = ".tvastar-workspace",
        *,
        policy: SecurityPolicy | None = None,
        resources: ResourcePolicy | None = None,
        credential_filter: CredentialFilter | None = None,
        shell: str | None = None,
    )
    # snapshot() → dict[str, bytes] — recursive walk of root, relative POSIX paths.
    # restore(snap) — deletes extra files, recreates snapshotted ones.
    # < 500 ms on ~500 KB.
    audit: list[AuditEntry]   # append-only log of every exec call

@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str = ""
    timed_out: bool = False

    @property def ok(self) -> bool   # exit_code == 0 and not timed_out
    def render(self) -> str          # human-readable stdout+stderr summary

@dataclass
class AuditEntry:
    """Append-only record of every exec() call on a sandbox."""
    command: str
    timestamp: float
    allowed: bool
    violation: str | None = None    # set when allowed=False
    exit_code: int | None = None    # set after completion
    duration_ms: float | None = None

    @classmethod
    def blocked(cls, command: str, reason: str) -> AuditEntry
    @classmethod
    def executed(cls, command: str, exit_code: int, duration_ms: float) -> AuditEntry

@dataclass
class ResourcePolicy:
    """CPU / memory / output limits for LocalSandbox."""
    max_cpu_seconds: float = 30.0
    max_memory_mb: int | None = None   # ulimit -v on Linux/macOS; no-op on Windows
    max_output_chars: int = 50_000
    allowed_domains: list[str] = field(default_factory=list)

@dataclass
class SecurityPolicy:
    """Command-allowlist / denylist for LocalSandbox."""
    network: bool = True                # False → zero out proxy env vars
    max_output_bytes: int = 256_000
    timeout_seconds: float = 60.0
    denied_commands: set[str] = field(default_factory=set)    # e.g. {"rm", "curl"}
    allowed_commands: set[str] = field(default_factory=set)   # empty = no allowlist
    denied_substrings: set[str] = field(default_factory=set)  # blocked anywhere in cmd

    def check(self, cmd: str) -> None   # raises SecurityViolation if blocked

@dataclass
class CredentialFilter:
    """Strips secret-looking env vars from the subprocess environment."""
    patterns: list[str] = field(default_factory=lambda: [
        "*_KEY", "*_TOKEN", "*_SECRET", "*_PASSWORD",
        "*_PASS", "*_CREDENTIAL", "*_CREDENTIALS",
    ])    # case-insensitive glob patterns

    def filter_env(self, env: dict[str, str]) -> dict[str, str]
```

---

## Long-Term Memory (`tvastar/contrib/ltm/`)

Optional contrib module — no extra dependencies for BM25 retrieval.
Install `sentence-transformers` and `numpy` for semantic cosine retrieval.

```python
from tvastar.contrib.ltm import LTMStore, LTMNode

@dataclass
class LTMNode:
    id: str
    type: str             # "factual" | "procedural"
    content: str
    tags: list[str] = []
    session_id: str = ""
    created_at: float = 0.0

class LTMStore:
    def __init__(
        self,
        store: Store,
        max_inject: int = 5,      # nodes injected per system prompt call
        semantic: bool = False,   # True → cosine similarity via sentence-transformers
    )

    async def consolidate(
        self,
        result: RunResult,
        model: Model,
        *,
        session_id: str = "",
    ) -> list[LTMNode]
    # Extracts factual/procedural nodes from result.messages via LLM.
    # Gates on result.stopped == "end_turn" (not result.ok).
    # Redacts credentials; sanitizes user messages against injection patterns.
    # Returns [] if the run did not complete or no nodes were extracted.

    def retrieve(self, query: str, *, k: int | None = None) -> list[LTMNode]
    # BM25-style keyword overlap by default; cosine similarity if semantic=True.

    def as_hook(self) -> Callable[..., str]
    # Returns a system_prompt_hook. Extended signature (auto-detected):
    #   hook(system_prompt: str, *, last_user_text: str = "") -> str
    # Retrieval is keyed on last_user_text when available (per-turn intent),
    # falling back to the system prompt for the first turn.

    def all_nodes(self) -> list[LTMNode]
    def clear(self) -> None
    def _save(self, node: LTMNode) -> None   # direct insert (test helper)
```

---

## Cost & Budget (`tvastar/cost.py`)

```python
@dataclass
class Cost:
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""

    @property
    def usd(self) -> float    # total USD cost for this run

    def __add__(self, other: Cost) -> Cost

def cost_for_model(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
) -> Cost
# Looks up per-token pricing. Covers: all Claude tiers, GPT-4o, o1, o3-mini,
# Llama via Groq. Add custom entries to COST_TABLE.

@dataclass
class BudgetPolicy:
    max_usd: float
    on_exceed: Literal["raise", "stop", "approve"] = "raise"
    # "raise"   → raises BudgetExceeded (default)
    # "stop"    → stops run with stopped="budget" (no exception)
    # "approve" → routes to the agent's approval_gate for human sign-off;
    #             falls back to "raise" if no gate is configured
    warn_at: float | None = 0.8   # warn when spend reaches this fraction of max_usd
```

---

## Approval (`tvastar/approval.py`)

```python
class ApprovalGate:
    """Human-in-the-loop gate — pauses the run and waits for human decision."""

    def __init__(
        self,
        backend: Literal["cli", "webhook", "event"] = "cli",
        *,
        webhook_url: str | None = None,   # for backend="webhook"
        on_request: Callable[[ApprovalRequest], Any] | None = None,  # for backend="event"
        timeout: float = 300.0,
    )

    async def request(
        self,
        message: str,
        *,
        timeout: float | None = None,
        metadata: dict | None = None,
    ) -> None
    # Raises ApprovalDenied or ApprovalTimeout if the human denies or times out.

@dataclass
class ApprovalRequest:
    message: str
    timeout: float = 300.0
    metadata: dict = field(default_factory=dict)

    def approve(self) -> None   # unblocks the waiting agent
    def deny(self) -> None      # raises ApprovalDenied in the agent

async def require_approval(
    message: str,
    *,
    timeout: float = 300.0,
) -> None
# Convenience shortcut — calls the default gate set by set_default_gate()

def set_default_gate(gate: ApprovalGate) -> None
```

Wire to an agent:

```python
agent = create_agent("assistant", model=..., approval_gate=ApprovalGate(backend="cli"))
```

Wire governance-specific approval:

```python
gov = GovernancePolicy(phases={"read": {"grep"}, "write": {"*"}},
                       approval_gate=ApprovalGate(backend="cli"))
agent = create_agent("assistant", model=..., governance=gov)
```

---

## Compaction (`tvastar/compaction.py`)

```python
@dataclass
class CompactionPolicy:
    max_messages: int = 60
    max_tokens_estimate: int = 80_000
    keep_last: int = 10
    min_messages: int = 20
    summary_instruction: str = "..."
    token_estimator: Callable[[list[Message]], int] | None = None
    summary_model: Model | None = None   # override model used for summarisation
    # None → uses the session's own model (default)

def should_compact(messages: list[Message], policy: CompactionPolicy) -> bool

async def compact_messages(
    messages: list[Message],
    model: Model,
    policy: CompactionPolicy,
    *,
    system: str | None = None,
) -> list[Message]
# Returns: [compact_notice_msg, summary_msg, *tail(keep_last)]

async def compact_session(
    session: Session,
    *,
    policy: CompactionPolicy | None = None,  # falls back to spec.compaction
    force: bool = False,
) -> bool    # True if compaction was performed
```

---

## Memory (`tvastar/memory/store.py`)

```python
class Store(Protocol):
    def get(self, key: str) -> Any | None
    def set(self, key: str, value: Any) -> None
    def delete(self, key: str) -> None
    def keys(self, prefix: str = "") -> list[str]

class InMemoryStore(Store): ...   # default — process-lifetime

class FileStore(Store):           # persists to JSON files on disk
    def __init__(self, path: str)

class Memory:
    # Namespaced KV store (scoped to a session or workflow)
    def get(self, key: str) -> Any | None
    def set(self, key: str, value: Any) -> None
    def delete(self, key: str) -> None
    def keys(self) -> list[str]
```

---

## Observability (`tvastar/observability.py`)

```python
class Tracer:
    def __init__(self, exporters: list[Exporter] | None = None)
    @contextmanager
    def span(self, name: str, **attributes) -> Iterator[Span]

@dataclass
class Span:
    name: str
    attributes: dict[str, Any]
    span_id: str
    parent_id: str | None
    start: float
    end: float | None
    status: str                    # 'ok' | 'error: <type>'
    @property duration_ms: float | None

class ConsoleExporter:             # human-readable to stderr
class JSONLExporter:               # append-only JSONL file
    def __init__(self, path: str = "tvastar-trace.jsonl")
class OTelExporter:                # OpenTelemetry bridge (optional dep)

NULL_TRACER = Tracer()             # zero-overhead default
```

---

## Detection (`tvastar/detect/`)

```python
@dataclass
class Finding:
    detector: str
    severity: Severity
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)
    # (was named 'context' in older docs — renamed to 'evidence' to avoid
    # confusion with Python's built-in context concept)

    def __str__(self) -> str   # "detector [SEVERITY]: message"

class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

@dataclass
class ToolEvent:
    """One tool call + its result, as seen by detectors."""
    call: ToolUseBlock
    result: ToolResultBlock | None
    step: int

@dataclass
class RunContext:
    """Snapshot passed to every detector after the run completes."""
    messages: list[Message]
    tools: ToolRegistry
    stopped: str        # 'end_turn'|'max_steps'|'error'|'memory_cap'
    final_text: str

    @property def tool_calls(self) -> list[ToolUseBlock]
    @property def tool_results(self) -> list[ToolResultBlock]
    @property def events(self) -> list[ToolEvent]        # paired calls+results
    @property def last_tool_result(self) -> ToolResultBlock | None

# A detector is: Callable[[RunContext], list[Finding]]

def default_detectors() -> list[Callable]
# Returns: unknown_tool, schema_mismatch, thrash_loop, ignored_tool_error,
#          unverified_completion, prompt_injection, empty_answer, step_limit

def run_detectors(ctx: RunContext, detectors: list) -> list[Finding]
```

---

## MCP Client (`tvastar/mcp/`)

```python
class MCPClient:
    server_info: dict[str, Any]

    @classmethod
    def stdio(cls, command: str, args: list[str] | None = None, **kw) -> MCPClient

    @classmethod
    def http(cls, url: str, **kw) -> MCPClient

    async def connect(self) -> MCPClient
    async def close(self) -> None
    async def __aenter__(self) -> MCPClient
    async def __aexit__(self, *exc) -> None

    @property tools: list[Tool]
    def tool_names(self) -> list[str]
    async def call_tool(self, name: str, arguments: dict) -> str

async def connect_mcp_server(
    *,
    command: str | None = None,
    args: list[str] | None = None,
    url: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    **kw,
) -> MCPClient
```

---

## Errors (`tvastar/errors.py`)

```python
class TvastarError(Exception): ...
class ModelError(TvastarError): ...      # model API failure
class ToolError(TvastarError): ...       # tool execution failure
class ToolNotFound(ToolError): ...       # unknown tool name
class SkillError(TvastarError): ...      # skill load/exec failure
class SandboxError(TvastarError): ...    # sandbox start/exec failure
class SecurityViolation(SandboxError): ... # blocked command
class DurableError(TvastarError): ...    # checkpoint failure
```

---

## Skills (`tvastar/skills/`)

```python
@dataclass
class Skill:
    name: str
    description: str
    instructions: str
    tools: list[str] | None = None    # None = all tools allowed
    metadata: dict[str, Any] = field(default_factory=dict)  # extra frontmatter fields
    source: str | None = None         # file path the skill was loaded from

    def summary(self) -> str          # one-line description for catalog

class SkillLibrary:
    def __init__(self, skills: list[Skill] | None = None)

    @classmethod
    def from_dirs(cls, *dirs: str) -> SkillLibrary
    # Loads all *.md files in the given directories.

    @classmethod
    def from_workspace(cls) -> SkillLibrary
    # Auto-discovers skills from .agents/skills/ relative to the working directory.
    # Used by LocalSandbox harnesses to load workspace-local skills.

    def get(self, name: str) -> Skill    # raises SkillError if not found
    def names(self) -> list[str]
    def catalog(self) -> str             # formatted for system prompt injection

def parse_skill(path: str) -> Skill     # parse a single .md file
```

Skill Markdown format:

```markdown
---
name: my-skill
description: One-line description for the model to understand when to use this
tools: [read_file, grep]              # optional — restricts available tools
---

Full instructions for the agent when this skill is active.
```

---

## Durable execution (`tvastar/durable.py`)

```python
class Checkpointer:
    def __init__(self, store: Store)

    def save(
        self,
        session_id: str,
        *,
        messages: list[Message],
        fs_snapshot: dict[str, str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None

    def load(self, session_id: str) -> dict | None
    def exists(self, session_id: str) -> bool
    def list_sessions(self) -> list[str]
```

---

## Filesystem (`tvastar/filesystem/`)

```python
@dataclass
class GrepMatch:
    """One line of a grep result."""
    path: str      # relative path from sandbox root
    line_no: int
    line: str

# Available on FileSystem (accessible from tools via ctx.filesystem):
class FileSystem:
    def read(self, path: str) -> str
    def write(self, path: str, content: str) -> None
    def exists(self, path: str) -> bool
    def delete(self, path: str) -> None
    def listdir(self, path: str = ".") -> list[str]
    def glob(self, pattern: str) -> list[str]
    def grep(self, pattern: str, *, glob: str = "**/*") -> list[GrepMatch]
```

---

## Task Graph (`tvastar/graph.py`)

```python
class TaskGraph:
    """DAG-based parallel task execution."""

    def task(
        self,
        name: str,
        prompt: str,
        *,
        depends_on: list[str] | None = None,
        cancel_after: float | None = None,
        result: Any | None = None,    # structured output schema
        agent: str | None = None,     # AgentProfile name for this task
    ) -> TaskGraph    # returns self for chaining

    async def run(
        self,
        *,
        inject_results: bool = True,  # prepend dependency output to downstream prompts
        concurrency: int = 8,         # semaphore cap; 0 = unlimited
    ) -> GraphResult

@dataclass
class GraphResult:
    results: dict[str, RunResult]
    findings: dict[str, list[Finding]] = field(default_factory=dict)

    def __getitem__(self, name: str) -> RunResult
    def __iter__(self) -> Iterator[str]           # iterates task names
    def __len__(self) -> int

    @property def ok(self) -> bool                # all tasks succeeded with no warnings
    @property def text(self) -> dict[str, str]    # {task_name: result.text}
    @property def all_findings(self) -> list[Finding]  # flat list across all tasks
```

---

## Deploy adapters (`tvastar/deploy/`)

```python
from tvastar.deploy import asgi_app, lambda_handler, serverless_handler, run_github_action

def asgi_app(spec: AgentSpec, *, store: Store | None = None) -> Any
# Returns a FastAPI/Starlette ASGI app — deploy to Fly, Render, Cloud Run, etc.

def lambda_handler(spec: AgentSpec) -> Callable
# Returns an AWS Lambda handler: handler(event, context) -> dict

def serverless_handler(spec: AgentSpec) -> Callable
# Returns fn({"prompt": "..."}) -> {"text": "..."} for GCP/Azure/Vercel

def run_github_action(spec: AgentSpec) -> None
# Reads INPUT_PROMPT env var, runs agent, writes step outputs — call in __main__
```

---

## Loop Engineering (`tvastar/loop/`)

> **v0.11.0+**  `Loop = Agent + Schedule + Verify + Handoff`

---

### `LoopState` — lifecycle enum

```python
class LoopState(str, Enum):
    IDLE          = "idle"          # waiting for next trigger
    TRIGGERED     = "triggered"     # trigger() called, run not yet started
    RUNNING       = "running"       # agent is executing
    VERIFYING     = "verifying"     # checking result against detectors
    PASS          = "pass"          # goal met; resets to IDLE
    FAIL          = "fail"          # goal not met; may retry or handoff
    RETRY         = "retry"         # backing off before next attempt
    HANDOFF       = "handoff"       # retries exhausted; escalating
    HANDOFF_FAILED = "handoff_failed"  # handoff policy itself threw
    INTERRUPTED   = "interrupted"   # process crashed while RUNNING (crash recovery)
    SUSPENDED     = "suspended"     # circuit breaker: too many consecutive failures
```

---

### `FailureKind` — why a run failed

```python
class FailureKind(str, Enum):
    TIMEOUT      = "timeout"       # cancel_after fired
    MODEL_ERROR  = "model_error"   # provider API error
    LOGIC_ERROR  = "logic_error"   # agent ran but goal not met (result.ok False)
    DETECTION    = "detection"     # silent-failure detector fired
    UNKNOWN      = "unknown"       # unexpected exception
```

---

### `LoopRun` — one iteration's record

```python
@dataclass
class LoopRun:
    run_id: str
    loop_name: str
    state: LoopState
    iteration: int             # which attempt within this handoff cycle
    started_at: float          # unix timestamp
    ended_at: float | None
    result_text: str | None    # final assistant text (metadata only — not full messages)
    result_steps: int | None
    result_stopped: str | None
    findings: list             # from silent-failure detectors
    failure_kind: FailureKind | None
    retry_after: float | None  # unix timestamp: honour backoff before retrying
    error: str | None          # exception message if run errored
    context: dict              # arbitrary key=value carried between retries

    @property
    def ok(self) -> bool           # True iff state == PASS
    @property
    def duration(self) -> float | None
```

---

### `LoopEvent` — emitted on every state transition

```python
@dataclass
class LoopEvent:
    loop_name: str
    run_id: str
    state: LoopState
    at: float
    data: dict
```

---

### `LoopConfig` — validated at construction

```python
@dataclass
class LoopConfig:
    name: str                        # loop identity key for checkpointing
    goal: str                        # plain-language objective passed to agent each run
    schedule: str = "@manual"        # cron expr | @daily | @hourly | … | @manual
    max_iterations: int = 3          # retries before HANDOFF (per cycle)
    cancel_after: float | None = None  # per-run timeout in seconds (strongly recommended)
    retry_backoff_base: float = 30.0 # seconds: 30 → 60 → 120 with exponential growth
    circuit_breaker_limit: int = 5   # consecutive HANDOFF cycles → SUSPENDED
    handoff: HandoffPolicy | None = None
    meta_model: Model | None = None  # one-shot instruction rewriter after FAIL
    optimizer: Callable | None = None  # Callable[[str, list[LoopRun]], str]; takes precedence over meta_model
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None
    # Validates: name non-empty, goal non-empty, max_iterations >= 1,
    # retry_backoff_base >= 0, and the cron schedule parses correctly.
    # Raises ValueError at construction time — never at 2am.
```

---

### `Loop` — the core primitive

```python
class Loop:
    def __init__(
        self,
        spec: AgentSpec,
        config: LoopConfig,
        store: Store | None = None,   # default: InMemoryStore; use FileStore for durability
        tracer: Tracer | None = None,
    ) -> None

    # Properties
    @property
    def name(self) -> str
    @property
    def state(self) -> LoopState
    @property
    def config(self) -> LoopConfig

    # Public API
    async def trigger(self, context: dict | None = None) -> LoopRun
    # Run one iteration now (regardless of schedule).
    # Raises RuntimeError if SUSPENDED, already RUNNING, or inside backoff window.

    async def start(self) -> None
    # Start the background cron scheduler (no-op for @manual loops).

    async def stop(self) -> None
    # Cancel the background scheduler gracefully.

    def reset(self) -> None
    # Clear SUSPENDED state and reset circuit breaker counter. Manual intervention.

    def on_event(self, fn: Callable[[LoopEvent], None]) -> None
    # Register a listener called on every state transition.

    def history(self, limit: int = 50) -> list[LoopRun]
    def last_run(self) -> LoopRun | None

    # Werner-hardened internals (overridable in subclasses)
    async def _run_iteration(self, run: LoopRun, context: dict) -> None
    def _build_prompt(self, context: dict) -> str
```

---

### Handoff policies (`tvastar/loop/handoff.py`)

```python
class HandoffPolicy(ABC):
    @abstractmethod
    async def escalate(self, run: LoopRun, history: list[LoopRun]) -> None: ...

class LogHandoff(HandoffPolicy):
    # Prints a structured escalation report to stderr.
    # Default when no handoff is configured.

class CallbackHandoff(HandoffPolicy):
    def __init__(self, fn: Callable[[LoopRun, list[LoopRun]], Awaitable[None]]) -> None
    # Calls an async function with (run, history).

class MultiHandoff(HandoffPolicy):
    def __init__(self, policies: list[HandoffPolicy]) -> None
    # Fires all policies. Collects and re-raises all failures after all run.
```

---

### Cron scheduler (`tvastar/loop/schedule.py`)

```python
def next_run_time(expr: str, after: datetime) -> datetime
# Returns the next UTC datetime when expr fires strictly after `after`.
# Supports @yearly @annually @monthly @weekly @daily @midnight @hourly aliases.
# Supports full 5-field cron: MIN HOUR DOM MON DOW (with ranges, steps, comma lists).
# Raises ValueError for @manual or malformed expressions.
```

---

### Readiness audit (`tvastar/loop/audit.py`)

```python
@dataclass
class ReadinessLevel:
    level: int                    # 0–3
    name: str                     # MANUAL | OBSERVE | GATED | AUTONOMOUS
    description: str
    passes: list[str]             # checks that pass
    gaps: list[str]               # blocking gaps (fix to advance level)
    warnings: list[str]           # non-blocking advisories

    @property
    def is_production_ready(self) -> bool   # True iff level >= 3

def audit_loop(loop: Loop) -> ReadinessLevel
# Pure function — does not start or run the loop.
# Checks: schedule, handoff, cancel_after, detectors, circuit_breaker_limit.
# Raises TypeError if loop is not a Loop instance.
```

| Level | Name | Gate checks |
|-------|------|------------|
| L0 | MANUAL | Loop object exists |
| L1 | OBSERVE | + schedule != @manual + handoff configured |
| L2 | GATED | + cancel_after is not None |
| L3 | AUTONOMOUS | + detectors non-empty + circuit_breaker_limit > 0 |

---

### Pre-built patterns (`tvastar/loop/patterns/`)

All patterns inherit from `Loop`. All ship with hardened instructions and `_VERIFY_FOOTER`
requiring explicit SUCCESS/PARTIAL/FAILURE in every run.

```python
class CISweeper(Loop):
    def __init__(
        self,
        model: Model,
        *,
        schedule: str = "*/15 * * * *",
        max_iterations: int = 3,
        handoff: HandoffPolicy | None = None,    # default: LogHandoff
        tools: list | None = None,               # default: default_toolset()
        extra_instructions: str = "",
    ) -> None

class PRBabysitter(Loop):
    def __init__(self, model, *, schedule="*/30 * * * *", max_iterations=2, ...)

class DailyTriage(Loop):
    def __init__(self, model, *, schedule="0 9 * * *", max_iterations=2, ...)

class DependencySweeper(Loop):
    def __init__(self, model, *, schedule="0 3 * * *", max_iterations=2, ...)

class PostMergeCleanup(Loop):
    def __init__(self, model, *, schedule="*/30 * * * *", max_iterations=2, ...)

class ChangelogDrafter(Loop):
    def __init__(self, model, *, schedule="0 9 * * 1", max_iterations=2, ...)

class MakerChecker(Loop):
    """Two-agent verification: Maker proposes, Checker independently verifies."""
    def __init__(
        self,
        maker_model: Model,
        checker_model: Model,
        goal: str,
        *,
        name: str = "maker-checker",
        schedule: str = "@manual",
        max_rounds: int = 3,             # Maker+Checker cycles before HANDOFF
        handoff: HandoffPolicy | None = None,
        cancel_after: float | None = None,
        maker_tools: list | None = None,
        checker_tools: list | None = None,
        extra_maker_instructions: str = "",
        extra_checker_instructions: str = "",
        store: Store | None = None,
        tracer: Tracer | None = None,
    ) -> None
    # Checker must respond with APPROVED or REJECTED (fail-safe: no verdict = REJECTED).
    # retry_backoff_base=0.0 so checker feedback is addressed immediately.
    # Checker timeout/error → MODEL_ERROR (counted against round limit, not swallowed).
```

---

### Loop CLI (`tvastar loop`)

```
tvastar loop init <Pattern> [--name NAME] [--out PATH]
tvastar loop run  <ref>
tvastar loop status <ref>
tvastar loop audit <ref>
```

`<ref>` format: `path/to/file.py:loop_var` or `module.path:loop_var` (defaults `attr` to `loop`).

```python
# Equivalent Python API (all commands are also callable functions)
from tvastar.loop.cli import cmd_init, cmd_run, cmd_status, cmd_audit

cmd_init("CISweeper", name=None, out=None)      # → int exit code
cmd_run(".tvastar/loops/ci_sweeper.py:loop")    # → int exit code
cmd_status(".tvastar/loops/ci_sweeper.py:loop") # → int exit code
cmd_audit(".tvastar/loops/ci_sweeper.py:loop")  # → int exit code (0 only at L3)
```

**Exit codes:** `run` and `audit` return 0 on success (PASS / L3), 1 on failure — safe for
use as CI gates: `tvastar loop audit .tvastar/loops/ci.py:loop || exit 1`

---

## `tvastar.assurance` — Verifiable Execution

> Added in v0.15.0. Provides cryptographically-signed per-run receipts, an append-only
> chain-linked audit log, PII redaction before hashing, configurable retention, and
> quality SLA enforcement.

```python
from tvastar.assurance import (
    AssurancePolicy,     # attach to create_agent()
    ExecutionReceipt,    # available on result.receipt after each run
    TrustLog,            # append-only chain-linked WORM log
    SanitizationPolicy,  # PII/PHI redaction applied before hashing
    RetentionPolicy,     # archive old entries for SOX/HIPAA/GDPR schedules
    SLABreached,         # exception raised when quality_score < min_score
)
```

---

### `AssurancePolicy`

Attach to an agent via `create_agent(..., assurance=policy)`. Controls signing,
logging, quality enforcement, and PII redaction.

```python
@dataclass
class AssurancePolicy:
    key: str = ""
    # HMAC-SHA256 signing key for receipt signatures.
    # If empty, reads TVASTAR_RECEIPT_KEY env var at runtime.
    # If still empty, signature field is "" (unsigned but still hashed).

    log: TrustLog | None = None
    # If set, every completed receipt is appended to this log.

    min_score: int = 0
    # Quality SLA threshold (0–100). If receipt.quality_score < min_score,
    # the on_fail handler is invoked.

    on_fail: Literal["ignore", "raise", "escalate"] = "ignore"
    # "ignore"   → log and continue
    # "raise"    → raise SLABreached(receipt)
    # "escalate" → call on_escalate callback (falls back to "raise" if None)

    on_escalate: Callable[[ExecutionReceipt], None] | None = None
    # Called when on_fail="escalate". Receives the failing receipt.

    sanitize: SanitizationPolicy | None = None
    # Applied before hashing: scrubs PII/PHI from prompt, tool calls, and
    # final_text in the receipt. Does NOT alter the actual model conversation.
```

---

### `ExecutionReceipt`

Immutable record of one agent run. Available as `result.receipt` after any
`harness.run()`, `sess.prompt()`, or `sess.task()` when an `AssurancePolicy`
is configured.

```python
@dataclass
class ExecutionReceipt:
    run_id: str              # "run_<hex12>" — unique per run
    agent: str               # AgentSpec.name
    model_name: str          # e.g. "claude-sonnet-4-6"
    prompt: str              # user prompt (sanitized if SanitizationPolicy set)
    tool_calls: list[dict]   # [{id, name, input, output}, ...] — all calls in order
    final_text: str          # last assistant message text (sanitized if set)
    findings: list[dict]     # serialized Finding objects from silent-failure detectors
    approvals: list[dict]    # [{tool, approved_by, approved_at, message}, ...]
    usage_input: int         # total input tokens consumed
    usage_output: int        # total output tokens consumed
    quality_score: int       # 0–100; computed from findings and stop reason
    quality_grade: str       # "PASS" (≥80) | "WARN" (50–79) | "FAIL" (<50)
    stopped: str             # "end_turn" | "max_steps" | "error"
    started_at: float        # epoch seconds
    completed_at: float      # epoch seconds
    prev_hash: str           # content_hash of the preceding receipt; "" for first
    content_hash: str        # "sha256:<hex64>" — over all fields above
    signature: str           # "hmac-sha256:<hex64>"; "" if no key configured
    version: str             # receipt schema version; currently "2"

    def verify(self, key: str = "") -> bool
    # Recomputes content_hash and HMAC. Returns True if both match.
    # Pass the same key used in AssurancePolicy (or TVASTAR_RECEIPT_KEY env).

    def to_json(self) -> str
    # Serializes to a compact JSON string. Round-trips cleanly with from_json().

    @classmethod
    def from_json(cls, s: str) -> ExecutionReceipt
    # Deserializes from a JSON string produced by to_json().

    @classmethod
    def from_dict(cls, d: dict) -> ExecutionReceipt
    # Constructs from a plain dict (e.g. parsed from JSONL log line).

    def to_audit_report(self, *, fmt: Literal["text", "html"] = "text") -> str
    # Renders a human-readable or HTML audit report for regulators.
    # Includes: run metadata, tool call table, findings, quality grade, hash chain.
```

---

### `TrustLog`

Append-only, chain-linked log of `ExecutionReceipt` objects. Each receipt's
`prev_hash` field points to the `content_hash` of the entry before it, forming
a tamper-evident chain. Raising `ValueError` on any chain break makes silent
tampering detectable.

```python
class TrustLog:
    def __init__(
        self,
        path: str | None = None,
        # JSONL file path. Each line is one receipt JSON.
        # None = in-memory only (lost on process exit).
        *,
        on_breach: Callable[[ExecutionReceipt], None] | None = None,
        # Called with the first tampered receipt found by verify_chain().
        can_read: Callable[[str], bool] | None = None,
        # Role-based read predicate. Called as can_read(role).
        # If it returns False, get() and iter_as() raise PermissionError.
        # __iter__ bypasses access control (for internal iteration).
    )

    def append(self, receipt: ExecutionReceipt) -> None
    # Appends receipt to the log. Sets receipt.prev_hash = tail_hash before
    # writing. Raises ValueError if the incoming receipt's prev_hash does not
    # match tail_hash (chain integrity violation).

    def verify_chain(self) -> bool
    # Walks every entry and re-verifies content_hash and prev_hash linkage.
    # Calls on_breach with the first failing receipt and returns False.
    # Returns True only if all entries are intact.

    def get(self, run_id: str, *, role: str = "") -> ExecutionReceipt | None
    # Returns the receipt with the given run_id, or None if not found.
    # Raises PermissionError if can_read is set and can_read(role) returns False.

    def iter_as(self, role: str) -> Iterator[ExecutionReceipt]
    # Iterates all receipts. Raises PermissionError if can_read(role) is False.

    def apply_retention(self, policy: RetentionPolicy) -> int
    # Identifies entries eligible for archival per policy.
    # If policy.hold_until is set and time.time() < hold_until, returns 0.
    # If policy.archive_path is set, eligible entries are appended to that JSONL
    # file and removed from the live log.
    # Returns the count of eligible entries (archived or counted-only).

    def to_jsonl(self) -> str
    # Returns all entries serialized as newline-delimited JSON.

    @property
    def tail_hash(self) -> str
    # content_hash of the most-recently appended receipt; "" if log is empty.

    def __iter__(self) -> Iterator[ExecutionReceipt]
    # Iterates all entries with no access-control check.

    def __len__(self) -> int
    # Number of entries in the log.
```

---

### `SanitizationPolicy`

Regex-based PII/PHI scrubber applied to receipt fields before hashing. Does not
modify the live conversation — only the content written into `ExecutionReceipt`.

```python
class SanitizationPolicy:
    patterns: list[tuple[re.Pattern, str]]
    # List of (compiled_pattern, replacement_string) pairs applied in order.

    redact_prompt: bool = True     # scrub receipt.prompt
    redact_tools: bool = True      # scrub receipt.tool_calls inputs and outputs
    redact_answer: bool = True     # scrub receipt.final_text

    @classmethod
    def hipaa(cls) -> SanitizationPolicy
    # Redacts: SSN, date-of-birth, phone numbers, email addresses,
    # IP addresses, bearer tokens, API keys.

    @classmethod
    def pci(cls) -> SanitizationPolicy
    # Redacts: credit card numbers (Luhn-aware), CVV/CVC codes,
    # bearer tokens, API keys.

    @classmethod
    def gdpr(cls) -> SanitizationPolicy
    # Redacts: email addresses, phone numbers, IP addresses,
    # date-of-birth, bearer tokens.

    @classmethod
    def all_pii(cls) -> SanitizationPolicy
    # Union of hipaa() + pci() + gdpr() — broadest coverage.

    @classmethod
    def presidio(
        cls,
        languages: list[str] = ["en"],
        entities: list[str] | None = None,
        # None → all 50+ Presidio entity types.
        # Explicit list e.g. ["PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER"].
        score_threshold: float = 0.5,
        # Minimum confidence to redact. Lower = more aggressive.
    ) -> SanitizationPolicy
    # ML-powered detection via Microsoft Presidio.
    # Requires: pip install tvastar[presidio]
    # Model download: python -m spacy download en_core_web_lg

    def add_pattern(
        self,
        pattern: str | re.Pattern,
        replacement: str,
    ) -> SanitizationPolicy
    # Appends a custom regex rule. Returns self for chaining.

    def scrub(self, text: str) -> str
    # Applies all patterns to a string. Returns the redacted result.

    def scrub_tool_calls(self, tool_calls: list[dict]) -> list[dict]
    # Applies scrub() to the input and output values of each tool call dict.

    def apply(
        self,
        *,
        prompt: str,
        tool_calls: list,
        final_text: str,
    ) -> tuple[str, list, str]
    # Convenience: applies redact_prompt / redact_tools / redact_answer flags
    # and returns (sanitized_prompt, sanitized_tool_calls, sanitized_final_text).
```

---

### `RetentionPolicy`

Describes which log entries are eligible for archival. Passed to
`TrustLog.apply_retention()`.

```python
@dataclass
class RetentionPolicy:
    max_age_days: int | None = None
    # Archive entries whose started_at is older than this many days.
    # None = no age-based archival.

    hold_until: float | None = None
    # Epoch timestamp. If time.time() < hold_until, apply_retention() returns 0
    # (legal hold — nothing is archived regardless of age).

    archive_path: str | None = None
    # Path to a JSONL file where archived entries are appended.
    # None = count eligible entries only, do not move them.
```

---

### `SLABreached`

Raised by the session when `AssurancePolicy.on_fail == "raise"` and
`receipt.quality_score < policy.min_score`.

```python
class SLABreached(Exception):
    receipt: ExecutionReceipt
    # The failing receipt — inspect receipt.quality_score, receipt.findings,
    # and receipt.quality_grade for details.
```

Example:

```python
try:
    result = await sess.prompt("Analyze this data")
except SLABreached as e:
    print(f"Quality score {e.receipt.quality_score} below threshold")
    print(e.receipt.findings)
```

---

### `TokenVault` (`tvastar.assurance.sanitize`)

Reversible PII tokenization. The model receives opaque tokens instead of real values; `rehydrate()` swaps them back in the response.

```python
class TokenVault:
    def __init__(self) -> None

    def tokenize(self, text: str, policy: SanitizationPolicy) -> str
    # Apply policy patterns to text, storing originals in vault.
    # Returns tokenized text with <<LABEL_N>> placeholders.

    def rehydrate(self, text: str) -> str
    # Replace all <<LABEL_N>> tokens in text with the originals captured during tokenize().

    def __len__(self) -> int           # number of tokens recorded
```

Token format: `<<EMAIL_1>>`, `<<US_SSN_2>>`, etc. — derived from the policy's pattern labels. Multiple occurrences of the same type get unique counters.

```python
from tvastar.assurance import SanitizationPolicy, TokenVault

vault = TokenVault()
clean   = vault.tokenize(prompt, SanitizationPolicy.hipaa())
result  = await sess.prompt(clean)
final   = vault.rehydrate(result.text)
```

---

## `DSPyOptimizer` (`tvastar/loop/optimize.py`)

Systematic instruction optimizer for self-improving loops. Wraps DSPy `ChainOfThought` to rewrite agent instructions from failure evidence. Requires `pip install tvastar[dspy]`.

```python
class DSPyOptimizer:
    def __init__(
        self,
        model: str = "gpt-4o",   # DSPy model string (litellm-compatible)
        *,
        max_demos: int = 3,       # max PASS runs to use as few-shot examples
        max_fails: int = 5,       # max FAIL runs to include in failure evidence
        **lm_kwargs,              # forwarded to dspy.LM(model, **lm_kwargs)
    )

    def __call__(
        self,
        instructions: str,
        runs: list[LoopRun],
    ) -> str
    # Returns improved instructions, or instructions unchanged if DSPy returns empty.
```

Plug into `LoopConfig.optimizer` — takes precedence over `meta_model`:

```python
from tvastar.loop.optimize import DSPyOptimizer

config = LoopConfig(
    name="ci",
    goal="Keep build green.",
    optimizer=DSPyOptimizer("claude-sonnet-4-6"),
)
```

The optimizer callable signature `(instructions: str, runs: list[LoopRun]) -> str` is stable — any callable that matches it works as an optimizer, not just `DSPyOptimizer`.

---

## Registration APIs (v0.20.0)

Runtime-extensible registration for costs, injection patterns, and overflow phrases.

### `register_model_cost()` (`tvastar.cost`)

```python
def register_model_cost(
    model_name: str,
    input_per_million: float,
    output_per_million: float,
) -> None
# Register or update model pricing at runtime.
# New entries available immediately for all subsequent cost calculations.
# Re-registering an existing model name updates that entry.
```

### `register_injection_pattern()` (`tvastar.boundary`)

```python
def register_injection_pattern(name: str, pattern: re.Pattern) -> None
# Register or replace a named injection detection pattern.
# Subsequent scan_for_injection() calls include the new pattern.
# Same name replaces old pattern.
```

### `register_overflow_phrase()` (`tvastar.session`)

```python
def register_overflow_phrase(phrase: str) -> None
# Add a phrase to the overflow detection set (case-insensitive).
# Subsequent overflow checks include the new phrase.
```

---

## Protocol Types (v0.20.0)

All protocols in `tvastar.types` are `@runtime_checkable`. Use `isinstance()` to verify.

```python
from tvastar.types import (
    Detector,          # __call__(ctx: RunContext) -> list[Finding]
    ApprovalGate,      # async request(message, **kwargs) -> bool
    BudgetPolicy,      # max_usd, on_exceed, should_warn(cost), attribute(cost)
    ToolPolicy,        # __call__(ctx) -> Iterable[str]
    GovernancePolicy,  # current_phase, set_phase(), is_allowed(), enforce(), copy()
    AssurancePolicy,   # log, min_score, on_fail, key, enforce_sla(receipt)
    AgentPruner,       # update(name, result), active(profiles), should_prune(name)
    ToolRetryPolicy,   # max_attempts, should_retry(exc), sleep_for(attempt)
)
```

---

## CompactionPolicy (v0.20.0 additions)

```python
@dataclass
class CompactionPolicy:
    max_messages: int = 60
    max_tokens_estimate: int = 80_000
    keep_last: int = 10
    min_messages: int = 20
    summary_instruction: str = "..."
    summary_model: Any | None = None

    # v0.20.0
    cooldown: float = 30.0              # seconds between reactive compaction attempts
    summary_max_tokens: int = 1024      # max_tokens for summary generation model call
    summary_temperature: float = 0.3    # temperature for summary generation model call
```

---

## Session (v0.20.0 additions)

```python
@dataclass
class Session:
    # ... existing fields ...
    last_checkpoint_error: Exception | None = None   # v0.20.0: queryable checkpoint failure
```

---

## Extension Points Usage (v0.20.0)

### Middleware

```python
def log_middleware(messages):
    print(f"Sending {len(messages)} messages")
    return messages

agent = create_agent("x", model=model, middleware=[log_middleware])
```

### Fallback Models

```python
agent = create_agent(
    "resilient",
    model=primary_model,
    fallback_models=[backup_model_1, backup_model_2],
)
# On primary failure (non-overflow), tries each fallback in order.
# Overflow errors bypass fallbacks — handled by compaction instead.
```

### Stop Predicate

```python
agent = create_agent(
    "bounded",
    model=model,
    stop_predicate=lambda result: "DONE" in result.text,
)
# Loop ends with stopped="predicate" when the predicate returns True.
```

### Tool Hooks

```python
def audit_hook(name, args):
    log.info(f"Calling {name} with {args}")
    return None  # don't modify

def redact_hook(name, args, result):
    return result.replace("SECRET", "***")

agent = create_agent("x", model=model, pre_tool_hook=audit_hook, post_tool_hook=redact_hook)
```

---

## Compliance Copilot (`tvastar/comply/`)

Continuous compliance layer. Zero runtime deps beyond stdlib.

### Core Functions

```python
from tvastar.comply import audit_compliance, verify_pii_protection

def audit_compliance(
    loop: Any,
    *,
    framework: str | None = None,       # None defaults to "EU_AI_Act"
    registry: FrameworkRegistry | None = None,
) -> AuditResult
# Pure function. Fault-isolated — never raises into the calling agent loop.
# Returns NON_COMPLIANT with remediation on exception.

def verify_pii_protection(
    receipt: ExecutionReceipt,
    vault_configured: bool,
) -> PIIVerificationRecord
# Scans receipt prompt for 7 PII patterns + counts opaque tokens.
```

### Data Models

```python
from tvastar.comply import (
    AuditResult, PIIVerificationRecord, ComplianceAlert,
    LoopStatus, FleetSummary, ComplianceCostReport, RetentionAction,
)

@dataclass
class AuditResult:
    loop_name: str
    status: str                    # "COMPLIANT" | "NON_COMPLIANT"
    framework: str
    checks: list[ArticleCheck]
    pii_verification: PIIVerificationRecord | None
    timestamp: float
    remediation: list[str]

@dataclass
class PIIVerificationRecord:
    vault_active: bool
    token_count: int
    leak_count: int
    content_hash: str
    leaked_types: list[str]

@dataclass
class ComplianceAlert:
    severity: str                  # "INFO" | "WARNING" | "CRITICAL"
    alert_type: str                # "DRIFT" | "CHAIN_BREACH" | "PII_LEAK"
    loop_name: str
    run_id: str
    timestamp: float
    description: str
    suppression_count: int = 0

@dataclass
class FleetSummary:
    total: int
    compliant: int
    non_compliant: int
    stale: int
    fleet_compliance_pct: float
    per_loop: list[LoopStatus]
    compliance_overhead: dict[str, float] | None

@dataclass
class ComplianceCostReport:
    loop_name: str
    compliance_tokens: int
    total_tokens: int
    overhead_ratio: float
    window_start: float
    window_end: float
```

### FrameworkRegistry

```python
from tvastar.comply import FrameworkRegistry, RegulatoryFramework

class FrameworkRegistry:
    def __init__(self) -> None            # EU_AI_Act registered by default
    def register(self, framework: RegulatoryFramework) -> None
    def get(self, name: str) -> RegulatoryFramework | None
    def get_checks(self, name: str | None = None) -> list[FrameworkCheck]
    def list_frameworks(self) -> list[str]
```

### AlertEngine

```python
from tvastar.comply import AlertEngine, StderrSink, FileSink, CallbackSink

class AlertEngine:
    def __init__(self, sinks: list[AlertSink] | None = None, suppression_window: float = 300.0)
    def emit(self, alert: ComplianceAlert) -> bool   # True if delivered (not suppressed)
```

### ComplianceDashboard

```python
from tvastar.comply import ComplianceDashboard

class ComplianceDashboard:
    def __init__(self, *, check_interval: float = 60.0)
    def update(self, loop_name: str, result: AuditResult) -> None
    def query(self) -> FleetSummary
    def to_json(self) -> str
```

### WatchDaemon

```python
from tvastar.comply import WatchDaemon

class WatchDaemon:
    def __init__(self, loops: list, *, interval: float = 60.0, alert_engine=None, dashboard=None, framework=None, retention_manager=None)
    async def start(self) -> None    # runs indefinitely; logs config to stderr
    async def stop(self) -> None     # graceful shutdown
    # Raises ValueError if loops is empty
```

### RetentionManager

```python
from tvastar.comply import RetentionManager, FRAMEWORK_RETENTION

# FRAMEWORK_RETENTION = {"SOX": 2555, "HIPAA": 2190, "GDPR": 1825, "GLBA": 1825, "DORA": 1825}

class RetentionManager:
    def __init__(self, trust_log: TrustLog, framework: str = "EU_AI_Act")
    def activate_hold(self) -> RetentionAction
    def release_hold(self) -> RetentionAction
    def is_held(self) -> bool
    def check_approaching_expiry(self, within_days: int = 30) -> int
    def apply_retention(self) -> RetentionAction
```

### CostTracker

```python
from tvastar.comply import CostTracker

class CostTracker:
    def __init__(self, *, alert_engine=None, threshold: float = 0.15)
    def record_compliance_tokens(self, loop_name: str, run_id: str, tokens: int) -> None
    def record_business_tokens(self, loop_name: str, run_id: str, tokens: int) -> None
    def overhead_ratio(self, loop_name: str) -> float
    def fleet_overhead(self) -> dict[str, float]
    def report(self, loop_name: str | None = None, *, window_hours: float = 24.0) -> list[ComplianceCostReport]
```

### ReportGenerator

```python
from tvastar.comply import ReportGenerator

class ReportGenerator:
    def __init__(self, trust_log: TrustLog)
    def generate(self, run_id: str, *, fmt: str = "text", include_pii_proof: bool = True, output: str | None = None) -> str
    # Raises KeyError if run_id not found
```

### Configuration

```python
from tvastar.comply import load_config, ComplianceConfig

def load_config(path: str) -> ComplianceConfig
# JSON (stdlib) or YAML (optional PyYAML). Raises ComplianceError on invalid config.
```

### CLI

```bash
tvastar-comply audit <loop> [--framework EU_AI_Act] [--config comply.json] [--format json|text]
tvastar-comply report <run_id> [--output report.html] [--fmt html|text|json]
tvastar-comply watch [--config comply.json]
tvastar-comply dashboard [--format json|text]
tvastar-comply compliance-cost [--window-hours 24] [--format json|text]
```

Exit codes: 0 success, 1 operational error, 2 compliance violation.
