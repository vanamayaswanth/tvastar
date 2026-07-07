"""dispatch() — fire-and-observe async input delivery for event-driven agents.

Enables chat/webhook/queue patterns where the caller does NOT wait for the
agent to finish responding. The agent processes the input in the background;
results are observable via callbacks or the session's message history.

Usage (chat webhook)::

    from tvastar.dispatch import dispatch, DispatchInput

    # In a webhook handler:
    await dispatch(
        agent,
        id="thread_42",
        session="thread_42",
        input=DispatchInput(type="chat.message", text=message.text),
        on_complete=lambda result: send_reply(result.text),
    )
    return "ok"   # respond to platform immediately

The agent runs in the background. ``on_complete`` is called when it finishes.

Usage (fire-and-forget)::

    await dispatch(agent, id="job_1", input=DispatchInput(text="Summarise report.md"))
    # Returns immediately. Check logs / observe for results.

Usage (observe all dispatched activity)::

    from tvastar.dispatch import observe_dispatch

    observe_dispatch(lambda event: print(event))

Usage (isolated dispatch pools)::

    from tvastar.dispatch import DispatchPool

    pool = DispatchPool(max_harness_cache=100)
    await pool.dispatch(spec, id="job_1", text="hello")
    pool.close()  # release all cached harnesses
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

from .agent import AgentSpec
from .harness import Harness
from .memory.store import InMemoryStore, Store
from .session import RunResult

if TYPE_CHECKING:
    from .observability import Tracer


# ── Input types ──────────────────────────────────────────────────────────────


@dataclass
class DispatchInput:
    """Normalised input payload delivered to an agent session via dispatch()."""

    text: str = ""
    type: str = "chat.message"  # chat.message | task | event | custom
    message_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Dispatch event ────────────────────────────────────────────────────────────


@dataclass
class DispatchEvent:
    """Emitted to observers at key lifecycle moments of a dispatched run."""

    type: str  # dispatch_start | dispatch_end | dispatch_error
    dispatch_id: str
    agent_id: str  # the 'id' field (e.g. thread id, user id)
    session_id: str
    at: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)


# ── DispatchPool: encapsulated dispatch state ─────────────────────────────────


class DispatchPool:
    """Encapsulated dispatch state — no more module-level mutables.

    Each DispatchPool instance maintains its own isolated state for active
    dispatches, session locks, harness cache, and observers. This allows
    running multiple independent dispatch pools without memory leaks or
    cross-contamination.

    Args:
        max_harness_cache: Maximum number of cached Harness instances.
            When exceeded, least-recently-used inactive entries are evicted.
            Defaults to 500.
    """

    def __init__(self, max_harness_cache: int = 500) -> None:
        self.max_harness_cache = max_harness_cache
        self._active: dict[str, asyncio.Task] = {}
        self._dispatch_agent_ids: dict[str, str] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._default_harnesses: dict[str, Harness] = {}
        self._observers: list[Callable[[DispatchEvent], Any]] = []
        self._default_store = InMemoryStore()

    # ── Observer management ───────────────────────────────────────────────

    def observe(self, callback: Callable[[DispatchEvent], Any]) -> None:
        """Register a callback that receives every DispatchEvent from this pool.

        Callbacks are invoked synchronously (keep them lightweight — filter, log,
        enqueue). Exceptions in callbacks are silently swallowed so they never
        break a live agent run.
        """
        self._observers.append(callback)

    def unobserve(self, callback: Callable[[DispatchEvent], Any]) -> bool:
        """Unregister a previously registered observer. Returns True if found."""
        try:
            self._observers.remove(callback)
            return True
        except ValueError:
            return False

    def _emit(self, event: DispatchEvent) -> None:
        for obs in self._observers:
            try:
                obs(event)
            except Exception:
                pass

    # ── Active dispatch management ────────────────────────────────────────

    def cancel(self, dispatch_id: str) -> bool:
        """Cancel an in-flight dispatched run by its dispatch_id.

        Returns True if the task was found and cancelled, False otherwise.
        """
        task = self._active.get(dispatch_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    def list_active(self) -> list[str]:
        """Return dispatch_ids of currently running dispatched runs."""
        return [did for did, t in self._active.items() if not t.done()]

    # ── Core dispatch ─────────────────────────────────────────────────────

    async def dispatch(
        self,
        spec: AgentSpec,
        *,
        id: str,
        session: Optional[str] = None,
        input: Optional[DispatchInput] = None,
        text: Optional[str] = None,
        store: Optional[Store] = None,
        on_complete: Optional[Callable[[RunResult], Any]] = None,
        on_error: Optional[Callable[[Exception], Any]] = None,
        cancel_after: Optional[float] = None,
        tracer: Optional["Tracer"] = None,
    ) -> str:
        """Deliver input to an agent session asynchronously (fire-and-observe).

        Creates a background task that runs the agent against the given input.
        Returns a ``dispatch_id`` immediately — the agent runs in the background.

        Args:
            spec: The AgentSpec to run.
            id: Instance identity (e.g. thread_id, user_id, repo_id). Agents with
                the same ``id`` share a Harness and accumulate session history.
            session: Session identity within the instance. Defaults to ``id``.
                Use separate session values to run parallel conversation branches
                for the same instance.
            input: Structured input. Pass either this or ``text``.
            text: Shorthand for ``DispatchInput(text=text)``.
            store: Backing store for session persistence. Shared across calls with
                the same ``id`` if you pass the same store instance.
            on_complete: Called with RunResult when the agent finishes.
            on_error: Called with the exception if the agent errors.
            cancel_after: Timeout in seconds for the background run.

        Returns:
            dispatch_id — use with ``cancel()`` or observers.
        """
        if input is None:
            input = DispatchInput(text=text or "")

        session_id = session or id
        dispatch_id = f"dispatch_{uuid.uuid4().hex[:12]}"

        # Reuse or create a harness per agent instance id
        shared_store = store or self._default_store
        if id not in self._default_harnesses:
            if len(self._default_harnesses) > self.max_harness_cache:
                # LRU eviction: find the least-recently-used harness
                # (the one with no active dispatches; fall back to oldest if all active)
                # Build set of agent IDs that have running dispatch tasks
                active_agent_ids: set[str] = set()
                for did, t in self._active.items():
                    if not t.done():
                        agent_id_for_dispatch = self._dispatch_agent_ids.get(did)
                        if agent_id_for_dispatch is not None:
                            active_agent_ids.add(agent_id_for_dispatch)

                evict_key = None
                for key in self._default_harnesses:
                    if key not in active_agent_ids:
                        evict_key = key
                        break
                if evict_key is None:
                    # All are active — evict the oldest
                    evict_key = next(iter(self._default_harnesses))
                self._default_harnesses.pop(evict_key, None)
            self._default_harnesses[id] = Harness(
                spec, store=shared_store, durable=True, tracer=tracer
            )
        harness = self._default_harnesses[id]

        self._emit(
            DispatchEvent(
                type="dispatch_start",
                dispatch_id=dispatch_id,
                agent_id=id,
                session_id=session_id,
                data={"text": input.text[:200], "input_type": input.type},
            )
        )

        async def _run() -> None:
            try:
                prompt_text = input.text
                if input.metadata:
                    import json

                    prompt_text = f"{prompt_text}\n\n[context: {json.dumps(input.metadata)}]"

                sess = harness.session(session_id)
                # Serialize concurrent access to the same session to prevent
                # message corruption (Bug #3: concurrent dispatch to same session)
                lock_key = f"{id}:{session_id}"
                if lock_key not in self._session_locks:
                    self._session_locks[lock_key] = asyncio.Lock()
                async with self._session_locks[lock_key]:
                    coro = sess.prompt(prompt_text)
                    if cancel_after is not None:
                        result = await asyncio.wait_for(coro, timeout=cancel_after)
                    else:
                        result = await coro

                self._emit(
                    DispatchEvent(
                        type="dispatch_end",
                        dispatch_id=dispatch_id,
                        agent_id=id,
                        session_id=session_id,
                        data={"stopped": result.stopped, "steps": result.steps},
                    )
                )
                if on_complete:
                    try:
                        ret = on_complete(result)
                        if asyncio.iscoroutine(ret):
                            await ret
                    except Exception:
                        pass
            except Exception as exc:
                self._emit(
                    DispatchEvent(
                        type="dispatch_error",
                        dispatch_id=dispatch_id,
                        agent_id=id,
                        session_id=session_id,
                        data={"error": f"{type(exc).__name__}: {exc}"},
                    )
                )
                if on_error:
                    try:
                        ret = on_error(exc)
                        if asyncio.iscoroutine(ret):
                            await ret
                    except Exception:
                        pass
            finally:
                self._active.pop(dispatch_id, None)
                self._dispatch_agent_ids.pop(dispatch_id, None)

        task = asyncio.create_task(_run())
        self._active[dispatch_id] = task
        self._dispatch_agent_ids[dispatch_id] = id
        return dispatch_id

    async def dispatch_and_wait(
        self,
        spec: AgentSpec,
        *,
        id: str,
        session: Optional[str] = None,
        input: Optional[DispatchInput] = None,
        text: Optional[str] = None,
        store: Optional[Store] = None,
        cancel_after: Optional[float] = None,
        tracer: Optional["Tracer"] = None,
    ) -> RunResult:
        """Like dispatch() but awaits completion and returns RunResult.

        Useful when you want the dispatch pattern (shared harness by id, event
        observers) but also need the result in the same async context.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[RunResult] = loop.create_future()

        def on_complete(result: RunResult) -> None:
            if not future.done():
                loop.call_soon(future.set_result, result)

        def on_error(exc: Exception) -> None:
            if not future.done():
                loop.call_soon(future.set_exception, exc)

        await self.dispatch(
            spec,
            id=id,
            session=session,
            input=input,
            text=text,
            store=store,
            on_complete=on_complete,
            on_error=on_error,
            cancel_after=cancel_after,
            tracer=tracer,
        )
        return await future

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Release all cached harnesses and cancel active tasks.

        After calling close(), the pool is reset to an empty state and can be
        reused or discarded.
        """
        # Cancel all active tasks
        for task in self._active.values():
            if not task.done():
                task.cancel()
        self._active.clear()
        self._dispatch_agent_ids.clear()
        self._session_locks.clear()
        self._default_harnesses.clear()
        self._observers.clear()


# ── Default pool instance ─────────────────────────────────────────────────────

_default_pool = DispatchPool()


# ── Module-level backward-compatible API ──────────────────────────────────────
# These delegate to the default pool instance for full backward compatibility.


def observe_dispatch(callback: Callable[[DispatchEvent], Any]) -> None:
    """Register a callback that receives every DispatchEvent.

    Callbacks are invoked synchronously (keep them lightweight — filter, log,
    enqueue). Exceptions in callbacks are silently swallowed so they never
    break a live agent run.
    """
    _default_pool.observe(callback)


def unobserve_dispatch(callback: Callable[[DispatchEvent], Any]) -> bool:
    """Unregister a previously registered observer. Returns True if found."""
    return _default_pool.unobserve(callback)


def cancel_dispatch(dispatch_id: str) -> bool:
    """Cancel an in-flight dispatched run by its dispatch_id.

    Returns True if the task was found and cancelled, False otherwise.
    """
    return _default_pool.cancel(dispatch_id)


def list_active_dispatches() -> list[str]:
    """Return dispatch_ids of currently running dispatched runs."""
    return _default_pool.list_active()


async def dispatch(
    spec: AgentSpec,
    *,
    id: str,
    session: Optional[str] = None,
    input: Optional[DispatchInput] = None,
    text: Optional[str] = None,
    store: Optional[Store] = None,
    on_complete: Optional[Callable[[RunResult], Any]] = None,
    on_error: Optional[Callable[[Exception], Any]] = None,
    cancel_after: Optional[float] = None,
    tracer: Optional["Tracer"] = None,
) -> str:
    """Deliver input to an agent session asynchronously (fire-and-observe).

    Creates a background task that runs the agent against the given input.
    Returns a ``dispatch_id`` immediately — the agent runs in the background.

    Args:
        spec: The AgentSpec to run.
        id: Instance identity (e.g. thread_id, user_id, repo_id). Agents with
            the same ``id`` share a Harness and accumulate session history.
        session: Session identity within the instance. Defaults to ``id``.
            Use separate session values to run parallel conversation branches
            for the same instance.
        input: Structured input. Pass either this or ``text``.
        text: Shorthand for ``DispatchInput(text=text)``.
        store: Backing store for session persistence. Shared across calls with
            the same ``id`` if you pass the same store instance.
        on_complete: Called with RunResult when the agent finishes.
        on_error: Called with the exception if the agent errors.
        cancel_after: Timeout in seconds for the background run.

    Returns:
        dispatch_id — use with ``cancel_dispatch()`` or observers.
    """
    return await _default_pool.dispatch(
        spec,
        id=id,
        session=session,
        input=input,
        text=text,
        store=store,
        on_complete=on_complete,
        on_error=on_error,
        cancel_after=cancel_after,
        tracer=tracer,
    )


async def dispatch_and_wait(
    spec: AgentSpec,
    *,
    id: str,
    session: Optional[str] = None,
    input: Optional[DispatchInput] = None,
    text: Optional[str] = None,
    store: Optional[Store] = None,
    cancel_after: Optional[float] = None,
    tracer: Optional["Tracer"] = None,
) -> RunResult:
    """Like dispatch() but awaits completion and returns RunResult.

    Useful when you want the dispatch pattern (shared harness by id, event
    observers) but also need the result in the same async context.
    """
    return await _default_pool.dispatch_and_wait(
        spec,
        id=id,
        session=session,
        input=input,
        text=text,
        store=store,
        cancel_after=cancel_after,
        tracer=tracer,
    )
