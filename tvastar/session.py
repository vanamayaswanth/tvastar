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

from .errors import ToolError, ToolNotFound
from .memory.store import Memory
from .observability import Tracer
from .compaction import compact_session
from .profiles import MAX_TASK_DEPTH, AgentProfile
from .sandbox.base import Sandbox
from .sandbox.virtual import VirtualSandbox
from .skills.loader import Skill
from .tools.base import ToolContext, ToolRegistry
from .types import (
    Message,
    ModelResponse,
    StopReason,
    StreamEvent,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    Usage,
)

if TYPE_CHECKING:  # pragma: no cover
    from .agent import AgentSpec
    from .detect import Finding
    from .harness import Harness


@dataclass
class RunResult:
    """Outcome of a ``prompt`` call."""

    text: str
    messages: list[Message]
    usage: Usage
    steps: int
    stopped: str = "end_turn"  # end_turn | max_steps | error
    findings: list["Finding"] = field(default_factory=list)
    data: Optional[Any] = None  # populated when result= schema is used

    def __str__(self) -> str:
        return self.text

    @property
    def warnings(self) -> list["Finding"]:
        from .detect import Severity

        return [f for f in self.findings if f.severity != Severity.INFO]

    @property
    def ok(self) -> bool:
        return self.stopped == "end_turn" and not self.warnings


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

    def _system_prompt(self) -> str:
        base = self.spec.build_system_prompt()
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
        result: Optional[Any] = None,
    ) -> RunResult:
        """Send a user message and run the loop to completion.

        Args:
            text: The user prompt.
            result: Optional schema for structured output. Pass a callable
                    validator (e.g. a Pydantic model class) to validate and
                    parse the model's JSON response into ``RunResult.data``.
        """
        if not self._started:
            await self.start()
        prompt_text = text
        if result is not None:
            prompt_text = _inject_schema_instruction(text, result)
        self.messages.append(Message("user", prompt_text))
        with self.tracer.span("session.prompt", session=self.id, agent=self.spec.name):
            run_result = await self._run_loop()
        if result is not None:
            run_result.data = _parse_structured(run_result.text, result)
        return run_result

    async def skill(
        self,
        name: str,
        text: str,
        *,
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
                self.messages.append(Message("user", prompt_text))
                run_result = await self._run_loop()
            if result is not None:
                run_result.data = _parse_structured(run_result.text, result)
            return run_result
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
        """
        # ── depth guard ─────────────────────────────────────────────────────
        if self._task_depth >= MAX_TASK_DEPTH:
            raise RuntimeError(
                f"Task depth limit ({MAX_TASK_DEPTH}) reached. "
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

        # ── inject cwd into child system prompt ─────────────────────────────
        full_prompt = prompt
        if cwd:
            full_prompt = f"[Working directory: {cwd}]\n\n{prompt}"

        async def _run_child() -> RunResult:
            async with child:
                return await child.prompt(full_prompt, result=result)

        with self.tracer.span(
            "session.task",
            parent=self.id,
            depth=child._task_depth,
            profile=agent or "anonymous",
        ):
            if cancel_after is not None:
                try:
                    return await asyncio.wait_for(_run_child(), timeout=cancel_after)
                except asyncio.TimeoutError:
                    raise asyncio.TimeoutError(
                        f"Task cancelled after {cancel_after}s "
                        f"(agent={agent or 'anonymous'}, prompt={prompt[:60]!r})"
                    )
            return await _run_child()

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
            detect=False,  # child runs don't double-detect
            subagents=nested_subagents,
        )

    def tracer_event(self, kind: str, **data: Any) -> None:
        safe = {f"attr_{k}" if k == "name" else k: v for k, v in data.items()}
        with self.tracer.span(f"event.{kind}", **safe):
            pass

    # ---- the loop -----------------------------------------------------------

    async def _run_loop(self) -> RunResult:
        total = Usage()
        steps = 0
        stopped = "end_turn"
        spec = self.spec
        tools: list[ToolSpec] = self._active_tools().specs

        while steps < spec.max_steps:
            steps += 1
            with self.tracer.span("model.generate", step=steps, model=spec.model.name):
                resp: ModelResponse = await spec.model.generate(
                    self.messages,
                    system=self._system_prompt(),
                    tools=tools or None,
                    max_tokens=spec.max_tokens,
                    temperature=spec.temperature,
                    thinking_level=spec.thinking_level,
                )
            total = total + resp.usage
            self.messages.append(resp.message)

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
        )
        result.findings = self._detect(result)
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

    async def _execute_tools(self, uses: list[ToolUseBlock]) -> list[ToolResultBlock]:
        registry = self._active_tools()
        ctx = self._tool_context()

        default_retry = getattr(self.spec, "tool_retry", None)

        async def run_one(use: ToolUseBlock) -> ToolResultBlock:
            with self.tracer.span("tool.invoke", tool=use.name) as sp:
                try:
                    tool = registry.get(use.name)
                    out = await tool.invoke(use.input, ctx, default_retry=default_retry)
                    return ToolResultBlock(tool_use_id=use.id, content=out)
                except ToolNotFound as e:
                    sp.status = "error"
                    return ToolResultBlock(use.id, f"[error] {e}", is_error=True)
                except ToolError as e:
                    sp.status = "error"
                    return ToolResultBlock(use.id, f"[error] {e}", is_error=True)

        return list(await asyncio.gather(*(run_one(u) for u in uses)))

    # ---- streaming variant --------------------------------------------------

    async def stream(self, text: str) -> AsyncIterator[StreamEvent]:
        if not self._started:
            await self.start()
        self.messages.append(Message("user", text))
        spec = self.spec
        tools = self._active_tools().specs
        steps = 0
        while steps < spec.max_steps:
            steps += 1
            yield StreamEvent("turn_start", {"step": steps})
            resp: Optional[ModelResponse] = None
            async for ev in spec.model.stream(
                self.messages,
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
            assert resp is not None
            self.messages.append(resp.message)
            if resp.stop_reason != StopReason.TOOL_USE and not resp.tool_uses:
                yield StreamEvent("turn_end", {"text": resp.message.text})
                break
            for use in resp.tool_uses:
                yield StreamEvent("tool_call", {"name": use.name, "input": use.input})
            results = await self._execute_tools(resp.tool_uses)
            for r in results:
                yield StreamEvent("tool_result", {"content": r.content, "error": r.is_error})
            self.messages.append(Message("user", results))
            self._checkpoint()

    # ---- helpers ------------------------------------------------------------

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
        except Exception:
            pass

    def restore(self, record: dict[str, Any]) -> None:
        self.messages = list(record.get("messages", []))
        snap = record.get("fs_snapshot")
        if snap and isinstance(self.sandbox, VirtualSandbox):
            self.sandbox.fs.restore(snap)


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


def _parse_structured(text: str, schema: Any) -> Any:
    """Parse the model's JSON text against the schema. Returns parsed data or raw text."""
    import json
    import re

    # strip markdown fences if the model ignored instructions
    cleaned = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return text  # couldn't parse — return raw

    # Pydantic v2
    if hasattr(schema, "model_validate"):
        try:
            return schema.model_validate(data)
        except Exception:
            return data
    # Pydantic v1
    if hasattr(schema, "parse_obj"):
        try:
            return schema.parse_obj(data)
        except Exception:
            return data
    # callable validator
    if callable(schema):
        try:
            return schema(data)
        except Exception:
            return data
    return data
