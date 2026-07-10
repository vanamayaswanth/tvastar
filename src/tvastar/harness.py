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

import time
import warnings
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Optional

from .agent import AgentSpec
from .conversation.reducer import reduce
from .conversation.records import RecordType, record_to_dict, Record
from .conversation.writer import ConversationWriter
from .durable import Checkpointer
from .memory.store import InMemoryStore, Store
from .observability import NULL_TRACER, Tracer
from .sandbox.virtual import VirtualSandbox

if TYPE_CHECKING:
    from .conversation.session_info import SessionInfo
from .session import RunResult, Session


def _detect_interrupted_tools(log: list[dict[str, Any]]) -> list[str]:
    """Find tool_call_started records without a matching tool_result (by call_id).

    Returns the list of unresolved call_ids. Pure scan, no mutation.
    """
    started: set[str] = set()
    completed: set[str] = set()
    for record in log:
        if not isinstance(record, dict):
            continue
        rtype = record.get("type")
        data = record.get("data", {})
        call_id = data.get("call_id")
        if not call_id:
            continue
        if rtype == RecordType.TOOL_CALL_STARTED.value:
            started.add(call_id)
        elif rtype == RecordType.TOOL_RESULT.value:
            completed.add(call_id)
    return list(started - completed)


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
        compaction_threshold: int = 500,
    ):
        self.spec = spec
        self.store: Store = store or InMemoryStore()
        self.tracer: Tracer = tracer or NULL_TRACER
        self._durable: bool = durable
        self._compaction_threshold: int = compaction_threshold
        # ponytail: keep _checkpointer for one release cycle (backward compat)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            self._checkpointer: Optional[Checkpointer] = (
                Checkpointer(self.store) if durable else None
            )
        self._sessions: dict[str, Session] = {}
        self._sandbox: Any = None  # lazily created shared sandbox
        self._fs: Optional[HarnessFS] = None

    @property
    def checkpointer(self) -> Optional[Checkpointer]:
        """Deprecated: use the writer/reducer pattern instead."""
        warnings.warn(
            "Harness.checkpointer is deprecated; durability is now handled by "
            "ConversationWriter/reduce. Use harness._durable to check mode.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._checkpointer

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
        if sid:
            s = Session(spec=eff_spec, harness=self, id=sid)
        else:
            s = Session(spec=eff_spec, harness=self)
        self._sessions[s.id] = s
        return s

    def resume(self, session_id: str) -> Optional[Session]:
        """Resume a session from its event log, or return None."""
        if not self._durable:
            return None
        # Try new event-log approach first
        key = f"event_log:{session_id}"
        log = self.store.get(key)
        if log:
            s = self.session(name=session_id)
            # Detect and mark interrupted tool calls before reducing (REQ 11.3, 11.4, 11.5, 11.6)
            interrupted_ids = _detect_interrupted_tools(log)
            if interrupted_ids:
                from .durable import message_to_dict
                from .types import Message, ToolResultBlock

                for call_id in interrupted_ids:
                    # Append interrupted_marker as a TOOL_RESULT record
                    seq = len(log)
                    marker_record = Record(
                        type=RecordType.TOOL_RESULT,
                        seq=seq,
                        timestamp=time.time(),
                        data={
                            "call_id": call_id,
                            "tool_use_id": call_id,
                            "content": "[interrupted] Tool execution was interrupted and never completed.",
                            "is_error": True,
                            "interrupted": True,
                        },
                    )
                    log.append(record_to_dict(marker_record))
                    # Append a USER_MESSAGE with the tool result so the model sees it in context
                    msg = Message(
                        "user",
                        [
                            ToolResultBlock(
                                tool_use_id=call_id,
                                content="[interrupted] Tool execution was interrupted and never completed.",
                                is_error=True,
                            )
                        ],
                    )
                    seq = len(log)
                    msg_record = Record(
                        type=RecordType.USER_MESSAGE,
                        seq=seq,
                        timestamp=time.time(),
                        data={"message": message_to_dict(msg)},
                    )
                    log.append(record_to_dict(msg_record))
                # Persist updated log with markers
                self.store.set(key, log)

            s.messages = reduce(log)
            s._writer = ConversationWriter(
                self.store,
                session_id,
                compaction_threshold=self._compaction_threshold,
                event_bus=getattr(self, "_event_bus", None),
            )
            s._writer._seq = len(log)
            s.sandbox = s.spec.sandbox_factory()
            s._started = True
            # Restore filesystem snapshot from legacy checkpoint if available
            if self._checkpointer:
                record = self._checkpointer.load(session_id)
                if record:
                    snap = record.get("fs_snapshot")
                    if snap and isinstance(s.sandbox, VirtualSandbox):
                        s.sandbox.fs.restore(snap)
            return s
        # Fallback to legacy checkpointer for sessions created before migration
        if self._checkpointer:
            record = self._checkpointer.load(session_id)
            if record:
                s = self.session(session_id=session_id)
                s.sandbox = s.spec.sandbox_factory()
                s._started = True
                s.restore(record)
                return s
        return None

    def list_sessions(self, filter: Optional[str] = None, limit: int = 100) -> "list[SessionInfo]":
        """Return metadata for all known sessions (in-memory + persisted).

        When *filter* is provided, only sessions whose ID contains the filter
        string are returned.  Results are capped at *limit*.
        """
        from .conversation.session_info import SessionInfo

        sessions: dict[str, SessionInfo] = {}

        # Persisted sessions from session_meta:* entries in the store
        for key in self.store.keys(prefix="session_meta:"):
            meta = self.store.get(key)
            if meta and isinstance(meta, dict):
                sid = meta.get("id") or meta.get("persistence_key") or meta.get("name", "")
                info = SessionInfo(
                    id=sid,
                    last_activity=meta.get("last_activity", 0.0),
                )
                sessions[info.id] = info

        # In-memory active sessions (may not yet have persisted metadata)
        import time

        for sid in self._sessions:
            if sid not in sessions:
                sessions[sid] = SessionInfo(
                    id=sid,
                    last_activity=time.time(),
                )

        result = list(sessions.values())

        if filter is not None:
            result = [s for s in result if filter in s.id]

        return result[:limit]

    def delete_session(self, session_id: str) -> bool:
        """Remove all event log records and metadata for a session.

        Returns True if the session existed and was deleted, False otherwise.
        """
        key_log = f"event_log:{session_id}"
        key_meta = f"session_meta:{session_id}"
        exists = self.store.get(key_log) is not None or self.store.get(key_meta) is not None
        if not exists:
            return False
        self.store.delete(key_log)
        self.store.delete(key_meta)
        self._sessions.pop(session_id, None)
        return True

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
        with self.tracer.span("harness.run", agent=self.spec.name):
            s = self.session(session_id=session_id)
            async with s:
                run_result = await s.prompt(prompt, result=result, cancel_after=cancel_after)
            if session_id is None:
                self._release(s.id)  # prune anonymous one-shot sessions
        return run_result

    @asynccontextmanager
    async def transaction(self, session: Session) -> AsyncIterator[Session]:
        """Snapshot the session sandbox and messages before the block; restore on exception.

        Makes a group of agent steps atomically safe: if any step (or any code
        in the ``async with`` block) raises, the sandbox filesystem and session
        messages are rolled back to the state they were in before the block
        started. The exception is re-raised after rollback so callers can
        handle it.

        Child tasks (via ``session.task()``) within a transaction are also
        rolled back since they share the parent session's sandbox. The child
        inherits the parent's sandbox instance, so a rollback restores the
        filesystem for both parent and child operations.

        Only :class:`~tvastar.sandbox.virtual.VirtualSandbox` supports this
        today. For :class:`~tvastar.sandbox.local.LocalSandbox` the context
        manager yields normally but performs no snapshot/restore (for the
        filesystem portion).

        Example::

            async with sess:
                async with harness.transaction(sess) as s:
                    await s.prompt("Write the migration script")
                    await s.prompt("Run the tests")
                # On exception: messages + filesystem rolled back, exception re-raised.
        """
        sandbox = getattr(session, "sandbox", None)
        snap = None
        if sandbox is not None:
            try:
                snap = sandbox.snapshot()
            except NotImplementedError:
                snap = None  # sandbox doesn't support snapshots; proceed without

        # Snapshot session messages before the block
        messages_snapshot = list(session.messages)

        try:
            yield session
        except Exception:
            # Rollback session messages to pre-transaction state
            session.messages[:] = messages_snapshot

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
        with self.tracer.span("harness.fan_out", agent=self.spec.name, n_prompts=len(prompts)):
            return await self._fan_out_inner(prompts, concurrency=concurrency)

    async def _fan_out_inner(
        self,
        prompts: list,
        *,
        concurrency: Optional[int] = 8,
    ) -> list[RunResult]:
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
