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
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .agent import AgentSpec
from .harness import Harness
from .memory.store import InMemoryStore, Store
from .session import RunResult


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


# ── Global observer registry ─────────────────────────────────────────────────

_observers: list[Callable[[DispatchEvent], Any]] = []


def observe_dispatch(callback: Callable[[DispatchEvent], Any]) -> None:
    """Register a callback that receives every DispatchEvent.

    Callbacks are invoked synchronously (keep them lightweight — filter, log,
    enqueue). Exceptions in callbacks are silently swallowed so they never
    break a live agent run.
    """
    _observers.append(callback)


def _emit(event: DispatchEvent) -> None:
    for obs in _observers:
        try:
            obs(event)
        except Exception:
            pass


# ── Active dispatch registry ──────────────────────────────────────────────────

_active: dict[str, asyncio.Task] = {}  # dispatch_id -> asyncio.Task


def cancel_dispatch(dispatch_id: str) -> bool:
    """Cancel an in-flight dispatched run by its dispatch_id.

    Returns True if the task was found and cancelled, False otherwise.
    """
    task = _active.get(dispatch_id)
    if task and not task.done():
        task.cancel()
        return True
    return False


def list_active_dispatches() -> list[str]:
    """Return dispatch_ids of currently running dispatched runs."""
    return [did for did, t in _active.items() if not t.done()]


# ── Core dispatch() ───────────────────────────────────────────────────────────

_default_store = InMemoryStore()
_default_harnesses: dict[str, Harness] = {}  # agent_id -> Harness


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
    if input is None:
        input = DispatchInput(text=text or "")

    session_id = session or id
    dispatch_id = f"dispatch_{uuid.uuid4().hex[:12]}"

    # Reuse or create a harness per agent instance id
    shared_store = store or _default_store
    if id not in _default_harnesses:
        _default_harnesses[id] = Harness(spec, store=shared_store, durable=True)
    harness = _default_harnesses[id]

    _emit(
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
            coro = sess.prompt(prompt_text)
            if cancel_after is not None:
                result = await asyncio.wait_for(coro, timeout=cancel_after)
            else:
                result = await coro

            _emit(
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
            _emit(
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
            _active.pop(dispatch_id, None)

    task = asyncio.create_task(_run())
    _active[dispatch_id] = task
    return dispatch_id


async def dispatch_and_wait(
    spec: AgentSpec,
    *,
    id: str,
    session: Optional[str] = None,
    input: Optional[DispatchInput] = None,
    text: Optional[str] = None,
    store: Optional[Store] = None,
    cancel_after: Optional[float] = None,
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

    await dispatch(
        spec,
        id=id,
        session=session,
        input=input,
        text=text,
        store=store,
        on_complete=on_complete,
        on_error=on_error,
        cancel_after=cancel_after,
    )
    return await future
