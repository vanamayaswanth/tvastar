"""Tool masking — show the model only the tools it should consider *this turn*.

Exposing every tool on every turn burns context and, worse, invites the model
to reach for tools that are irrelevant to the current phase of work. A
``ToolPolicy`` is a pure function that, given the live conversation state,
returns the subset of tool names to expose for the next model call. The harness
applies it before each ``model.generate`` — so masking is dynamic, not static.

Design notes
------------
- A policy returns an iterable of tool *names*. The harness intersects that with
  the tools actually available this turn (after skill-scoping), so a policy can
  never *grant* a tool the agent doesn't have — only hide ones it does.
- Masking must never break a run: if a policy raises, the harness falls back to
  exposing all available tools and carries on.
- This is plain, inspectable Python. The helpers below cover the common cases
  (allow-list, deny-list, phase-by-step); for anything richer, pass your own
  ``Callable[[MaskContext], Iterable[str]]``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .types import Message


@dataclass
class MaskContext:
    """Read-only view of the run state a :data:`ToolPolicy` decides against."""

    step: int  # 1-based model-call index within this prompt
    available: list[str]  # tool names available this turn (post skill-scope)
    messages: "list[Message]" = field(default_factory=list)
    active_skill: Optional[str] = None

    @property
    def last_tool_used(self) -> Optional[str]:
        """Name of the most recently invoked tool, if any (handy for phasing)."""
        from .types import ToolUseBlock

        for m in reversed(self.messages):
            for b in m.blocks:
                if isinstance(b, ToolUseBlock):
                    return b.name
        return None


#: A tool-masking policy: given the run state, return the tool names to expose.
ToolPolicy = Callable[[MaskContext], Iterable[str]]


def allow_only(*names: str) -> ToolPolicy:
    """Expose only ``names`` (intersected with what's available)."""
    allowed = set(names)

    def policy(ctx: MaskContext) -> list[str]:
        return [n for n in ctx.available if n in allowed]

    policy.__name__ = "allow_only"
    return policy


def deny(*names: str) -> ToolPolicy:
    """Expose everything available *except* ``names``."""
    blocked = set(names)

    def policy(ctx: MaskContext) -> list[str]:
        return [n for n in ctx.available if n not in blocked]

    policy.__name__ = "deny"
    return policy


def phases(
    by_step: dict[int, Iterable[str]], *, default: Optional[Iterable[str]] = None
) -> ToolPolicy:
    """Expose different tools at different steps.

    ``by_step`` maps a *minimum* step to the tool names allowed from that step
    onward; the highest matching threshold wins. Before the first threshold (or
    when no threshold matches) ``default`` is used — ``None`` means "all
    available".

    Example::

        # research first, then allow writes once we've read enough
        phases({1: ["grep", "read_file"], 4: ["grep", "read_file", "write_file"]})
    """
    thresholds = sorted(by_step.items())

    def policy(ctx: MaskContext) -> list[str]:
        chosen: Optional[Iterable[str]] = default
        for min_step, names in thresholds:
            if ctx.step >= min_step:
                chosen = names
        if chosen is None:
            return list(ctx.available)
        allowed = set(chosen)
        return [n for n in ctx.available if n in allowed]

    policy.__name__ = "phases"
    return policy


def apply_policy(policy: Optional[ToolPolicy], ctx: MaskContext) -> Optional[set[str]]:
    """Resolve a policy to a set of allowed names, or ``None`` for "all".

    Never raises: a misbehaving policy yields ``None`` (expose everything) so
    masking can't take a run down.
    """
    if policy is None:
        return None
    try:
        return set(policy(ctx))
    except Exception:
        return None
