"""Harness — the configured handle that manages models, tools, sandboxes,
sessions, memory, durability, and observability.

``Agent = Model + Harness``. You pass an AgentSpec; the harness runs it across
one or more Sessions.

New in 0.2.0:
- ``harness.fs``       — application-level filesystem access (stage inputs,
                          collect outputs) backed by the agent's sandbox.
- ``harness.shell()``  — run a shell command from application code (not the
                          agent's tool layer) for workspace preparation.
- Named sessions via ``harness.session(name='branch-a')``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Optional

from .agent import AgentSpec
from .durable import Checkpointer
from .memory.store import InMemoryStore, Store
from .observability import NULL_TRACER, Tracer
from .session import RunResult, Session


class HarnessFS:
    """Application-level filesystem access backed by the agent's sandbox.

    This is *separate* from the agent's built-in file tools — it lets your
    orchestration code stage inputs before the agent runs and collect outputs
    after it finishes, without going through the model tool layer.

    Example::

        harness = Harness(agent)
        async with harness:
            await harness.fs.write_file("input.md", document_text)
            result = await harness.run("Summarise input.md and write output.md")
            summary = await harness.fs.read_file("output.md")
    """

    def __init__(self, sandbox: Any):
        self._sandbox = sandbox
        self._started = False

    async def _ensure(self) -> Any:
        if not self._started:
            await self._sandbox.start()
            self._started = True
        return self._sandbox.fs

    async def write_file(self, path: str, content: str) -> None:
        """Write a file into the agent's sandbox workspace."""
        fs = await self._ensure()
        fs.write(path, content)

    async def read_file(self, path: str) -> str:
        """Read a file from the agent's sandbox workspace."""
        fs = await self._ensure()
        if not fs.exists(path):
            raise FileNotFoundError(f"No such file in sandbox: {path}")
        return fs.read(path)

    async def exists(self, path: str) -> bool:
        fs = await self._ensure()
        return fs.exists(path)

    async def list_dir(self, path: str = ".") -> list[str]:
        fs = await self._ensure()
        return fs.listdir(path)

    async def delete_file(self, path: str) -> None:
        fs = await self._ensure()
        if fs.exists(path):
            fs.delete(path)


class Harness:
    def __init__(
        self,
        spec: AgentSpec,
        *,
        store: Optional[Store] = None,
        tracer: Optional[Tracer] = None,
        durable: bool = True,
    ):
        self.spec = spec
        self.store: Store = store or InMemoryStore()
        self.tracer: Tracer = tracer or NULL_TRACER
        self.checkpointer: Optional[Checkpointer] = Checkpointer(self.store) if durable else None
        self._sessions: dict[str, Session] = {}
        self._sandbox: Any = None  # lazily created shared sandbox
        self._fs: Optional[HarnessFS] = None

    # ---- application-level filesystem / shell --------------------------------

    def _get_sandbox(self) -> Any:
        """Lazily create a sandbox shared by harness.fs and harness.shell()."""
        if self._sandbox is None:
            self._sandbox = self.spec.sandbox_factory()
        return self._sandbox

    @property
    def fs(self) -> HarnessFS:
        """Application-level filesystem proxy for the agent's sandbox.

        Use this to stage input files before a run and read output files after.
        The sandbox is shared with sessions created by this harness when the
        agent spec's sandbox factory returns the same instance (e.g. LocalSandbox).
        For VirtualSandbox (the default), note that each session creates its own
        in-memory filesystem — use ``harness.session()`` and work through the
        session's sandbox if you need the agent to see harness-written files.
        """
        if self._fs is None:
            self._fs = HarnessFS(self._get_sandbox())
        return self._fs

    async def shell(self, cmd: str, *, timeout: Optional[float] = None) -> str:
        """Run a shell command in the agent's sandbox from application code.

        This is for *orchestration* preparation (e.g. ``git clone``, file setup)
        before handing work to the agent. It does not add messages to any session.

        Returns the combined stdout/stderr output string.
        """
        sandbox = self._get_sandbox()
        if not getattr(sandbox, "_started", False):
            await sandbox.start()
        result = await sandbox.exec(cmd, timeout=timeout)
        return result.render()

    # ---- context manager for lifecycle management ---------------------------

    async def __aenter__(self) -> "Harness":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._sandbox is not None:
            await self._sandbox.stop()

    # ---- sessions -----------------------------------------------------------

    def _release(self, sid: str) -> None:
        """Remove a completed one-shot session from the in-memory registry.

        Called automatically after ``run()`` and ``fan_out()`` finish and after
        each :class:`~tvastar.graph.TaskGraph` task completes so anonymous
        sessions don't accumulate in long-running servers.
        """
        self._sessions.pop(sid, None)

    def session(
        self,
        name: Optional[str] = None,
        *,
        spec: Optional[AgentSpec] = None,
        session_id: Optional[str] = None,
    ) -> Session:
        """Create (or fetch by id/name) a session bound to this harness.

        ``name`` is a human-friendly alias for the session, useful for parallel
        branches::

            api_branch  = harness.session('api-review')
            auth_branch = harness.session('auth-review')
            results = await asyncio.gather(
                api_branch.task('Review API risks.'),
                auth_branch.task('Review auth risks.'),
            )
        """
        sid = name or session_id
        if sid and sid in self._sessions:
            return self._sessions[sid]
        eff_spec = spec or self.spec
        # Give each session its own GovernancePolicy copy so set_phase() calls
        # in one concurrent session don't mutate the phase seen by others.
        gov = getattr(eff_spec, "governance", None)
        if gov is not None:
            import dataclasses

            eff_spec = dataclasses.replace(eff_spec, governance=gov.copy())
        s = Session(spec=eff_spec, harness=self)
        if sid:
            s.id = sid
        self._sessions[s.id] = s
        return s

    def resume(self, session_id: str) -> Optional[Session]:
        """Rehydrate a session from a durable checkpoint, if one exists."""
        if not self.checkpointer:
            return None
        record = self.checkpointer.load(session_id)
        if not record:
            return None
        s = self.session(session_id=session_id)
        s.sandbox = s.spec.sandbox_factory()
        s._started = True
        s.restore(record)
        return s

    def list_sessions(self) -> list[str]:
        if not self.checkpointer:
            return list(self._sessions)
        return sorted(set(self._sessions) | set(self.checkpointer.list_sessions()))

    # ---- convenience --------------------------------------------------------

    async def run(
        self,
        prompt: str,
        *,
        session_id: Optional[str] = None,
        result: Optional[Any] = None,
        cancel_after: Optional[float] = None,
    ) -> RunResult:
        """One-shot: open a session, run a prompt, close it.

        Args:
            prompt: The user prompt.
            session_id: Reuse/resume a specific session id.
            result: Optional schema for structured output (see Session.prompt).
            cancel_after: Optional timeout in seconds.
        """
        s = self.session(session_id=session_id)
        async with s:
            run_result = await s.prompt(prompt, result=result, cancel_after=cancel_after)
        if session_id is None:
            self._release(s.id)  # prune anonymous one-shot sessions
        return run_result

    @asynccontextmanager
    async def transaction(self, session: Session) -> AsyncIterator[Session]:
        """Snapshot the session sandbox before the block; restore on exception.

        Makes a group of agent steps atomically safe: if any step (or any code
        in the ``async with`` block) raises, the sandbox filesystem is rolled
        back to the state it was in before the block started. The exception is
        re-raised after rollback so callers can handle it.

        Only :class:`~tvastar.sandbox.virtual.VirtualSandbox` supports this
        today. For :class:`~tvastar.sandbox.local.LocalSandbox` the context
        manager yields normally but performs no snapshot/restore.

        Example::

            async with sess:
                async with harness.transaction(sess) as s:
                    await s.prompt("Write the migration script")
                    await s.prompt("Run the tests")
                # On exception: filesystem rolled back, exception re-raised.
        """
        sandbox = getattr(session, "sandbox", None)
        snap = None
        if sandbox is not None:
            try:
                snap = sandbox.snapshot()
            except NotImplementedError:
                snap = None  # sandbox doesn't support snapshots; proceed without

        try:
            yield session
        except Exception:
            if snap is not None:
                try:
                    sandbox.restore(snap)
                    with self.tracer.span("workspace_rollback", session=session.id):
                        pass
                except Exception as restore_exc:
                    # Rollback failed — log it so the caller knows the workspace
                    # was NOT restored, then re-raise the original error.
                    with self.tracer.span(
                        "workspace_rollback_failed",
                        session=session.id,
                        error=str(restore_exc),
                    ):
                        pass
            raise

    async def fan_out(
        self,
        prompts: list,
        *,
        concurrency: Optional[int] = 8,
    ) -> list[RunResult]:
        """Run multiple prompts concurrently, each in its own session.

        Args:
            prompts: A list of either:
                - ``str`` — a plain prompt (uses agent defaults)
                - ``dict`` — extended form with keys:
                    ``prompt`` (required), ``agent`` (profile name),
                    ``result`` (schema), ``cancel_after`` (timeout seconds),
                    ``thinking_level``, ``max_steps``
            concurrency: Maximum concurrent sessions. Defaults to 8 to avoid
            thundering-herd retry storms when a provider rate-limits.
            Pass ``None`` to run all prompts simultaneously (use with care).

        Returns:
            A list of ``RunResult`` in the same order as ``prompts``.

        Example::

            results = await harness.fan_out([
                "Review the auth package.",
                {"prompt": "Review the API layer.", "agent": "reviewer",
                 "cancel_after": 30.0},
            ])
        """
        import asyncio as _asyncio

        async def _run_one(item) -> RunResult:
            if isinstance(item, str):
                prompt_text = item
                agent_name = None
                result_schema = None
                cancel_after = None
                thinking_level = None
                max_steps = None
            else:
                prompt_text = item["prompt"]
                agent_name = item.get("agent")
                result_schema = item.get("result")
                cancel_after = item.get("cancel_after")
                thinking_level = item.get("thinking_level")
                max_steps = item.get("max_steps")

            sess = self.session()
            async with sess:
                if agent_name or thinking_level or max_steps or result_schema or cancel_after:
                    fan_result = await sess.task(
                        prompt_text,
                        agent=agent_name,
                        result=result_schema,
                        cancel_after=cancel_after,
                        thinking_level=thinking_level,
                        max_steps=max_steps,
                    )
                else:
                    fan_result = await sess.prompt(prompt_text, result=result_schema)
            self._release(sess.id)  # prune completed fan-out sessions
            return fan_result

        if concurrency is None:
            # Unlimited — caller opted in explicitly; all prompts fire at once.
            return list(await _asyncio.gather(*(_run_one(p) for p in prompts)))

        # Bounded concurrency via semaphore
        sem = _asyncio.Semaphore(concurrency)

        async def _bounded(item) -> RunResult:
            async with sem:
                return await _run_one(item)

        return list(await _asyncio.gather(*(_bounded(p) for p in prompts)))
