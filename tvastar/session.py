"""Session — a stateful conversation that runs the agent loop.

The loop is the core of the harness:

    1. append the user prompt
    2. call the model with the system prompt + tools
    3. if the model requested tools, execute them (concurrently) and feed
       results back, then go to 2
    4. otherwise return the assistant's final message

Around that loop we layer: skills (inject extra instructions + scope tools),
sub-tasks (delegated child sessions), durable checkpoints (after every turn),
and tracing. Sessions are cheap and isolated, so a single harness scales to
many concurrent conversations.
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

    def __str__(self) -> str:
        return self.text

    @property
    def warnings(self) -> list["Finding"]:
        """Findings at WARNING severity or above (the ones worth surfacing)."""
        from .detect import Severity

        return [f for f in self.findings if f.severity != Severity.INFO]

    @property
    def ok(self) -> bool:
        """True when the run finished cleanly with no warning/error findings."""
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

    # ---- lifecycle ------------------------------------------------------

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

    # ---- tool context / scoping ----------------------------------------

    def _tool_context(self) -> ToolContext:
        return ToolContext(
            sandbox=self.sandbox,
            filesystem=self.sandbox.fs if self.sandbox else None,
            memory=self.memory,
            session=self,
        )

    def _active_tools(self) -> ToolRegistry:
        """Tools available right now, narrowed by an active skill if any."""
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

    # ---- public API -----------------------------------------------------

    async def prompt(self, text: str) -> RunResult:
        """Send a user message and run the loop to completion."""
        if not self._started:
            await self.start()
        self.messages.append(Message("user", text))
        with self.tracer.span("session.prompt", session=self.id, agent=self.spec.name):
            return await self._run_loop()

    async def skill(self, name: str, text: str) -> RunResult:
        """Invoke a named skill for one prompt, then deactivate it."""
        skill = self.spec.skills.get(name)
        self.tracer_event("skill_loaded", name=name)
        prev = self._active_skill
        self._active_skill = skill
        try:
            with self.tracer.span("session.skill", skill=name, session=self.id):
                if not self._started:
                    await self.start()
                self.messages.append(Message("user", text))
                return await self._run_loop()
        finally:
            self._active_skill = prev

    async def task(self, instructions: str, prompt: str, **kw: Any) -> RunResult:
        """Delegate to a fresh sub-agent (child session) and return its result.

        The child runs in its own session (and its own sandbox) so it can't
        clobber this conversation — the standard sub-agent pattern.
        """
        from .agent import create_agent

        child_spec = create_agent(
            name=f"{self.spec.name}:task",
            model=self.spec.model,
            instructions=instructions,
            tools=[self.spec.tools.get(n) for n in self.spec.tools.names()],
            skills=self.spec.skills,
            sandbox=self.spec.sandbox_factory,
            max_steps=kw.get("max_steps", self.spec.max_steps),
        )
        with self.tracer.span("session.task", parent=self.id):
            self.tracer_event("task_spawned", instructions=instructions[:80])
            child = self.harness.session(spec=child_spec)
            async with child:
                return await child.prompt(prompt)

    def tracer_event(self, kind: str, **data: Any) -> None:
        # lightweight breadcrumb without opening a span. Guard against data keys
        # (e.g. "name") colliding with span()'s positional argument.
        safe = {f"attr_{k}" if k == "name" else k: v for k, v in data.items()}
        with self.tracer.span(f"event.{kind}", **safe):
            pass

    # ---- the loop -------------------------------------------------------

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
                )
            total = total + resp.usage
            self.messages.append(resp.message)

            if resp.stop_reason != StopReason.TOOL_USE and not resp.tool_uses:
                stopped = "end_turn"
                break

            # Execute requested tools concurrently, preserving order in results.
            results = await self._execute_tools(resp.tool_uses)
            self.messages.append(Message("user", results))
            # checkpoint after each tool round
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
        """Run the configured failure detectors over the finished run.

        Cheap, in-process, and isolated — detector errors never affect the run.
        Each finding is also emitted as a trace breadcrumb for observability.
        """
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

        async def run_one(use: ToolUseBlock) -> ToolResultBlock:
            with self.tracer.span("tool.invoke", tool=use.name) as sp:
                try:
                    tool = registry.get(use.name)
                    out = await tool.invoke(use.input, ctx)
                    return ToolResultBlock(tool_use_id=use.id, content=out)
                except ToolNotFound as e:
                    sp.status = "error"
                    return ToolResultBlock(use.id, f"[error] {e}", is_error=True)
                except ToolError as e:
                    sp.status = "error"
                    return ToolResultBlock(use.id, f"[error] {e}", is_error=True)

        return list(await asyncio.gather(*(run_one(u) for u in uses)))

    # ---- streaming variant ---------------------------------------------

    async def stream(self, text: str) -> AsyncIterator[StreamEvent]:
        """Like ``prompt`` but yields StreamEvents as they happen."""
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

    # ---- helpers --------------------------------------------------------

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
        except Exception:  # durability must never break a live run
            pass

    def restore(self, record: dict[str, Any]) -> None:
        """Rehydrate from a checkpoint record (see Checkpointer.load)."""
        self.messages = list(record.get("messages", []))
        snap = record.get("fs_snapshot")
        if snap and isinstance(self.sandbox, VirtualSandbox):
            self.sandbox.fs.restore(snap)
