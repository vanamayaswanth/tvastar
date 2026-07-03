"""AgentProfile — named specialist subagent configurations.

A profile is reusable configuration for a child session. It is not a deployed
agent and has no persistent identity — it defines *how* a task child should
behave when selected by name.

Usage::

    from tvastar.profiles import define_agent_profile

    reviewer = define_agent_profile(
        name="reviewer",
        description="Reviews code for correctness and security.",
        model=AnthropicModel("claude-sonnet-4-6"),
        instructions="Report only issues with a reproducible failure scenario.",
        thinking_level="high",
    )

    agent = create_agent(
        "coordinator",
        model=model,
        subagents=[reviewer],
        ...
    )

    # In a session:
    result = await session.task(
        "Review the auth package.",
        agent="reviewer",
        cwd="packages/auth",
        cancel_after=30.0,
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, Union

if TYPE_CHECKING:  # pragma: no cover
    from .model.base import Model

#: Maximum nesting depth for task delegation chains.
MAX_TASK_DEPTH = 4


@dataclass
class AgentProfile:
    """Reusable specialist configuration for delegated child sessions."""

    name: str
    description: str = ""
    instructions: Optional[str] = None
    model: Optional[Model] = None  # Model instance or None → inherit
    tools: Optional[list] = None  # None → inherit parent tools
    skills: Optional[list] = None  # None → inherit parent skills
    thinking_level: Optional[str] = None  # 'low' | 'medium' | 'high' | None
    max_steps: Optional[int] = None  # None → inherit
    subagents: list["AgentProfile"] = field(default_factory=list)
    detect: Optional[Union[bool, list]] = None  # None=inherit, False=disable, True/list=configure
    metadata: dict[str, Any] = field(default_factory=dict)


def define_agent_profile(
    name: str,
    *,
    description: str = "",
    instructions: Optional[str] = None,
    model: Optional[Model] = None,
    tools: Optional[list] = None,
    skills: Optional[list] = None,
    thinking_level: Optional[str] = None,
    max_steps: Optional[int] = None,
    subagents: Optional[list[AgentProfile]] = None,
    detect: Optional[Union[bool, list]] = None,
    **metadata: Any,
) -> AgentProfile:
    """Create a named specialist profile for use as a subagent.

    Args:
        name: Unique identifier used in session.task(agent='name').
        description: Human-readable description (surfaced in traces).
        instructions: System prompt override. None = inherit parent's.
        model: Model override for child sessions. None = inherit parent's.
        tools: Tool override list. None = inherit parent's.
        skills: Skill override list. None = inherit parent's.
        thinking_level: Reasoning effort ('low'|'medium'|'high'). None = inherit.
        max_steps: Step ceiling override. None = inherit.
        subagents: Nested profiles this profile can delegate to.
        detect: Detector configuration for child sessions.
            None = inherit parent's detector configuration.
            False = disable detection for child.
            True or list = configure detectors accordingly.
        **metadata: Arbitrary metadata stored on the profile.
    """
    return AgentProfile(
        name=name,
        description=description,
        instructions=instructions,
        model=model,
        tools=tools,
        skills=skills,
        thinking_level=thinking_level,
        max_steps=max_steps,
        subagents=list(subagents or []),
        detect=detect,
        metadata=dict(metadata),
    )
