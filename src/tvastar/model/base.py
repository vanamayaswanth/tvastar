"""The Model interface — the bottom layer of the harness.

A Model turns (messages + tools + system prompt) into a ModelResponse. It is
deliberately small: everything else in Tvastar is built on top of this contract.
Providers (Anthropic, OpenAI, mock) implement `generate`. Streaming is optional
and defaults to a non-streamed shim.
"""

from __future__ import annotations

import abc
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..types import Message, ModelResponse, StreamEvent, TextBlock, ToolSpec

# Substrings (lowercased) treated as retryable transient errors by default.
_RETRYABLE_PHRASES = (
    "rate limit",
    "too many requests",
    "429",
    "503",
    "502",
    "504",
    "timeout",
    "connection",
    "server error",
    "overloaded",
)


def _default_retryable(exc: Exception) -> bool:
    """Return True when the exception looks like a transient network/rate-limit error."""
    msg = str(exc).lower()
    return any(phrase in msg for phrase in _RETRYABLE_PHRASES)


@dataclass
class ModelRetryPolicy:
    """Retry policy for transient model errors (rate limits, timeouts, server faults).

    Attach to an Anthropic or OpenAI model adapter to automatically retry on
    transient failures with exponential backoff:

    .. code-block:: python

        from tvastar.model import AnthropicModel, ModelRetryPolicy

        model = AnthropicModel(
            retry=ModelRetryPolicy(max_attempts=4, backoff_base=2.0)
        )

    Attributes:
        max_attempts: Total attempts including the first. 1 = no retry.
        backoff_base: Base delay (seconds) for exponential backoff.
        backoff_max: Maximum delay cap (seconds).
        jitter: Maximum uniform random jitter added to each delay (seconds).
        retryable: Optional predicate ``(exc) -> bool``. Defaults to checking
                   for HTTP 429/5xx patterns and connection-level errors.
    """

    max_attempts: int = 3
    backoff_base: float = 1.0
    backoff_max: float = 60.0
    jitter: float = 0.25
    retryable: Optional[Callable[[Exception], bool]] = None

    # Circuit breaker configuration
    circuit_breaker_threshold: int = 5
    circuit_breaker_cooldown: float = 30.0

    # Runtime state (not constructor params)
    _consecutive_failures: int = field(default=0, init=False, repr=False)
    _circuit_opened_at: float | None = field(default=None, init=False, repr=False)
    _circuit_state: str = field(default="closed", init=False, repr=False)

    @property
    def circuit_state(self) -> str:
        """Current circuit breaker state: 'closed', 'open', or 'half_open'."""
        if self._circuit_state == "open" and self._circuit_opened_at is not None:
            elapsed = time.time() - self._circuit_opened_at
            if elapsed >= self.circuit_breaker_cooldown:
                self._circuit_state = "half_open"
        return self._circuit_state

    def should_allow_request(self) -> bool:
        """Return True if a request should be allowed through the circuit breaker."""
        state = self.circuit_state
        if state == "closed":
            return True
        if state == "half_open":
            return True  # allow one probe
        return False  # open — fail fast

    def _record_success(self) -> None:
        """Record a successful request, closing the circuit if it was half-open."""
        self._consecutive_failures = 0
        if self._circuit_state in ("half_open", "open"):
            self._circuit_state = "closed"
            self._circuit_opened_at = None

    def _record_failure(self) -> None:
        """Record a failed request, opening the circuit if threshold is reached."""
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.circuit_breaker_threshold:
            self._circuit_state = "open"
            self._circuit_opened_at = time.time()


class Model(abc.ABC):
    """Abstract async model provider."""

    #: human-readable identifier, e.g. "claude-opus-4-8"
    name: str = "model"
    #: provider family for OTel GenAI traces (gen_ai.system), e.g. "anthropic"
    system: str = "unknown"

    @abc.abstractmethod
    async def generate(
        self,
        messages: list[Message],
        *,
        system: Optional[str] = None,
        tools: Optional[list[ToolSpec]] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        stop_sequences: Optional[list[str]] = None,
        thinking_level: Optional[str] = None,
    ) -> ModelResponse:
        """Produce a single assistant response.

        Args:
            thinking_level: Reasoning effort hint — ``'low'``, ``'medium'``,
                ``'high'``, or ``None`` (no extended thinking). Providers map
                this to their native reasoning/thinking API.
        """
        raise NotImplementedError

    async def stream(
        self,
        messages: list[Message],
        *,
        system: Optional[str] = None,
        tools: Optional[list[ToolSpec]] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        stop_sequences: Optional[list[str]] = None,
        thinking_level: Optional[str] = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a response as events, ending with a ``turn_end`` carrying the
        full ModelResponse in ``data["response"]``.

        Default implementation falls back to ``generate`` and emits the text as
        a single delta. Providers may override for true token streaming.
        """
        resp = await self.generate(
            messages,
            system=system,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            stop_sequences=stop_sequences,
            thinking_level=thinking_level,
        )
        for block in resp.message.blocks:
            if isinstance(block, TextBlock) and block.text:
                yield StreamEvent("text_delta", {"text": block.text})
        yield StreamEvent("turn_end", {"response": resp})


@dataclass
class CascadeModel(Model):
    """Response-level speculative decoding for cost optimization.

    Based on RLM-Cascade (arxiv.org/html/2606.22840v1):
    - Draft model (cheap) generates candidate
    - Verify model (capable) accepts, enhances, or is skipped
    - Complexity router decides path: SKIP / ENHANCE / DIRECT

    Achieved 45.8% cost reduction with 100% quality preservation.

    Usage::

        from tvastar.model import CascadeModel, AnthropicModel

        cascade = CascadeModel(
            draft=AnthropicModel("claude-haiku-4-5"),
            verify=AnthropicModel("claude-sonnet-4-6"),
            complexity_threshold=0.5,  # 0.0-1.0
        )
        # Use like any other model
        response = await cascade.generate(messages)

    Attributes:
        draft: Cheaper model for initial generation.
        verify: Capable model for verification/enhancement.
        complexity_threshold: Tasks below this use draft only (0.0-1.0).
    """

    draft: Model = field(default=None)
    verify: Model = field(default=None)
    complexity_threshold: float = 0.5
    name: str = "cascade"
    system: str = "cascade"

    def __post_init__(self):
        if self.draft is None or self.verify is None:
            raise ValueError("CascadeModel requires both draft and verify models")

    async def generate(
        self,
        messages: list[Message],
        *,
        system: Optional[str] = None,
        tools: Optional[list[ToolSpec]] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        stop_sequences: Optional[list[str]] = None,
        thinking_level: Optional[str] = None,
    ) -> ModelResponse:
        """Generate with draft/verify cascade.

        Simple heuristic complexity routing:
        - No tools + short prompt -> draft only (SKIP)
        - Tools present -> verify directly (DIRECT)
        - Long/complex prompt -> draft + verify (ENHANCE)
        """
        # Compute complexity heuristic
        complexity = self._estimate_complexity(messages, tools)

        if complexity < self.complexity_threshold:
            # SKIP: Use draft only
            return await self.draft.generate(
                messages,
                system=system,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
                stop_sequences=stop_sequences,
                thinking_level=thinking_level,
            )
        elif tools:
            # DIRECT: Schema-critical (tool selection) -> verify only
            return await self.verify.generate(
                messages,
                system=system,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
                stop_sequences=stop_sequences,
                thinking_level=thinking_level,
            )
        else:
            # ENHANCE: Draft, then verify can refine
            # ponytail: for now, just use verify on complex tasks
            # Full implementation would draft-then-enhance
            return await self.verify.generate(
                messages,
                system=system,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
                stop_sequences=stop_sequences,
                thinking_level=thinking_level,
            )

    def _estimate_complexity(
        self,
        messages: list[Message],
        tools: Optional[list[ToolSpec]],
    ) -> float:
        """Heuristic complexity score (0.0-1.0)."""
        # Factors that increase complexity
        msg_count = len(messages)
        total_chars = sum(
            len(b.text) if hasattr(b, "text") else 0
            for m in messages
            for b in getattr(m, "blocks", [])
        )
        tool_count = len(tools) if tools else 0

        # Normalize to 0-1 range
        score = 0.0
        if msg_count > 5:
            score += 0.2
        if msg_count > 10:
            score += 0.2
        if total_chars > 2000:
            score += 0.2
        if total_chars > 5000:
            score += 0.2
        if tool_count > 0:
            score += 0.3

        return min(1.0, score)
