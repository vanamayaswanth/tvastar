# Tvastar API Reference

Complete API reference for Tvastar v0.10.0. Every public symbol, field, and signature.

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
    budget: BudgetPolicy | None = None,
    approval_gate: ApprovalGate | None = None,
    tool_policy: ToolPolicy | None = None,
    governance: GovernancePolicy | None = None,
    system_prompt_hook: Callable[..., str] | None = None,
    memory_cap_mb: float | None = None,   # session memory ceiling in MB
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
    max_tokens: int                        # default 4096
    temperature: float                     # default 1.0
    thinking_level: str | None             # 'low'|'medium'|'high'|None
    detectors: list                        # silent-failure detectors
    compaction: CompactionPolicy | None
    tool_retry: ToolRetryPolicy | None
    subagents: dict[str, AgentProfile]
    budget: BudgetPolicy | None            # cost ceiling (None=unlimited)
    approval_gate: ApprovalGate | None     # human-in-the-loop gate
    tool_policy: ToolPolicy | None         # per-turn masking (advisory)
    governance: GovernancePolicy | None    # invocation-layer enforcement (tamper-proof)
    system_prompt_hook: Callable[..., str] | None  # augments system prompt before each call
    memory_cap_mb: float | None            # session memory ceiling in MB (None=unlimited)
    metadata: dict[str, Any]

    def build_system_prompt(self, *, last_user_text: str = "") -> str
    # Applies system_prompt_hook if set; hook failure warns and falls back gracefully.
    # Extended hooks that declare last_user_text receive the most-recent user message.

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
    stopped: str                     # 'end_turn' | 'max_steps' | 'error'
    findings: list[Finding]          # from silent-failure detectors
    data: Any | None                 # populated when result= schema used

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
class ModelResponse:
    message: Message
    stop_reason: StopReason
    usage: Usage
    raw: Any | None                  # provider's raw response
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
    type: 'text_delta'|'tool_call'|'tool_result'|'turn_start'|'turn_end'|'error'
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
class AnthropicModel(Model):
    def __init__(
        self,
        model: str = "claude-opus-4-8",
        *,
        api_key: str | None = None,    # default: ANTHROPIC_API_KEY env
        client: Any | None = None,     # inject pre-built AsyncAnthropic
    )
    # thinking_level: low→1024, medium→8000, high→16000 budget_tokens
    # + interleaved-thinking-2025-05-14 beta header
    # + forces temperature=1.0 when thinking enabled

class OpenAIModel(Model):
    def __init__(
        self,
        model: str = "gpt-4o",
        *,
        api_key: str | None = None,    # default: OPENAI_API_KEY env
        base_url: str | None = None,   # for compatible providers
        client: Any | None = None,
    )
    # thinking_level: passed as reasoning_effort='low'|'medium'|'high'

class MockModel(Model):
    name = "mock"
    def __init__(self, script: list[str | ToolUseBlock | Message] | None = None)
    calls: list[list[Message]]         # inspect after test
    # thinking_level: accepted, noted in echo text
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
    **metadata,
) -> AgentProfile
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
    stderr: str
    timed_out: bool = False
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
    context: dict[str, Any] = {}

class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

@dataclass
class RunContext:
    messages: list[Message]
    tools: ToolRegistry
    stopped: str
    final_text: str

# A detector is: Callable[[RunContext], list[Finding]]

def default_detectors() -> list[Callable]
# Returns: unknown_tool, schema_mismatch, thrash_loop, ignored_tool_error,
#          unverified_completion, empty_answer, step_limit

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

class SkillLibrary:
    def __init__(self, skills: list[Skill] | None = None)

    @classmethod
    def from_dirs(cls, *dirs: str) -> SkillLibrary   # loads *.md files

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
