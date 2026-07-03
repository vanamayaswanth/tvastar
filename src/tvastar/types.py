"""Core data types shared across Tvastar.

These are intentionally lightweight dataclasses so the framework has zero
runtime dependencies in its core. Provider adapters translate to/from these.

Protocol types define the structural contracts for pluggable policies and
extension points. They use ``@runtime_checkable`` so users can verify their
implementations satisfy the interface with ``isinstance()`` checks.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    Iterable,
    Literal,
    Optional,
    Protocol,
    Union,
    runtime_checkable,
)

if TYPE_CHECKING:  # pragma: no cover
    from .detect.base import Finding, RunContext
    from .profiles import AgentProfile


# ---------------------------------------------------------------------------
# Protocol types — structural interfaces for AgentSpec pluggable fields
# ---------------------------------------------------------------------------


@runtime_checkable
class Detector(Protocol):
    """A failure detector: inspects a completed run context and returns findings.

    Detectors are pure functions over a run transcript. They must never raise —
    the runner isolates failures and reports them as findings.
    """

    def __call__(self, ctx: "RunContext") -> "list[Finding]": ...


@runtime_checkable
class ApprovalGate(Protocol):
    """Human-in-the-loop approval gate.

    Pauses an agent run and waits for a human to approve or reject before
    proceeding. Implementations may use CLI prompts, webhooks, Slack, etc.
    """

    async def request(self, message: str, **kwargs: Any) -> bool: ...


@runtime_checkable
class BudgetPolicy(Protocol):
    """Cost ceiling enforcement policy applied during agent runs.

    The session checks ``max_usd`` and ``on_exceed`` to decide when to stop,
    calls ``should_warn`` to emit warning findings, and ``attribute`` to
    route per-step costs to phases.
    """

    max_usd: float
    on_exceed: str

    def should_warn(self, cost: Any) -> bool: ...

    def attribute(self, cost: Any) -> None: ...


@runtime_checkable
class ToolPolicy(Protocol):
    """Dynamic tool-masking policy controlling which tools are visible per step.

    Receives the current mask context and returns the tool names to expose.
    """

    def __call__(self, ctx: Any) -> Iterable[str]: ...


@runtime_checkable
class GovernancePolicy(Protocol):
    """Invocation-layer enforcement policy for phase-based tool access control.

    Unlike masking (which hides tools from the model's view), governance runs
    after the model has requested a tool call — inside tool execution.
    """

    current_phase: str
    approval_gate: Optional[ApprovalGate]

    def set_phase(self, name: str) -> None: ...

    def is_allowed(self, tool_name: str) -> bool: ...

    async def enforce(
        self, tool_name: str, tool_use_id: str = ""
    ) -> Optional["ToolResultBlock"]: ...

    def copy(self) -> "GovernancePolicy": ...


@runtime_checkable
class AssurancePolicy(Protocol):
    """Verifiable-execution policy producing signed receipts and enforcing SLAs."""

    log: Any
    min_score: int
    on_fail: str
    key: str

    def enforce_sla(self, receipt: Any) -> None: ...


@runtime_checkable
class AgentPruner(Protocol):
    """Demotes underperforming AgentProfiles based on observed run quality.

    Auto-updated after ``sess.task()`` completes so slow/failing agents are
    demoted before the next routing decision.
    """

    def update(self, profile_name: str, result: Any) -> None: ...

    def active(self, profiles: "Iterable[AgentProfile]") -> "list[AgentProfile]": ...

    def should_prune(self, profile_name: str) -> bool: ...


@runtime_checkable
class ToolRetryPolicy(Protocol):
    """Automatic retry policy for transient tool failures.

    The tool executor uses ``max_attempts`` to cap retries, ``should_retry``
    to decide if an exception is transient, and ``sleep_for`` to compute
    backoff duration.
    """

    max_attempts: int

    def should_retry(self, exc: Exception) -> bool: ...

    def sleep_for(self, attempt: int) -> float: ...


Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class TextBlock:
    """A chunk of plain text in a message."""

    text: str
    type: Literal["text"] = "text"


@dataclass
class ToolUseBlock:
    """A request from the model to invoke a tool."""

    name: str
    input: dict[str, Any]
    id: str = field(default_factory=lambda: f"call_{uuid.uuid4().hex[:12]}")
    type: Literal["tool_use"] = "tool_use"


@dataclass
class ToolResultBlock:
    """The result of a tool invocation, fed back to the model."""

    tool_use_id: str
    content: str
    is_error: bool = False
    type: Literal["tool_result"] = "tool_result"


@dataclass
class ImageBlock:
    """An image in a user message, forwarded to vision-capable models.

    Args:
        data: Base64-encoded image bytes, or a URL when ``source_type="url"``.
        media_type: MIME type — ``image/jpeg``, ``image/png``, ``image/gif``,
                    or ``image/webp``.
        source_type: ``"base64"`` (default) or ``"url"``.
    """

    data: str
    media_type: str = "image/jpeg"
    source_type: str = "base64"
    type: Literal["image"] = "image"


ContentBlock = Union[TextBlock, ImageBlock, ToolUseBlock, ToolResultBlock]


@dataclass
class Message:
    """A single turn in a conversation.

    `content` is either a plain string (sugar for a single TextBlock) or a
    list of content blocks. Normalize with `.blocks`.
    """

    role: Role
    content: Union[str, list[ContentBlock]]
    id: str = field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:12]}")
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def blocks(self) -> list[ContentBlock]:
        if isinstance(self.content, str):
            return [TextBlock(text=self.content)]
        return self.content

    @property
    def text(self) -> str:
        """Concatenated text of all text blocks."""
        return "".join(b.text for b in self.blocks if isinstance(b, TextBlock))

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        return [b for b in self.blocks if isinstance(b, ToolUseBlock)]


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

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


@dataclass
class ModelResponse:
    """A model's reply to a list of messages."""

    message: Message
    stop_reason: StopReason
    usage: Usage = field(default_factory=Usage)
    raw: Optional[Any] = None

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        return self.message.tool_uses


@dataclass
class ToolSpec:
    """Provider-agnostic description of a callable tool."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class StreamEvent:
    """Emitted during a streaming agent run for observability/UX."""

    type: Literal[
        "text_delta",
        "tool_call",
        "tool_result",
        "turn_start",
        "turn_end",
        "skill_loaded",
        "task_spawned",
        "error",
    ]
    data: dict[str, Any] = field(default_factory=dict)
    at: float = field(default_factory=time.time)
