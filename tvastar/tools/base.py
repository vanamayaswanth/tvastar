"""Tools: typed Python functions the agent can invoke.

Define a tool with the ``@tool`` decorator. Sync or async functions both work.
A tool may declare a first parameter named ``ctx`` to receive a ToolContext
(giving access to the sandbox, filesystem, session memory, etc.) — it is
injected by the executor and never exposed to the model.

Tool retry
----------
Transient failures (network timeouts, rate limits, flaky I/O) can be retried
automatically. Attach a ``ToolRetryPolicy`` to a ``Tool`` or set a
harness-wide default via ``create_agent(tool_retry=...)``:

    from tvastar.tools import ToolRetryPolicy

    @tool(retry=ToolRetryPolicy(max_attempts=3, backoff_base=0.5))
    async def call_api(url: str) -> str:
        ...

    # Or harness-wide:
    agent = create_agent(..., tool_retry=ToolRetryPolicy(max_attempts=3))
"""

from __future__ import annotations

import asyncio
import inspect
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TYPE_CHECKING

from ..errors import ToolError, ToolNotFound
from ..types import ToolSpec
from .schema import schema_from_callable

if TYPE_CHECKING:  # pragma: no cover
    from ..filesystem.base import FileSystem
    from ..sandbox.base import Sandbox


# ── ToolRetryPolicy ───────────────────────────────────────────────────────────


@dataclass
class ToolRetryPolicy:
    """Configures automatic retry for transient tool failures.

    Attributes:
        max_attempts: Total number of attempts (1 = no retry).
        backoff_base: Base sleep in seconds. Actual sleep is
            ``backoff_base * 2 ** attempt + jitter``.
        backoff_max: Cap on sleep duration per backoff step (seconds).
        jitter: Maximum random jitter added to each backoff (seconds).
        retryable: A callable ``(exc) -> bool`` that decides whether the
            exception is transient. Defaults to retrying everything except
            ``ToolNotFound`` and argument ``TypeError``s.
    """

    max_attempts: int = 3
    backoff_base: float = 0.5
    backoff_max: float = 10.0
    jitter: float = 0.1
    retryable: Optional[Callable[[Exception], bool]] = None

    def should_retry(self, exc: Exception) -> bool:
        if self.retryable is not None:
            return self.retryable(exc)
        # Default: don't retry type/argument errors — those won't get better
        return not isinstance(exc, (ToolNotFound, TypeError))

    def sleep_for(self, attempt: int) -> float:
        """Exponential backoff with full jitter."""
        base = self.backoff_base * (2**attempt)
        capped = min(base, self.backoff_max)
        return capped + random.uniform(0, self.jitter)


# ── ToolContext ───────────────────────────────────────────────────────────────


@dataclass
class ToolContext:
    """Runtime handles injected into tools that request ``ctx``."""

    sandbox: Optional["Sandbox"] = None
    filesystem: Optional["FileSystem"] = None
    memory: Any = None
    session: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


# ── Tool ─────────────────────────────────────────────────────────────────────


@dataclass
class Tool:
    name: str
    description: str
    fn: Callable[..., Any]
    input_schema: dict[str, Any]
    wants_ctx: bool = False
    retry: Optional[ToolRetryPolicy] = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.input_schema)

    async def invoke(
        self,
        args: dict[str, Any],
        ctx: Optional[ToolContext] = None,
        *,
        default_retry: Optional[ToolRetryPolicy] = None,
    ) -> str:
        """Execute the tool, returning a string result for the model.

        Sync tools run in a thread to avoid blocking the event loop — keeps the
        harness responsive and scalable under concurrent sessions.

        Retry behaviour:
        - Tool-level ``retry`` takes precedence over ``default_retry``.
        - If neither is set, no retry is applied.
        """
        policy = self.retry or default_retry
        call_args = dict(args)
        if self.wants_ctx:
            call_args["ctx"] = ctx or ToolContext()

        last_exc: Optional[Exception] = None
        max_attempts = policy.max_attempts if policy else 1

        for attempt in range(max_attempts):
            try:
                if asyncio.iscoroutinefunction(self.fn):
                    result = await self.fn(**call_args)
                else:
                    result = await asyncio.to_thread(self.fn, **call_args)
                return _stringify(result)
            except ToolError:
                raise  # already formatted — don't retry
            except TypeError as e:
                raise ToolError(f"Invalid arguments for tool '{self.name}': {e}") from e
            except Exception as e:
                last_exc = e
                if policy is None or not policy.should_retry(e):
                    raise ToolError(f"Tool '{self.name}' failed: {e}") from e
                if attempt < max_attempts - 1:
                    await asyncio.sleep(policy.sleep_for(attempt))

        raise ToolError(
            f"Tool '{self.name}' failed after {max_attempts} attempts: {last_exc}"
        ) from last_exc


# ── helpers ───────────────────────────────────────────────────────────────────


def _stringify(result: Any) -> str:
    if result is None:
        return "ok"
    if isinstance(result, str):
        return result
    try:
        import json

        return json.dumps(result, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(result)


# ── @tool decorator ───────────────────────────────────────────────────────────


def tool(
    fn: Optional[Callable] = None,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    retry: Optional[ToolRetryPolicy] = None,
) -> Any:
    """Decorator turning a function into a :class:`Tool`.

    Usage::

        @tool
        def add(a: int, b: int) -> int:
            "Add two numbers."
            return a + b

        @tool(retry=ToolRetryPolicy(max_attempts=3))
        async def flaky_api(url: str) -> str:
            ...
    """

    def wrap(f: Callable) -> Tool:
        sig = inspect.signature(f)
        wants_ctx = "ctx" in sig.parameters
        desc = description or (inspect.getdoc(f) or "").strip() or f.__name__
        desc = desc.split("\n\n")[0].strip()
        return Tool(
            name=name or f.__name__,
            description=desc,
            fn=f,
            input_schema=schema_from_callable(f),
            wants_ctx=wants_ctx,
            retry=retry,
        )

    if fn is not None:
        return wrap(fn)
    return wrap


# ── ToolRegistry ──────────────────────────────────────────────────────────────


class ToolRegistry:
    """A name->Tool map with spec export and execution."""

    def __init__(self, tools: Optional[list[Tool]] = None):
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.add(t)

    def add(self, t: Tool) -> None:
        if not isinstance(t, Tool):
            raise ToolError(f"Expected Tool, got {type(t).__name__}. Use @tool.")
        self._tools[t.name] = t

    def extend(self, tools: list[Tool]) -> None:
        for t in tools:
            self.add(t)

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise ToolNotFound(f"No tool named '{name}'")
        return self._tools[name]

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    @property
    def specs(self) -> list[ToolSpec]:
        return [t.spec for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)
