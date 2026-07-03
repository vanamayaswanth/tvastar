"""Session — a stateful conversation that runs the agent loop.

The loop:
    1. append the user prompt
    2. call the model with the system prompt + tools
    3. if the model requested tools, execute them (concurrently) and feed
       results back, then go to 2
    4. otherwise return the assistant's final message

Around that loop: skills (inject extra instructions + scope tools),
sub-tasks (delegated child sessions with named profiles, depth limits,
cancellation), durable checkpoints (after every turn), and tracing.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from .approval import ApprovalDenied, ApprovalTimeout
from .compaction import compact_session
from .cost import BudgetExceeded, Cost
from .errors import ToolError, ToolNotFound
from .memory.store import Memory
from .observability import Tracer
from .profiles import MAX_TASK_DEPTH, AgentProfile
from .sandbox.base import Sandbox
from .sandbox.virtual import VirtualSandbox
from .skills.loader import Skill
from .tools.base import ToolContext, ToolRegistry
from .types import (
    ImageBlock,
    Message,
    ModelResponse,
    StopReason,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    Usage,
)

if TYPE_CHECKING:  # pragma: no cover
    from .agent import AgentSpec
    from .detect import Finding
    from .harness import Harness
    from .quality import LoopQualityReport

# How many times to retry structured-output parsing before giving up.
_STRUCTURED_RETRIES = 2

# Substrings (lowercased) that indicate a context-overflow error from the provider.
_OVERFLOW_PHRASES = (
    "context_length_exceeded",
    "prompt is too long",
    "context window exceeded",
    "maximum context length",
    "input is too long",
    "request too large",
    "token count exceeds",
)


def register_overflow_phrase(phrase: str) -> None:
    """Add a phrase to the overflow detection set.

    The phrase is stored lowercased so that overflow detection is
    case-insensitive.  Duplicate phrases (after lowering) are ignored.

    Example::

        register_overflow_phrase("my_custom_overflow_error")
        # Now _is_context_overflow will match exceptions containing that phrase.
    """
    global _OVERFLOW_PHRASES
    if phrase.lower() not in _OVERFLOW_PHRASES:
        _OVERFLOW_PHRASES = (*_OVERFLOW_PHRASES, phrase.lower())


class _ProfiledModelWrapper:
    """Lightweight per-child model wrapper that holds its own _profile state.

    This avoids mutating a shared Model instance's _profile field when
    concurrent task() calls use profile-routed models (e.g. MockModel with
    keyed scripts). The wrapper delegates generate()/stream() to the underlying
    model but sets _profile on itself. Before delegating, it temporarily sets
    _profile on the inner model for the duration of the synchronous setup
    portion of the call, ensuring no interleaving can corrupt the state.

    Because asyncio is cooperative (single-threaded), the only dangerous
    interleaving points are at awaits. This wrapper brackets the inner call
    with set/restore to keep the window minimal and correct.
    """

    def __init__(self, model: Any, profile: str) -> None:
        # Store the underlying model and the per-child profile
        object.__setattr__(self, "_inner", model)
        object.__setattr__(self, "_profile", profile)

    @property
    def name(self) -> str:  # type: ignore[override]
        return self._inner.name  # type: ignore[attr-defined]

    @property
    def system(self) -> str:  # type: ignore[override]
        return self._inner.system  # type: ignore[attr-defined]

    async def generate(self, *args: Any, **kwargs: Any) -> Any:
        """Delegate to the inner model with per-child profile isolation.

        Sets _profile on the inner model only for the synchronous preamble
        of generate() (where it reads _profile to select a script). After
        the call completes, restores the previous value.
        """
        inner = object.__getattribute__(self, "_inner")
        profile = object.__getattribute__(self, "_profile")
        prev = getattr(inner, "_profile", None)
        inner._profile = profile
        try:
            return await inner.generate(*args, **kwargs)
        finally:
            inner._profile = prev

    async def stream(self, *args: Any, **kwargs: Any) -> Any:
        """Delegate stream() with the same profile isolation."""
        inner = object.__getattribute__(self, "_inner")
        profile = object.__getattribute__(self, "_profile")
        prev = getattr(inner, "_profile", None)
        inner._profile = profile
        try:
            return await inner.stream(*args, **kwargs)
        finally:
            inner._profile = prev

    def __getattr__(self, name: str) -> Any:
        """Forward any other attribute access to the inner model."""
        inner = object.__getattribute__(self, "_inner")
        return getattr(inner, name)


class StructuredOutputError(RuntimeError):
    """Raised when strict structured output parsing fails after all retries."""

    def __init__(self, schema: Any, raw_text: str, parse_error: str) -> None:
        self.schema = schema
        self.raw_text = raw_text
        self.parse_error = parse_error
        super().__init__(
            f"Structured output parsing failed for {type(schema).__name__}: {parse_error}"
        )


@dataclass
class RunResult:
    """Outcome of a ``prompt`` call."""

    text: str
    messages: list[Message]
    usage: Usage
    steps: int
    stopped: str = "end_turn"  # end_turn | max_steps | budget | error
    findings: list["Finding"] = field(default_factory=list)
    data: Optional[Any] = None  # populated when result= schema is used
    cost: Optional[Cost] = None  # token cost for this run (model-priced)
    receipt: Optional[Any] = None  # ExecutionReceipt when assurance is configured

    def __str__(self) -> str:
        return self.text

    @property
    def warnings(self) -> list["Finding"]:
        from .detect import Severity

        return [f for f in self.findings if f.severity != Severity.INFO]

    @property
    def ok(self) -> bool:
        return self.stopped == "end_turn" and not self.warnings

    @property
    def quality(self) -> "LoopQualityReport":
        from .quality import score_run

        return score_run(self)


@dataclass
class Session:
    spec: "AgentSpec"
    harness: "Harness"
    id: str = field(default_factory=lambda: f"sess_{uuid.uuid4().hex[:12]}")
    messages: list[Message] = field(default_factory=list)
    sandbox: Optional[Sandbox] = None
    _started: bool = False
    _active_skill: Optional[Skill] = None
    _task_depth: int = 0  # how deep in the task delegation tree we are
    _cancel_event: Optional[asyncio.Event] = None
    _last_compact_at: float = 0.0  # monotonic time of last compaction attempt
    _last_user_text: str = ""  # most-recent user message text (for LTM hook)
    _approval_records: list = field(
        default_factory=list
    )  # [{tool, approved_by, approved_at, message}]
    last_checkpoint_error: Optional[Exception] = None

    # ---- lifecycle ----------------------------------------------------------

    @property
    def tracer(self) -> Tracer:
        return self.harness.tracer

    @property
    def memory(self) -> Memory:
        return Memory(self.harness.store, scope=self.id)

    async def start(self) -> "Session":
        if self._started:
            return self
        self.sandbox = self.spec.sandbox_factory()
        await self.sandbox.start()
        self._started = True
        return self

    async def close(self) -> None:
        if self.sandbox is not None:
            await self.sandbox.stop()
        self._started = False

    async def __aenter__(self) -> "Session":
        return await self.start()

    async def __aexit__(self, *exc) -> None:
        await self.close()

    # ---- tool context / scoping ---------------------------------------------

    def _tool_context(self) -> ToolContext:
        return ToolContext(
            sandbox=self.sandbox,
            filesystem=self.sandbox.fs if self.sandbox else None,
            memory=self.memory,
            session=self,
            approval_gate=getattr(self.spec, "approval_gate", None),
        )

    def _active_tools(self) -> ToolRegistry:
        if self._active_skill and self._active_skill.tools is not None:
            allowed = set(self._active_skill.tools)
            reg = ToolRegistry()
            for name in self.spec.tools.names():
                if name in allowed:
                    reg.add(self.spec.tools.get(name))
            return reg
        return self.spec.tools

    def _visible_tool_specs(self, step: int) -> list[ToolSpec]:
        """Tools exposed to the model this turn: skill-scoped, then mask-policy.

        Masking is dynamic (recomputed each step) and can only *hide* available
        tools, never grant new ones. A misbehaving policy falls back to exposing
        everything — masking must never break a run.
        """
        from .masking import MaskContext, apply_policy

        reg = self._active_tools()
        specs = reg.specs
        policy = getattr(self.spec, "tool_policy", None)
        if policy is None:
            return specs
        ctx = MaskContext(
            step=step,
            available=reg.names(),
            messages=self.messages,
            active_skill=self._active_skill.name if self._active_skill else None,
        )
        allowed = apply_policy(policy, ctx)
        if allowed is None:
            return specs
        masked = [s for s in specs if s.name in allowed]
        if len(masked) != len(specs):
            self.tracer_event("tools_masked", step=step, visible=len(masked), total=len(specs))
        return masked

    def _system_prompt(self) -> str:
        base = self.spec.build_system_prompt(last_user_text=self._last_user_text)
        if self._active_skill:
            base = (
                f"{base}\n\n## Active skill: {self._active_skill.name}\n"
                f"{self._active_skill.instructions}"
            )
        return base

    # ---- public API ---------------------------------------------------------

    async def prompt(
        self,
        text: str,
        *,
        images: Optional[list[ImageBlock]] = None,
        result: Optional[Any] = None,
        cancel_after: Optional[float] = None,
        strict: bool = False,
        reflect: bool = False,
    ) -> RunResult:
        """Send a user message and run the loop to completion.

        Args:
            text: The user prompt.
            images: Optional images to attach to this turn (vision models only).
            result: Optional schema for structured output. Parse failures are
                    retried up to ``spec.structured_retries`` times before falling
                    back to raw text.
            cancel_after: Optional timeout in seconds.
            strict: If True, raise StructuredOutputError on final parse failure
                    instead of falling back to raw text.
            reflect: If True, run one round of self-reflection on the response
                     to improve quality. Reflection failures are swallowed.
        """
        if not self._started:
            await self.start()
        prompt_text = text
        if result is not None:
            prompt_text = _inject_schema_instruction(text, result)
        # auto-tokenize via assurance.vault so model never sees raw PII
        _vault = None
        _policy = getattr(self.spec, "assurance", None)
        if _policy is not None and getattr(_policy, "vault", None) is not None:
            _vault = _policy.vault
            prompt_text = _vault.tokenize(prompt_text, getattr(_policy, "sanitize", None))
        if images:
            self.messages.append(Message("user", [TextBlock(text=prompt_text), *images]))
        else:
            self.messages.append(Message("user", prompt_text))
        # Track the raw user text so _system_prompt() can forward it to hooks
        # that want the current intent (e.g. LTMStore.as_hook()).
        self._last_user_text = text
        self._approval_records = []  # reset per-run approval audit trail
        with self.tracer.span("session.prompt", session=self.id, agent=self.spec.name):
            if cancel_after is not None:
                _run_result = await asyncio.wait_for(
                    self._run_with_schema(result, strict=strict), timeout=cancel_after
                )
            else:
                _run_result = await self._run_with_schema(result, strict=strict)
        if _vault is not None:
            _run_result.text = _vault.rehydrate(_run_result.text)
        # Auto-reflection if enabled
        if reflect and _run_result.text:
            try:
                from .reflection import reflect as _reflect
                _reflection = await _reflect(
                    _run_result.text,
                    model=self.spec.model,
                    max_rounds=1,
                )
                if _reflection.improved:
                    _run_result.text = _reflection.final_text
            except Exception:
                pass  # reflection failure must never break a run
        return _run_result

    async def skill(
        self,
        name: str,
        text: str,
        *,
        images: Optional[list[ImageBlock]] = None,
        result: Optional[Any] = None,
    ) -> RunResult:
        """Invoke a named skill for one prompt, then deactivate it."""
        skill_obj = self.spec.skills.get(name)
        self.tracer_event("skill_loaded", name=name)
        prev = self._active_skill
        self._active_skill = skill_obj
        try:
            with self.tracer.span("session.skill", skill=name, session=self.id):
                if not self._started:
                    await self.start()
                prompt_text = text
                if result is not None:
                    prompt_text = _inject_schema_instruction(text, result)
                if images:
                    self.messages.append(Message("user", [TextBlock(text=prompt_text), *images]))
                else:
                    self.messages.append(Message("user", prompt_text))
                return await self._run_with_schema(result)
        finally:
            self._active_skill = prev

    async def task(
        self,
        prompt: str,
        *,
        agent: Optional[str] = None,
        instructions: Optional[str] = None,
        result: Optional[Any] = None,
        cwd: Optional[str] = None,
        cancel_after: Optional[float] = None,
        model: Optional[Any] = None,
        thinking_level: Optional[str] = None,
        max_steps: Optional[int] = None,
        router: Optional[Any] = None,
        strict: bool = False,
    ) -> RunResult:
        """Delegate to a fresh child session and return its result.

        Args:
            prompt: The prompt to send the child session.
            agent: Name of a registered AgentProfile to use as specialist.
                   None → anonymous task inheriting parent's config.
            instructions: Override instructions for the child (anonymous tasks).
                          Ignored when ``agent`` is specified (profile wins).
            result: Optional schema for structured output from the child.
            cwd: Working directory hint (attached to child's system prompt).
            cancel_after: Timeout in seconds. Raises asyncio.TimeoutError if exceeded.
            model: Override model for this task (highest precedence).
            thinking_level: Override reasoning level for this task.
            max_steps: Override max steps for this task.
            router: Optional AgentRouter. When agent is None, the router picks
                    the best matching profile automatically via semantic similarity.
        """
        # ── auto-route when no agent specified ───────────────────────────────
        if agent is None and router is not None:
            agent = router.route(prompt)

        # ── depth guard ─────────────────────────────────────────────────────
        _depth_limit = self.spec.max_task_depth if hasattr(self.spec, "max_task_depth") else MAX_TASK_DEPTH
        if self._task_depth >= _depth_limit:
            raise RuntimeError(
                f"Task depth limit ({_depth_limit}) reached. "
                "Avoid chains of agents handing off ambiguous objectives."
            )

        profile: Optional[AgentProfile] = None
        if agent is not None:
            profile = self.spec.get_subagent(agent)
            if profile is None:
                available = list(self.spec.subagents.keys())
                raise ValueError(
                    f"No subagent profile named '{agent}'. "
                    f"Available: {available}. "
                    "Register profiles via create_agent(subagents=[...])."
                )

        child_spec = self._build_child_spec(
            profile=profile,
            instructions_override=instructions,
            model_override=model,
            thinking_level_override=thinking_level,
            max_steps_override=max_steps,
        )

        self.tracer_event(
            "task_spawned",
            profile=agent or "anonymous",
            depth=self._task_depth + 1,
        )

        child = self.harness.session(spec=child_spec)
        child._task_depth = self._task_depth + 1
        # Share parent's sandbox with child so files are visible across task
        # boundaries and transaction rollback covers both (Bug #6)
        if self.sandbox is not None:
            child.sandbox = self.sandbox

        # ── inject cwd into child system prompt ─────────────────────────────
        full_prompt = prompt
        if cwd:
            full_prompt = f"[Working directory: {cwd}]\n\n{prompt}"

        async def _run_child() -> RunResult:
            async with child:
                return await child.prompt(full_prompt, result=result, strict=strict)

        # ── profile-keyed mock routing ──────────────────────────────────────
        # If the effective model supports profile routing (duck-type check),
        # isolate the profile state per-child via a lightweight wrapper so
        # concurrent fan_out calls sharing the same model do not race (Bug #8).
        _effective_model = child_spec.model
        _profile_name = agent or "anonymous"
        _has_profile = hasattr(_effective_model, "_profile")
        if _has_profile:
            # Create a per-child wrapper that holds its own _profile without
            # mutating the shared model instance.
            child_spec.model = _ProfiledModelWrapper(_effective_model, _profile_name)
        # Also store profile routing context in child spec metadata for
        # downstream consumers that may need it without accessing the model.
        child_spec.metadata["_routing_profile"] = _profile_name

        with self.tracer.span(
            "session.task",
            parent=self.id,
            depth=child._task_depth,
            profile=agent or "anonymous",
        ):
            if cancel_after is not None:
                try:
                    _task_result = await asyncio.wait_for(_run_child(), timeout=cancel_after)
                except asyncio.TimeoutError:
                    raise asyncio.TimeoutError(
                        f"Task cancelled after {cancel_after}s "
                        f"(agent={agent or 'anonymous'}, prompt={prompt[:60]!r})"
                    )
            else:
                _task_result = await _run_child()
        # auto-update pruner so slow/failing agents are demoted before the next routing decision
        _pruner = getattr(self.spec, "pruner", None)
        if _pruner is not None and agent is not None:
            _pruner.update(agent, _task_result)
        return _task_result

    def _build_child_spec(
        self,
        *,
        profile: Optional[AgentProfile],
        instructions_override: Optional[str],
        model_override: Optional[Any],
        thinking_level_override: Optional[str],
        max_steps_override: Optional[int],
    ) -> "AgentSpec":
        """Assemble a child AgentSpec merging parent + profile + task overrides."""
        from .agent import create_agent
        from .skills.loader import SkillLibrary

        parent = self.spec

        # Resolve each dimension: task override > profile > parent
        eff_model = model_override or (profile.model if profile else None) or parent.model
        eff_instructions = (
            instructions_override
            or (profile.instructions if profile else None)
            or parent.instructions
        )
        eff_thinking = (
            thinking_level_override
            or (profile.thinking_level if profile else None)
            or parent.thinking_level
        )
        eff_max_steps = (
            max_steps_override or (profile.max_steps if profile else None) or parent.max_steps
        )

        # Tools: profile.tools overrides parent; None = inherit
        if profile and profile.tools is not None:
            tools = profile.tools
        else:
            tools = [parent.tools.get(n) for n in parent.tools.names()]

        # Skills: profile.skills overrides parent; None = inherit
        if profile and profile.skills is not None:
            skill_lib = SkillLibrary(profile.skills)
        else:
            skill_lib = parent.skills

        # Subagents: profile's own nested subagents (not parent's)
        nested_subagents = list(profile.subagents) if profile else []

        child_name = f"{parent.name}:task:{profile.name}" if profile else f"{parent.name}:task"

        # Resolve detection: profile.detect overrides; None = inherit parent
        if profile and profile.detect is not None:
            # Explicit value on profile: False disables, True/list configures
            eff_detect: Any = profile.detect
        else:
            # None (or no profile) → inherit parent's detector configuration
            eff_detect = parent.detectors if parent.detectors else False

        return create_agent(
            child_name,
            model=eff_model,
            instructions=eff_instructions,
            tools=tools,
            skills=skill_lib,
            sandbox=parent.sandbox_factory,
            max_steps=eff_max_steps,
            max_tokens=parent.max_tokens,
            temperature=parent.temperature,
            thinking_level=eff_thinking,
            detect=eff_detect,
            subagents=nested_subagents,
        )

    def tracer_event(self, kind: str, **data: Any) -> None:
        safe = {f"attr_{k}" if k == "name" else k: v for k, v in data.items()}
        with self.tracer.span(f"event.{kind}", **safe):
            pass

    # ---- structured-output retry wrapper ------------------------------------

    async def _run_with_schema(self, schema: Optional[Any], *, strict: bool = False) -> RunResult:
        """Run the agent loop; if structured output fails to parse, correct and retry."""
        run_result = await self._run_loop()
        if schema is None:
            return run_result

        retries = self.spec.structured_retries

        parsed, ok = _try_parse(run_result.text, schema)
        parse_error_msg = ""
        for _ in range(retries):
            if ok:
                break
            self.tracer_event("structured_output_retry")
            parse_error_msg = str(parsed) if not ok else ""
            self.messages.append(Message("user", _correction_message(parsed, schema)))
            run_result = await self._run_loop()
            parsed, ok = _try_parse(run_result.text, schema)

        if ok:
            run_result.data = parsed
        else:
            # Capture the final parse error message
            parse_error_msg = str(parsed)

            if strict:
                raise StructuredOutputError(schema, run_result.text, parse_error_msg)

            # Fall back to raw text and surface a warning so run.ok → False.
            run_result.data = run_result.text
            from .detect import Finding, Severity

            run_result.findings = list(run_result.findings) + [
                Finding(
                    "structured_parse_failure",
                    Severity.WARNING,
                    f"structured output parsing failed after {retries + 1} attempt(s);"
                    " falling back to raw text",
                    {"schema": type(schema).__name__},
                )
            ]
        return run_result

    # ---- the loop -----------------------------------------------------------

    async def _run_loop(self) -> RunResult:
        total = Usage()
        steps = 0
        stopped = "end_turn"
        spec = self.spec
        _budget_approved = False
        _run_started_at = time.time()
        _budget_warnings: list["Finding"] = []

        while steps < spec.max_steps:
            steps += 1
            tools: list[ToolSpec] = self._visible_tool_specs(steps)
            # Apply middleware before generate
            effective_messages = self._apply_middleware(self.messages)
            with self.tracer.span(
                "model.generate",
                **_genai_request_attrs(spec, step=steps, n_tools=len(tools)),
            ) as sp:
                try:
                    resp: ModelResponse = await spec.model.generate(
                        effective_messages,
                        system=self._system_prompt(),
                        tools=tools or None,
                        max_tokens=spec.max_tokens,
                        temperature=spec.temperature,
                        thinking_level=spec.thinking_level,
                    )
                except Exception as exc:
                    # Reactive overflow recovery via shared helper.
                    if _is_context_overflow(exc):
                        recovered = await self._maybe_compact_reactive(exc, step=steps)
                        if recovered:
                            # Re-apply middleware after compaction (messages changed)
                            effective_messages = self._apply_middleware(self.messages)
                            resp = await spec.model.generate(
                                effective_messages,
                                system=self._system_prompt(),
                                tools=tools or None,
                                max_tokens=spec.max_tokens,
                                temperature=spec.temperature,
                                thinking_level=spec.thinking_level,
                            )
                        else:
                            raise exc
                    elif spec.fallback_models:
                        # Non-overflow exception with fallback models configured:
                        # try each fallback in order; first success wins.
                        resp = None  # type: ignore[assignment]
                        for _fb_model in spec.fallback_models:
                            try:
                                resp = await _fb_model.generate(
                                    effective_messages,
                                    system=self._system_prompt(),
                                    tools=tools or None,
                                    max_tokens=spec.max_tokens,
                                    temperature=spec.temperature,
                                    thinking_level=spec.thinking_level,
                                )
                                break
                            except Exception:
                                continue
                        if resp is None:
                            # All fallbacks failed — raise the primary exception
                            raise exc
                    else:
                        raise
            _set_genai_response_attrs(sp, resp)
            total = total + resp.usage
            self.messages.append(resp.message)

            # attribute per-step cost to the active budget phase (if any)
            budget = getattr(spec, "budget", None)
            if budget is not None:
                step_cost = Cost(resp.usage.input_tokens, resp.usage.output_tokens, spec.model.name)
                budget.attribute(step_cost)

            # enforce the cost budget via shared helper
            stop_reason, _budget_approved, _budget_warnings = await self._enforce_budget(
                total,
                _budget_approved=_budget_approved,
                _budget_warnings=_budget_warnings,
            )
            if stop_reason is not None:
                stopped = stop_reason
                break

            # Memory cap via shared helper
            mem_stop = await self._enforce_memory_cap(resp)
            if mem_stop is not None:
                stopped = mem_stop
                break

            # step_callback: invoke after model generate, before tool execution
            if spec.step_callback is not None:
                try:
                    spec.step_callback(steps, resp, self.messages)
                except Exception:
                    import warnings as _warnings

                    _warnings.warn("step_callback raised; continuing loop")

            # stop_predicate: check if user-defined predicate wants to halt the loop
            if spec.stop_predicate is not None:
                try:
                    _result_in_progress = RunResult(
                        text=self._last_assistant_text(),
                        messages=self.messages,
                        usage=total,
                        steps=steps,
                        stopped="in_progress",
                        cost=Cost(total.input_tokens, total.output_tokens, spec.model.name),
                    )
                    if spec.stop_predicate(_result_in_progress):
                        stopped = "predicate"
                        break
                except Exception:
                    import warnings as _warnings

                    _warnings.warn("stop_predicate raised; continuing loop")

            if resp.stop_reason != StopReason.TOOL_USE and not resp.tool_uses:
                stopped = "end_turn"
                break

            results = await self._execute_tools(resp.tool_uses)
            self.messages.append(Message("user", results))
            # auto-compact if policy threshold is reached
            await self._maybe_compact()
            self._checkpoint()
        else:
            stopped = "max_steps"

        self._checkpoint()
        final_text = self._last_assistant_text()
        result = RunResult(
            text=final_text,
            messages=self.messages,
            usage=total,
            steps=steps,
            stopped=stopped,
            cost=Cost(total.input_tokens, total.output_tokens, spec.model.name),
        )
        result.findings = self._detect(result)
        if _budget_warnings:
            result.findings = list(result.findings) + _budget_warnings
        result.receipt = self._assure(result, started_at=_run_started_at)
        if getattr(spec, "scrub_after_run", False):
            import hashlib as _hl

            for _msg in self.messages:
                _h = _hl.sha256(str(_msg.content).encode()).hexdigest()
                _msg.content = f"[scrubbed:sha256:{_h[:16]}]"
        return result

    def _detect(self, result: RunResult) -> list["Finding"]:
        detectors = self.spec.detectors
        if not detectors:
            return []
        from .detect import RunContext, run_detectors

        ctx = RunContext(
            messages=result.messages,
            tools=self.spec.tools,
            stopped=result.stopped,
            final_text=result.text,
        )
        findings = run_detectors(ctx, detectors)
        for f in findings:
            self.tracer_event("finding", detector=f.detector, severity=f.severity.value)
        return findings

    def _assure(self, result: RunResult, *, started_at: float) -> Optional[Any]:
        """Build a signed ExecutionReceipt and enforce SLA if assurance is configured."""
        policy = getattr(self.spec, "assurance", None)
        if policy is None:
            return None
        from .assurance.receipt import ExecutionReceipt

        prev_hash = policy.log.tail_hash if policy.log is not None else ""
        model_name = getattr(self.spec.model, "name", "")
        with self.tracer.span("assurance.receipt"):
            receipt = ExecutionReceipt.from_run_result(
                result,
                agent=self.spec.name,
                model_name=model_name,
                prompt=self._last_user_text,
                started_at=started_at,
                completed_at=time.time(),
                key=policy.key,
                prev_hash=prev_hash,
                sanitize=getattr(policy, "sanitize", None),
                approvals=list(self._approval_records),
            )
        if policy.log is not None:
            policy.log.append(receipt)
        with self.tracer.span("assurance.sla_check"):
            policy.enforce_sla(receipt)
        return receipt

    async def _execute_tools(self, uses: list[ToolUseBlock]) -> list[ToolResultBlock]:
        registry = self._active_tools()
        ctx = self._tool_context()

        default_retry = getattr(self.spec, "tool_retry", None)
        gov = getattr(self.spec, "governance", None)

        # Concurrency limiting: use a semaphore when tool_concurrency is set
        sem = (
            asyncio.Semaphore(self.spec.tool_concurrency)
            if self.spec.tool_concurrency
            else None
        )

        async def run_one(use: ToolUseBlock) -> ToolResultBlock:
            # Governance: invocation-layer enforcement (runs after masking/discovery).
            # Tamper-proof against prompt injection — checked in Python, not prompt.
            if gov is not None and not gov.is_allowed(use.name):
                self.tracer_event("governance_blocked", tool=use.name, phase=gov.current_phase)
                if gov.approval_gate is not None:
                    try:
                        _approval_msg = (
                            f"Tool '{use.name}' is restricted in phase '{gov.current_phase}'.\n"
                            f"Input: {use.input!r}\n"
                            "Policy 'least_privilege' requires elevation to proceed."
                        )
                        await gov.approval_gate.request(_approval_msg)
                        self._approval_records.append(
                            {
                                "tool": use.name,
                                "approved_by": getattr(gov.approval_gate, "_last_approver", ""),
                                "approved_at": time.time(),
                                "message": _approval_msg[:200],
                            }
                        )
                        # Human approved — fall through to normal execution below.
                    except (ApprovalDenied, ApprovalTimeout) as e:
                        return ToolResultBlock(use.id, f"[governance] {e}", is_error=True)
                else:
                    return ToolResultBlock(
                        use.id,
                        f"[governance] Tool '{use.name}' is not permitted"
                        f" in phase '{gov.current_phase}'",
                        is_error=True,
                    )

            # Pre-tool hook: allow observation/modification of args before execution
            args = dict(use.input) if use.input else {}
            if self.spec.pre_tool_hook:
                try:
                    modified = self.spec.pre_tool_hook(use.name, args)
                    if modified is not None:
                        args = modified
                except Exception:
                    import warnings
                    warnings.warn("pre_tool_hook raised; using original args")

            with self.tracer.span("tool.invoke", tool=use.name) as sp:
                try:
                    tool = registry.get(use.name)
                    out = await tool.invoke(args, ctx, default_retry=default_retry)
                    result = ToolResultBlock(tool_use_id=use.id, content=out)

                    # Post-tool hook: allow observation/modification of result
                    if self.spec.post_tool_hook:
                        try:
                            modified = self.spec.post_tool_hook(use.name, args, result.content)
                            if modified is not None:
                                result = ToolResultBlock(
                                    tool_use_id=use.id,
                                    content=modified,
                                    is_error=result.is_error,
                                )
                        except Exception:
                            import warnings
                            warnings.warn("post_tool_hook raised; using original result")

                    return result
                except ToolNotFound as e:
                    sp.status = "error"
                    return ToolResultBlock(use.id, f"[error] {e}", is_error=True)
                except ToolError as e:
                    sp.status = "error"
                    return ToolResultBlock(use.id, f"[error] {e}", is_error=True)

        async def run_one_limited(use: ToolUseBlock) -> ToolResultBlock:
            async with sem:  # type: ignore[union-attr]
                return await run_one(use)

        # Apply tool ordering function if configured (Req 18.2, 18.3)
        ordered = uses
        if self.spec.tool_order_fn:
            try:
                ordered = self.spec.tool_order_fn(uses)
            except Exception:
                import warnings

                warnings.warn("tool_order_fn raised; using original order")
                ordered = uses

        if sem:
            return list(await asyncio.gather(*(run_one_limited(u) for u in ordered)))
        return list(await asyncio.gather(*(run_one(u) for u in ordered)))

    # ---- streaming variant --------------------------------------------------

    async def stream(self, text: str) -> AsyncIterator[StreamEvent]:
        if not self._started:
            await self.start()
        self.messages.append(Message("user", text))
        spec = self.spec
        steps = 0
        total = Usage()
        _budget_approved = False
        _budget_warnings: list["Finding"] = []
        stopped = "end_turn"

        while steps < spec.max_steps:
            steps += 1
            tools = self._visible_tool_specs(steps)
            yield StreamEvent("turn_start", {"step": steps})
            # Apply middleware before generate
            effective_messages = self._apply_middleware(self.messages)
            resp: Optional[ModelResponse] = None
            try:
                async for ev in spec.model.stream(
                    effective_messages,
                    system=self._system_prompt(),
                    tools=tools or None,
                    max_tokens=spec.max_tokens,
                    temperature=spec.temperature,
                    thinking_level=spec.thinking_level,
                ):
                    if ev.type == "turn_end":
                        resp = ev.data["response"]
                    else:
                        yield ev
            except Exception as exc:
                # Reactive overflow recovery via shared helper.
                if _is_context_overflow(exc):
                    recovered = await self._maybe_compact_reactive(exc, step=steps)
                    if recovered:
                        effective_messages = self._apply_middleware(self.messages)
                        async for ev in spec.model.stream(
                            effective_messages,
                            system=self._system_prompt(),
                            tools=tools or None,
                            max_tokens=spec.max_tokens,
                            temperature=spec.temperature,
                            thinking_level=spec.thinking_level,
                        ):
                            if ev.type == "turn_end":
                                resp = ev.data["response"]
                            else:
                                yield ev
                    else:
                        raise
                else:
                    raise
            assert resp is not None
            total = total + resp.usage
            self.messages.append(resp.message)

            # Budget enforcement via shared helper
            stop_reason, _budget_approved, _budget_warnings = await self._enforce_budget(
                total,
                _budget_approved=_budget_approved,
                _budget_warnings=_budget_warnings,
            )
            if stop_reason is not None:
                stopped = stop_reason
                yield StreamEvent("turn_end", {"text": resp.message.text or "", "stopped": stopped})
                break

            # Memory cap enforcement via shared helper
            mem_stop = await self._enforce_memory_cap(resp)
            if mem_stop is not None:
                stopped = mem_stop
                yield StreamEvent("turn_end", {"text": resp.message.text or "", "stopped": stopped})
                break

            if resp.stop_reason != StopReason.TOOL_USE and not resp.tool_uses:
                stopped = "end_turn"
                yield StreamEvent("turn_end", {"text": resp.message.text})
                break
            # Tool calls go through _execute_tools which already enforces
            # governance policy (tool masking/blocking) via the same path as prompt().
            for use in resp.tool_uses:
                yield StreamEvent("tool_call", {"name": use.name, "input": use.input})
            results = await self._execute_tools(resp.tool_uses)
            for r in results:
                yield StreamEvent("tool_result", {"content": r.content, "error": r.is_error})
            self.messages.append(Message("user", results))
            # auto-compact if policy threshold is reached
            await self._maybe_compact()
            self._checkpoint()
        else:
            stopped = "max_steps"

        # Produce findings from configured detectors (same as prompt path)
        self._checkpoint()
        final_text = self._last_assistant_text()
        _stream_result = RunResult(
            text=final_text,
            messages=self.messages,
            usage=total,
            steps=steps,
            stopped=stopped,
            cost=Cost(total.input_tokens, total.output_tokens, spec.model.name),
        )
        _stream_result.findings = self._detect(_stream_result)
        if _budget_warnings:
            _stream_result.findings = list(_stream_result.findings) + _budget_warnings

    # ---- helpers ------------------------------------------------------------

    def _apply_middleware(self, messages: "list[Message]") -> "list[Message]":
        """Apply configured middleware pipeline to messages before generate.

        Each middleware receives the message list and returns a (possibly
        modified) list.  On exception: log warning, skip that middleware,
        continue with the last good state.  Middleware must never break a run.
        """
        if not self.spec.middleware:
            return messages
        result = messages
        for mw in self.spec.middleware:
            try:
                result = mw(result)
            except Exception:
                import warnings

                warnings.warn(f"Middleware {mw} raised; skipping")
        return result

    # ---- shared policy enforcement helpers (used by _run_loop and stream) ----

    async def _enforce_budget(
        self,
        total: Usage,
        *,
        _budget_approved: bool = False,
        _budget_warnings: "list[Finding] | None" = None,
    ) -> "tuple[Optional[str], bool, list[Finding]]":
        """Check budget policy and return (stop_reason_or_None, budget_approved, warnings).

        Called after each model generate to enforce cost limits.
        Returns:
            stop_reason: "budget" if the loop should stop, None otherwise.
            budget_approved: updated flag (True once the user approves overspend).
            warnings: list of budget-warning findings accumulated so far.
        """
        from .detect import Finding, Severity

        spec = self.spec
        budget = getattr(spec, "budget", None)
        if _budget_warnings is None:
            _budget_warnings = []

        if budget is None:
            return None, _budget_approved, _budget_warnings

        running = Cost(total.input_tokens, total.output_tokens, spec.model.name)

        if not _budget_approved:
            if budget.should_warn(running):
                warn_finding = Finding(
                    "budget_warning",
                    Severity.WARNING,
                    f"Spend ${running.usd:.4f} approaching limit ${budget.max_usd:.4f}",
                    {},
                )
                if not any(
                    getattr(f, "detector", None) == "budget_warning" for f in _budget_warnings
                ):
                    _budget_warnings.append(warn_finding)

            if running.usd >= budget.max_usd:
                if budget.on_exceed == "raise":
                    raise BudgetExceeded(spent=running.usd, limit=budget.max_usd)
                elif budget.on_exceed == "stop":
                    return "budget", _budget_approved, _budget_warnings
                elif budget.on_exceed == "approve":
                    gate = getattr(spec, "approval_gate", None)
                    if gate is None:
                        raise BudgetExceeded(spent=running.usd, limit=budget.max_usd)
                    try:
                        await gate.request(
                            f"Budget limit ${budget.max_usd:.2f} reached"
                            f" (spent ${running.usd:.4f}). Continue?",
                        )
                        _budget_approved = True
                    except (ApprovalDenied, ApprovalTimeout):
                        return "budget", _budget_approved, _budget_warnings

        return None, _budget_approved, _budget_warnings

    async def _enforce_memory_cap(self, resp: "ModelResponse") -> Optional[str]:
        """Check memory cap, compact if needed, return stop reason or None.

        If the accumulated messages exceed ``memory_cap_mb``, attempt a forced
        compaction.  If still over the cap after compaction, remove a dangling
        assistant tool-use turn (if present) and return ``"memory_cap"`` so the
        loop terminates cleanly.
        """
        cap_mb = getattr(self.spec, "memory_cap_mb", None)
        if cap_mb is None:
            return None

        size = _messages_size_bytes(self.messages)
        if size <= cap_mb * 1024 * 1024:
            return None

        compacted = False
        try:
            compacted = await compact_session(self, force=True)
        except Exception:
            pass
        if compacted:
            size = _messages_size_bytes(self.messages)
        if size > cap_mb * 1024 * 1024:
            self.tracer_event(
                "memory_cap_exceeded",
                cap_mb=cap_mb,
                size_mb=round(size / 1024 / 1024, 2),
            )
            # If the last message is an assistant tool-use turn that has not
            # yet had its results appended, remove it so the history stays in
            # a valid state for future calls.
            if (
                resp.stop_reason == StopReason.TOOL_USE
                and self.messages
                and self.messages[-1].role == "assistant"
            ):
                self.messages.pop()
            return "memory_cap"
        return None

    async def _maybe_compact_reactive(self, exc: Exception, step: int) -> bool:
        """Handle a context-overflow exception via compaction. Returns True if recovered.

        Only attempts compaction when a CompactionPolicy is configured and the
        cooldown period has elapsed since the last compaction.  On success,
        updates ``_last_compact_at`` and emits a tracer event.  On failure,
        returns False (caller should re-raise the original exception).
        """
        import time as _time

        spec = self.spec
        if getattr(spec, "compaction", None) is None:
            return False

        now = _time.monotonic()
        _compact_cooldown = getattr(spec.compaction, "cooldown", 30.0)
        if now - self._last_compact_at < _compact_cooldown:
            return False

        compacted = False
        try:
            compacted = await compact_session(self, force=True)
            if compacted:
                self._last_compact_at = now
        except Exception:
            pass

        if compacted:
            self.tracer_event("context_overflow_compacted", step=step)
            return True
        return False

    async def _maybe_compact(self) -> None:
        """Auto-compact context if the spec's compaction policy is set."""
        policy = getattr(self.spec, "compaction", None)
        if policy is None:
            return
        try:
            compacted = await compact_session(self, policy=policy)
            if compacted:
                self.tracer_event("context_compacted", messages_after=len(self.messages))
        except Exception:
            pass  # compaction must never break a live session

    def _last_assistant_text(self) -> str:
        for m in reversed(self.messages):
            if m.role == "assistant" and m.text:
                return m.text
        return ""

    def _checkpoint(self) -> None:
        if not self.harness.checkpointer:
            return
        snap = None
        if isinstance(self.sandbox, VirtualSandbox):
            snap = self.sandbox.fs.snapshot()
        try:
            self.harness.checkpointer.save(
                self.id,
                messages=self.messages,
                fs_snapshot=snap,
                meta={"agent": self.spec.name, "at": time.time()},
            )
        except Exception as exc:
            self.last_checkpoint_error = exc
            self.tracer_event("checkpoint_error", error=str(exc))

    def restore(self, record: dict[str, Any]) -> None:
        self.messages = list(record.get("messages", []))
        snap = record.get("fs_snapshot")
        if snap and isinstance(self.sandbox, VirtualSandbox):
            self.sandbox.fs.restore(snap)


# ── OpenTelemetry GenAI semantic conventions ───────────────────────────────────
# Span attributes follow the OTel `gen_ai.*` schema so Tvastar traces drop into
# Braintrust / Honeycomb / Datadog dashboards without custom mapping.
# https://opentelemetry.io/docs/specs/semconv/gen-ai/


def _genai_request_attrs(spec: "AgentSpec", *, step: int, n_tools: int) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.system": getattr(spec.model, "system", "unknown"),
        "gen_ai.request.model": spec.model.name,
        "gen_ai.request.max_tokens": spec.max_tokens,
        "gen_ai.request.temperature": spec.temperature,
        # Tvastar-specific extras (kept namespaced so they don't clash):
        "tvastar.step": step,
        "tvastar.tools.visible": n_tools,
    }
    return attrs


def _set_genai_response_attrs(span: Any, resp: "ModelResponse") -> None:
    """Stamp gen_ai response attributes on the live model.generate span."""
    try:
        span.attributes["gen_ai.usage.input_tokens"] = resp.usage.input_tokens
        span.attributes["gen_ai.usage.output_tokens"] = resp.usage.output_tokens
        finish = getattr(resp.stop_reason, "value", str(resp.stop_reason))
        span.attributes["gen_ai.response.finish_reasons"] = [finish]
    except Exception:
        pass  # tracing must never break a run


# ── Structured result helpers ─────────────────────────────────────────────────


def _inject_schema_instruction(prompt: str, schema: Any) -> str:
    """Append a JSON-output instruction to the prompt."""
    schema_hint = _schema_hint(schema)
    return (
        f"{prompt}\n\n"
        f"Respond with valid JSON only (no markdown fences, no explanation) "
        f"matching this structure:\n{schema_hint}"
    )


def _schema_hint(schema: Any) -> str:
    """Best-effort schema description for the model."""
    try:
        # Pydantic v2
        if hasattr(schema, "model_json_schema"):
            import json

            return json.dumps(schema.model_json_schema(), indent=2)
        # Pydantic v1
        if hasattr(schema, "schema"):
            import json

            return json.dumps(schema.schema(), indent=2)
        # dataclass
        if hasattr(schema, "__dataclass_fields__"):
            fields = list(schema.__dataclass_fields__.keys())
            return "{" + ", ".join(f'"{f}": ...' for f in fields) + "}"
        # dict schema
        if isinstance(schema, dict):
            import json

            return json.dumps(schema, indent=2)
    except Exception:
        pass
    return str(schema)


def _try_parse(text: str, schema: Any) -> tuple[Any, bool]:
    """Attempt to parse structured output.

    Returns ``(parsed_data, True)`` on success or ``(error_string, False)`` on
    failure. Callers use the error string to build a correction message for the
    model.
    """
    import json
    import re

    cleaned = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError) as exc:
        return str(exc), False

    # Pydantic v2
    if hasattr(schema, "model_validate"):
        try:
            return schema.model_validate(data), True
        except Exception as exc:
            return str(exc), False
    # Pydantic v1
    if hasattr(schema, "parse_obj"):
        try:
            return schema.parse_obj(data), True
        except Exception as exc:
            return str(exc), False
    # callable validator
    if callable(schema):
        try:
            return schema(data), True
        except Exception as exc:
            return str(exc), False
    return data, True


def _correction_message(error: Any, schema: Any) -> str:
    """User message injected between retries when structured output fails."""
    return (
        "Your previous response was not valid JSON matching the required schema.\n"
        f"Problem: {error}\n\n"
        f"Required schema:\n{_schema_hint(schema)}\n\n"
        "Respond with valid JSON only — no markdown fences, no explanation."
    )


def _parse_structured(text: str, schema: Any) -> Any:
    """Parse the model's JSON text against the schema. Returns parsed data or raw text."""
    data, ok = _try_parse(text, schema)
    return data if ok else text


def _is_context_overflow(exc: Exception) -> bool:
    """Return True when the exception looks like a provider context-window error."""
    msg = str(exc).lower()
    return any(phrase in msg for phrase in _OVERFLOW_PHRASES)


def _messages_size_bytes(messages: list[Message]) -> int:
    """Estimate the in-memory byte size of the accumulated message list.

    Counts UTF-8-encoded text in all content blocks. Used to enforce
    ``memory_cap_mb`` on ``AgentSpec``. Intentionally approximate — the goal
    is to catch runaway growth, not to be exact to the byte.
    """
    import json as _json

    total = 0
    for m in messages:
        for b in m.blocks:
            if isinstance(b, TextBlock):
                total += len(b.text.encode("utf-8", errors="replace"))
            elif isinstance(b, ToolUseBlock):
                total += len(_json.dumps(b.input).encode("utf-8", errors="replace"))
                total += len(b.name)
            elif isinstance(b, ToolResultBlock):
                total += len(b.content.encode("utf-8", errors="replace"))
            elif isinstance(b, ImageBlock):
                total += len(b.data)  # base64 string
    return total
