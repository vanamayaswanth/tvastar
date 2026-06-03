"""Tools: typed Python functions the agent can invoke.

Define a tool with the ``@tool`` decorator. Sync or async functions both work.
A tool may declare a first parameter named ``ctx`` to receive a ToolContext
(giving access to the sandbox, filesystem, session memory, etc.) — it is
injected by the executor and never exposed to the model.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TYPE_CHECKING

from ..errors import ToolError, ToolNotFound
from ..types import ToolSpec
from .schema import schema_from_callable

if TYPE_CHECKING:  # pragma: no cover
    from ..filesystem.base import FileSystem
    from ..sandbox.base import Sandbox


@dataclass
class ToolContext:
    """Runtime handles injected into tools that request ``ctx``."""

    sandbox: Optional["Sandbox"] = None
    filesystem: Optional["FileSystem"] = None
    memory: Any = None
    session: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Tool:
    name: str
    description: str
    fn: Callable[..., Any]
    input_schema: dict[str, Any]
    wants_ctx: bool = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.input_schema)

    async def invoke(self, args: dict[str, Any], ctx: Optional[ToolContext] = None) -> str:
        """Execute the tool, returning a string result for the model.

        Sync tools run in a thread to avoid blocking the event loop — keeps the
        harness responsive and scalable under concurrent sessions.
        """
        call_args = dict(args)
        if self.wants_ctx:
            call_args["ctx"] = ctx or ToolContext()
        try:
            if asyncio.iscoroutinefunction(self.fn):
                result = await self.fn(**call_args)
            else:
                result = await asyncio.to_thread(self.fn, **call_args)
        except ToolError:
            raise
        except TypeError as e:
            raise ToolError(f"Invalid arguments for tool '{self.name}': {e}") from e
        except Exception as e:
            raise ToolError(f"Tool '{self.name}' failed: {e}") from e
        return _stringify(result)


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


def tool(
    fn: Optional[Callable] = None,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> Any:
    """Decorator turning a function into a :class:`Tool`.

    Usage::

        @tool
        def add(a: int, b: int) -> int:
            "Add two numbers."
            return a + b
    """

    def wrap(f: Callable) -> Tool:
        sig = inspect.signature(f)
        wants_ctx = "ctx" in sig.parameters
        desc = description or (inspect.getdoc(f) or "").strip() or f.__name__
        # Use only the first paragraph of the docstring as the description.
        desc = desc.split("\n\n")[0].strip()
        return Tool(
            name=name or f.__name__,
            description=desc,
            fn=f,
            input_schema=schema_from_callable(f),
            wants_ctx=wants_ctx,
        )

    if fn is not None:
        return wrap(fn)
    return wrap


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
