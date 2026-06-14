"""Agent definition — the declarative spec for an agent.

``create_agent`` assembles a model, tools, skills, sandbox factory, and system
prompt into an immutable AgentSpec — a plain definition of an agent's behavior
and config. A Harness then runs it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union

from .compaction import CompactionPolicy
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
    thinking_level: Optional[str] = None  # 'low' | 'medium' | 'high' | None
    #: failure detectors run after each run (empty list disables detection)
    detectors: list = field(default_factory=list)
    #: optional auto-compaction policy (None = disabled)
    compaction: Optional[CompactionPolicy] = None
    #: optional harness-wide tool retry policy
    tool_retry: Optional[Any] = None
    #: named subagent profiles available for session.task(agent='name')
    subagents: dict = field(default_factory=dict)  # name -> AgentProfile
    #: optional cost ceiling enforced during the run (None = unlimited)
    budget: Optional[Any] = None  # BudgetPolicy
    #: optional human-in-the-loop approval gate, exposed to tools via ctx
    approval_gate: Optional[Any] = None  # ApprovalGate
    #: optional tool-masking policy applied before each model call (None = expose all)
    tool_policy: Optional[Any] = None  # masking.ToolPolicy
    #: optional invocation-layer governance (phase-based enforcement, separate from masking)
    governance: Optional[Any] = None  # masking.GovernancePolicy
    #: optional session message-size cap in megabytes. When the accumulated
    #: messages exceed this limit the run compacts (if possible) then stops
    #: with stopped="memory_cap". None = unlimited (default).
    memory_cap_mb: Optional[float] = None
    #: optional hook applied to the composed system prompt just before each model call.
    #: Basic signature: ``(system_prompt: str) -> str``.
    #: Extended signature: ``(system_prompt: str, *, last_user_text: str = "") -> str``.
    #: If the hook declares ``last_user_text`` the session passes the most-recent
    #: user message so retrieval can be keyed on actual intent, not static instructions.
    system_prompt_hook: Optional[Callable[..., str]] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def build_system_prompt(self, *, last_user_text: str = "") -> str:
        """Compose the system prompt from instructions + skill catalog, then apply hook.

        Args:
            last_user_text: The most-recent user message text from the live
                session.  Forwarded to hooks that declare a ``last_user_text``
                keyword parameter (e.g. ``LTMStore.as_hook()``) so retrieval
                can be keyed on what the user actually asked rather than the
                static instructions string.
        """
        import inspect
        import warnings

        parts = [self.instructions.strip()] if self.instructions.strip() else []
        catalog = self.skills.catalog()
        if catalog:
            parts.append(catalog)
        prompt = "\n\n".join(parts)
        if self.system_prompt_hook is not None:
            try:
                sig = inspect.signature(self.system_prompt_hook)
                if "last_user_text" in sig.parameters:
                    prompt = self.system_prompt_hook(prompt, last_user_text=last_user_text)
                else:
                    prompt = self.system_prompt_hook(prompt)
            except Exception as exc:
                warnings.warn(
                    f"system_prompt_hook raised {type(exc).__name__}: {exc!r}; "
                    "system prompt returned without hook augmentation.",
                    stacklevel=2,
                )
        return prompt

    def get_subagent(self, name: str) -> Optional[Any]:
        """Return a registered AgentProfile by name, or None."""
        return self.subagents.get(name)


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
    thinking_level: Optional[str] = None,
    detect: Union[bool, list, None] = True,
    subagents: Optional[list] = None,
    compaction: Optional[CompactionPolicy] = None,
    tool_retry: Optional[Any] = None,
    budget: Optional[Any] = None,
    approval_gate: Optional[Any] = None,
    tool_policy: Optional[Any] = None,
    governance: Optional[Any] = None,
    system_prompt_hook: Optional[Callable[..., str]] = None,
    memory_cap_mb: Optional[float] = None,
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
        thinking_level: Reasoning effort for extended thinking models
            ('low' | 'medium' | 'high'). None disables extended thinking.
        detect: Failure detection. ``True`` (default) uses the built-in suite,
            ``False``/``None`` disables it, or pass a custom list of detectors.
        subagents: List of AgentProfile objects available for
            session.task(agent='name') delegation.
        compaction: Auto-compaction policy. None = disabled.
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
        factory = lambda inst=sandbox: inst  # noqa: E731
    else:
        factory = sandbox

    if detect is True:
        from .detect import default_detectors

        detectors = default_detectors()
    elif isinstance(detect, list):
        detectors = detect
    else:
        detectors = []

    subagent_map: dict[str, Any] = {}
    for p in subagents or []:
        subagent_map[p.name] = p

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
        thinking_level=thinking_level,
        detectors=detectors,
        compaction=compaction,
        tool_retry=tool_retry,
        subagents=subagent_map,
        budget=budget,
        approval_gate=approval_gate,
        tool_policy=tool_policy,
        governance=governance,
        system_prompt_hook=system_prompt_hook,
        memory_cap_mb=memory_cap_mb,
        metadata=metadata,
    )
