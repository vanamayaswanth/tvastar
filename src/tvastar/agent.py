"""Agent definition — the declarative spec for an agent.

``create_agent`` assembles a model, tools, skills, sandbox factory, and system
prompt into an immutable AgentSpec — a plain definition of an agent's behavior
and config. A Harness then runs it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional, Union

from .compaction import CompactionPolicy
from .model.base import Model
from .sandbox.base import Sandbox
from .sandbox.virtual import VirtualSandbox
from .skills.loader import Skill, SkillLibrary
from .tools.base import Tool, ToolRegistry

if TYPE_CHECKING:  # pragma: no cover
    from .profiles import AgentProfile
    from .types import (
        AgentPruner,
        ApprovalGate,
        AssurancePolicy,
        BudgetPolicy,
        Detector,
        GovernancePolicy,
        ToolPolicy,
        ToolRetryPolicy,
    )

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
    #: Maximum number of tokens the model may generate per response.
    #: Passed to the model's ``generate()`` call as the token budget for each
    #: completion. Defaults to 4096.
    max_tokens: int = 4096
    temperature: float = 1.0
    thinking_level: Optional[str] = None  # 'low' | 'medium' | 'high' | None
    #: failure detectors run after each run (empty list disables detection)
    detectors: list[Detector] = field(default_factory=list)
    #: optional auto-compaction policy (None = disabled)
    compaction: Optional[CompactionPolicy] = None
    #: optional harness-wide tool retry policy
    tool_retry: Optional[ToolRetryPolicy] = None
    #: named subagent profiles available for session.task(agent='name')
    subagents: dict[str, AgentProfile] = field(default_factory=dict)
    #: optional cost ceiling enforced during the run (None = unlimited)
    budget: Optional[BudgetPolicy] = None
    #: optional human-in-the-loop approval gate, exposed to tools via ctx
    approval_gate: Optional[ApprovalGate] = None
    #: optional tool-masking policy applied before each model call (None = expose all)
    tool_policy: Optional[ToolPolicy] = None
    #: optional invocation-layer governance (phase-based enforcement, separate from masking)
    governance: Optional[GovernancePolicy] = None
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
    #: optional verifiable-execution policy — produces a signed ExecutionReceipt
    #: on every run and optionally enforces a Loop Quality SLA.
    assurance: Optional[AssurancePolicy] = None
    #: optional agent pruner — auto-updated after sess.task() completes so slow/failing
    #: agents are demoted before the next routing decision.
    pruner: Optional[AgentPruner] = None
    #: when True, replace every message's content with its SHA-256 hash after each run
    #: so PII in the conversation history cannot be recovered from the session object.
    scrub_after_run: bool = False
    #: number of retries when structured-output parsing fails (0 = no retries)
    structured_retries: int = 2
    #: maximum depth for nested session.task() delegation chains
    max_task_depth: int = 4
    #: maximum number of concurrent tool executions per step (None = unlimited)
    tool_concurrency: Optional[int] = None
    #: hook invoked before each tool execution; receives (tool_name, args_dict)
    #: and may return a modified args dict or None to use originals.
    pre_tool_hook: Optional[Callable[["str", "dict"], Optional["dict"]]] = None
    #: hook invoked after each tool execution; receives (tool_name, args_dict, result_str)
    #: and may return a modified result string or None to use original.
    post_tool_hook: Optional[Callable[["str", "dict", "str"], Optional["str"]]] = None
    #: callback invoked after each model generate call (before tool execution);
    #: receives (step_number, model_response, current_messages).
    step_callback: Optional[Callable[["int", Any, "list"], None]] = None
    #: predicate checked after each model response; if returns True the loop
    #: terminates with stopped="predicate".
    stop_predicate: Optional[Callable[[Any], bool]] = None
    #: ordered list of middleware callables applied to messages before each
    #: model generate call. Each receives the message list and returns a
    #: (possibly modified) message list.
    middleware: Optional[list[Callable[["list"], "list"]]] = None
    #: fallback models tried in order when the primary model raises a
    #: non-overflow exception during generate().
    fallback_models: Optional[list[Model]] = None
    #: function to reorder tool-use requests before execution; receives the
    #: list of tool-use blocks and returns a sorted list.
    tool_order_fn: Optional[Callable[["list"], "list"]] = None
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

    def get_subagent(self, name: str) -> Optional[AgentProfile]:
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
    tool_retry: Optional[ToolRetryPolicy] = None,
    budget: Optional[BudgetPolicy] = None,
    approval_gate: Optional[ApprovalGate] = None,
    tool_policy: Optional[ToolPolicy] = None,
    governance: Optional[GovernancePolicy] = None,
    system_prompt_hook: Optional[Callable[..., str]] = None,
    memory_cap_mb: Optional[float] = None,
    assurance: Optional[AssurancePolicy] = None,
    pruner: Optional[AgentPruner] = None,
    scrub_after_run: bool = False,
    structured_retries: int = 2,
    max_task_depth: int = 4,
    tool_concurrency: Optional[int] = None,
    pre_tool_hook: Optional[Callable[["str", "dict"], Optional["dict"]]] = None,
    post_tool_hook: Optional[Callable[["str", "dict", "str"], Optional["str"]]] = None,
    step_callback: Optional[Callable[["int", Any, "list"], None]] = None,
    stop_predicate: Optional[Callable[[Any], bool]] = None,
    middleware: Optional[list[Callable[["list"], "list"]]] = None,
    fallback_models: Optional[list[Model]] = None,
    tool_order_fn: Optional[Callable[["list"], "list"]] = None,
    compress_tool_output: bool = True,
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
        max_tokens: Maximum number of tokens the model may generate per
            response. This value is passed to the model's ``generate()`` call
            as the token budget for each completion, limiting how long a single
            model reply can be. Defaults to 4096.
        temperature: Sampling temperature for model generation.
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

    subagent_map: dict[str, AgentProfile] = {}
    for p in subagents or []:
        subagent_map[p.name] = p

    # --- Tool output compression wiring ---
    if compress_tool_output:
        from .compressor import ToolOutputCompressor

        compressor = ToolOutputCompressor()
        user_hook = post_tool_hook

        def _compressed_post_tool_hook(tool_name: str, args: dict, result: str) -> Optional[str]:
            current_result = result
            # Run compressor first (fault-tolerant)
            try:
                compressed = compressor(tool_name, args, current_result)
                if compressed is not None:
                    current_result = compressed
            except Exception:
                pass  # fallback to original result
            # Then run user's hook if provided
            if user_hook is not None:
                user_result = user_hook(tool_name, args, current_result)
                if user_result is not None:
                    return user_result
            # Return None if no modification from user hook, but if compressor
            # changed something we need to return that.
            if current_result is not result:
                return current_result
            return None

        post_tool_hook = _compressed_post_tool_hook

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
        assurance=assurance,
        pruner=pruner,
        scrub_after_run=scrub_after_run,
        structured_retries=structured_retries,
        max_task_depth=max_task_depth,
        tool_concurrency=tool_concurrency,
        pre_tool_hook=pre_tool_hook,
        post_tool_hook=post_tool_hook,
        step_callback=step_callback,
        stop_predicate=stop_predicate,
        middleware=middleware,
        fallback_models=fallback_models,
        tool_order_fn=tool_order_fn,
        metadata=metadata,
    )
