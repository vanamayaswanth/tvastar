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

from .types import ToolResultBlock

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


def health_policy(
    *,
    degraded_after: int = 2,
    exclusion_after: int = 5,
    cooldown_seconds: float = 60.0,
) -> ToolPolicy:
    """Return a ToolPolicy that excludes tools after repeated failures.

    Call ``policy.report_outcome(tool_name, success)`` after each tool execution
    to update the health state.
    """
    import time as _time

    _failures: dict[str, int] = {}  # tool_name → consecutive failure count
    _excluded_at: dict[str, float] = {}  # tool_name → time when excluded

    def _policy(ctx: MaskContext) -> list[str]:
        now = _time.time()
        # Re-admit tools whose cooldown has elapsed
        readmit = [t for t, ts in _excluded_at.items() if now - ts >= cooldown_seconds]
        for t in readmit:
            del _excluded_at[t]
        # Exclude tools over the threshold
        return [n for n in ctx.available if n not in _excluded_at]

    def report_outcome(tool_name: str, success: bool) -> None:
        if success:
            _failures.pop(tool_name, None)
            _excluded_at.pop(tool_name, None)
        else:
            _failures[tool_name] = _failures.get(tool_name, 0) + 1
            if _failures[tool_name] >= exclusion_after:
                _excluded_at[tool_name] = _time.time()

    _policy.report_outcome = report_outcome  # type: ignore[attr-defined]
    _policy.__name__ = "health_policy"
    return _policy


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

        return dataclasses.replace(self, phases={k: set(v) for k, v in self.phases.items()})

    async def enforce(
        self,
        tool_name: str,
        tool_use_id: str = "",
    ) -> Optional[ToolResultBlock]:
        """Check governance and return an error block on violation, or None if allowed.

        This is the single-call API for custom tool execution pipelines.
        Returns ``None`` when the tool is permitted (either directly or via
        approval gate). Returns a ``ToolResultBlock(is_error=True)`` when
        blocked.

        Algorithm:
            1. If the tool is allowed in the current phase → return None.
            2. If current_phase is not in phases → fail closed (error block).
            3. If an approval_gate is configured → await the gate:
               - Approved → return None.
               - ApprovalDenied / ApprovalTimeout → return error block.
            4. No gate → return error block (hard block).
        """
        from .approval import ApprovalDenied, ApprovalTimeout

        # 1. Tool is permitted in the current phase.
        if self.is_allowed(tool_name):
            return None

        # 2. Unknown phase → fail closed.
        if self.current_phase not in self.phases:
            return ToolResultBlock(
                tool_use_id=tool_use_id,
                content=f"[governance] denied: tool {tool_name!r} blocked — unknown phase {self.current_phase!r}",
                is_error=True,
            )

        # 3. Gate configured → ask for approval.
        if self.approval_gate is not None:
            try:
                await self.approval_gate.request(
                    f"Tool {tool_name!r} is not permitted in phase {self.current_phase!r}. Approve?",
                )
                return None
            except ApprovalDenied as exc:
                return ToolResultBlock(
                    tool_use_id=tool_use_id,
                    content=f"[governance] denied: {exc}",
                    is_error=True,
                )
            except ApprovalTimeout as exc:
                return ToolResultBlock(
                    tool_use_id=tool_use_id,
                    content=f"[governance] denied: {exc}",
                    is_error=True,
                )

        # 4. No gate → hard block.
        return ToolResultBlock(
            tool_use_id=tool_use_id,
            content=f"[governance] denied: tool {tool_name!r} is not permitted in phase {self.current_phase!r}",
            is_error=True,
        )
