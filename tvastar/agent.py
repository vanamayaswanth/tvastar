"""Agent definition — the declarative spec for an agent.

``create_agent`` assembles a model, tools, skills, sandbox factory, and system
prompt into an immutable AgentSpec — a plain definition of an agent's behavior
and config. A Harness then runs it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union

from .model.base import Model
from .sandbox.base import Sandbox
from .sandbox.virtual import VirtualSandbox
from .skills.loader import Skill, SkillLibrary
from .tools.base import Tool, ToolRegistry

SandboxFactory = Callable[[], Sandbox]


@dataclass
class AgentSpec:
    name: str
    model: Model
    instructions: str = ""
    tools: ToolRegistry = field(default_factory=ToolRegistry)
    skills: SkillLibrary = field(default_factory=SkillLibrary)
    sandbox_factory: SandboxFactory = field(default=VirtualSandbox)
    max_steps: int = 20
    max_tokens: int = 4096
    temperature: float = 1.0
    #: failure detectors run after each run (empty list disables detection)
    detectors: list = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def build_system_prompt(self) -> str:
        """Compose the system prompt from instructions + skill catalog."""
        parts = [self.instructions.strip()] if self.instructions.strip() else []
        catalog = self.skills.catalog()
        if catalog:
            parts.append(catalog)
        return "\n\n".join(parts)


def create_agent(
    name: str,
    *,
    model: Model,
    instructions: str = "",
    tools: Optional[list[Union[Tool, "ToolRegistry"]]] = None,
    skills: Optional[Union[list[Skill], SkillLibrary]] = None,
    sandbox: Union[SandboxFactory, Sandbox, None] = None,
    max_steps: int = 20,
    max_tokens: int = 4096,
    temperature: float = 1.0,
    detect: Union[bool, list, None] = True,
    **metadata: Any,
) -> AgentSpec:
    """Create an agent specification.

    Args:
        name: Human label for the agent.
        model: A Model instance (Anthropic/OpenAI/Mock/...).
        instructions: System prompt / persona.
        tools: List of Tool objects (or a ToolRegistry).
        skills: List of Skill objects or a SkillLibrary.
        sandbox: A Sandbox instance, a zero-arg factory, or None (VirtualSandbox).
        max_steps: Max model<->tool turns per prompt before stopping.
        detect: Failure detection. ``True`` (default) uses the built-in suite,
            ``False``/``None`` disables it, or pass a custom list of detectors.
    """
    registry = ToolRegistry()
    for t in tools or []:
        if isinstance(t, ToolRegistry):
            registry.extend([t.get(n) for n in t.names()])
        else:
            registry.add(t)

    if isinstance(skills, SkillLibrary):
        skill_lib = skills
    else:
        skill_lib = SkillLibrary(list(skills or []))

    if sandbox is None:
        factory: SandboxFactory = VirtualSandbox
    elif isinstance(sandbox, Sandbox):
        # Reuse the same instance across sessions (caller manages lifecycle).
        factory = lambda inst=sandbox: inst  # noqa: E731
    else:
        factory = sandbox

    if detect is True:
        from .detect import default_detectors

        detectors = default_detectors()
    elif isinstance(detect, list):
        detectors = detect
    else:  # False / None
        detectors = []

    return AgentSpec(
        name=name,
        model=model,
        instructions=instructions,
        tools=registry,
        skills=skill_lib,
        sandbox_factory=factory,
        max_steps=max_steps,
        max_tokens=max_tokens,
        temperature=temperature,
        detectors=detectors,
        metadata=metadata,
    )
