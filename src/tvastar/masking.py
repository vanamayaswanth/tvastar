"""Tool masking and capability governance.

**Masking** (discovery layer): show the model only the tools it should
consider *this turn*. A ``ToolPolicy`` is a pure function that, given the live
conversation state, returns the subset of tool names to expose before each
``model.generate`` call. Masking is advisory — it shapes what the model *sees*.

**Governance** (invocation layer): enforce phase-based permissions at the
moment a tool is *called*, regardless of what the model was shown. A
``GovernancePolicy`` intercepts ``Session._execute_tools`` and either hard-
blocks a call or routes it through an ``ApprovalGate`` so a human can
elevate the privilege. This layer is tamper-proof against prompt injection
because it runs in Python code, not as a prompt instruction.

Design notes
------------
- A ToolPolicy returns an iterable of tool *names*. The harness intersects that
  with the tools actually available this turn, so a policy can never *grant* a
  tool the agent doesn't have — only hide ones it does.
- Masking must never break a run: if a policy raises, the harness falls back to
  exposing all available tools and carries on.
- GovernancePolicy violations return an error ToolResultBlock (not a raised
  exception) so the agent loop stays alive and the model can self-correct.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Iterable, Optional

if TYPE_CHECKING:  # pragma: no cover
    from .approval import ApprovalGate
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


# ---------------------------------------------------------------------------
# Governance — invocation-layer enforcement
# ---------------------------------------------------------------------------


@dataclass
class GovernancePolicy:
    """Phase-based capability governance enforced at tool invocation time.

    Unlike masking (which hides tools from the model's view), governance runs
    *after* the model has requested a tool call — inside
    ``Session._execute_tools``. A prompt-injected model cannot bypass it by
    reasoning around what it was shown.

    Define named phases with an allow-set of tool names. Use ``"*"`` as a
    sentinel to mean "all tools". When a tool call violates the current phase:

    * If ``approval_gate`` is set, the gate is prompted. If the human approves,
      the call proceeds normally. If denied or timed out, an error result is
      returned to the model.
    * If ``approval_gate`` is ``None``, the call is hard-blocked with an error.

    Example::

        from tvastar import create_agent, GovernancePolicy, ApprovalGate

        gov = GovernancePolicy(
            phases={
                "read":  {"grep", "read_file", "glob"},
                "write": {"grep", "read_file", "glob", "write_file", "bash"},
            },
            current_phase="read",
            approval_gate=ApprovalGate(backend="cli"),
        )
        agent = create_agent("assistant", model=..., governance=gov)

        # Transition phases at runtime:
        agent.governance.set_phase("write")
    """

    phases: dict[str, set[str]]
    current_phase: str = "default"
    approval_gate: Optional["ApprovalGate"] = None

    def __post_init__(self) -> None:
        if not self.phases:
            raise ValueError(
                "GovernancePolicy.phases must not be empty. "
                "An empty phases dict would silently permit every tool call. "
                "Define at least one phase, e.g. phases={'default': {'*'}}."
            )

    def set_phase(self, name: str) -> None:
        """Switch to a named phase. Raises ``ValueError`` for unknown names."""
        if name not in self.phases:
            raise ValueError(
                f"Unknown governance phase {name!r}. Known phases: {sorted(self.phases)}"
            )
        self.current_phase = name

    def is_allowed(self, tool_name: str) -> bool:
        """Return True if ``tool_name`` is permitted in the current phase.

        Fails *closed*: if the current phase has no entry in ``phases``,
        the tool is **denied** rather than allowed.  This prevents a
        misconfigured or uninitialised phase from silently granting access.
        """
        allowed = self.phases.get(self.current_phase)
        if allowed is None:
            return False  # unknown phase → deny (fail closed)
        return "*" in allowed or tool_name in allowed

    def as_tool_policy(self) -> "ToolPolicy":
        """Return a masking :data:`ToolPolicy` that mirrors the current phase.

        The returned callable reads ``self.current_phase`` on every call, so
        ``set_phase()`` is reflected in masking immediately — both enforcement
        layers are driven by one policy object.

        Example::

            gov = GovernancePolicy(phases={"read": {"grep", "read_file"}, "write": {"*"}})
            agent = create_agent("a", model=m, governance=gov, tool_policy=gov.as_tool_policy())
            gov.set_phase("write")  # both layers unlock write tools at once
        """
        gov = self  # capture reference for the closure

        def _policy(ctx: "MaskContext") -> list[str]:
            allowed = gov.phases.get(gov.current_phase)
            if allowed is None or "*" in allowed:
                return list(ctx.available)
            return [n for n in ctx.available if n in allowed]

        _policy.__name__ = "governance_as_tool_policy"
        return _policy

    def copy(self) -> "GovernancePolicy":
        """Return a shallow copy with independent ``current_phase`` state.

        Each :class:`~tvastar.session.Session` created from the same
        :class:`~tvastar.agent.AgentSpec` gets its own copy so that
        ``set_phase()`` calls in one session cannot race with another.
        """
        import dataclasses
        return dataclasses.replace(self)
