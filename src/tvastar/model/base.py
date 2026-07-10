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
