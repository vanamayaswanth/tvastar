"""Harness — the configured handle that manages models, tools, sandboxes,
sessions, memory, durability, and observability.

This is the top-level object users hold. ``Agent = Model + Harness``: you pass
an AgentSpec, the harness runs it across one or more Sessions.
"""

from __future__ import annotations

from typing import Optional

from .agent import AgentSpec
from .durable import Checkpointer
from .memory.store import InMemoryStore, Store
from .observability import NULL_TRACER, Tracer
from .session import RunResult, Session


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

    # ---- sessions -------------------------------------------------------

    def session(
        self,
        *,
        spec: Optional[AgentSpec] = None,
        session_id: Optional[str] = None,
    ) -> Session:
        """Create (or fetch by id) a session bound to this harness."""
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        s = Session(spec=spec or self.spec, harness=self)
        if session_id:
            s.id = session_id
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
        # sandbox must exist before restore can replay an fs snapshot
        s.sandbox = s.spec.sandbox_factory()
        s._started = True
        s.restore(record)
        return s

    def list_sessions(self) -> list[str]:
        if not self.checkpointer:
            return list(self._sessions)
        return sorted(set(self._sessions) | set(self.checkpointer.list_sessions()))

    # ---- convenience ----------------------------------------------------

    async def run(self, prompt: str, *, session_id: Optional[str] = None) -> RunResult:
        """One-shot: open a session, run a prompt, close it."""
        s = self.session(session_id=session_id)
        async with s:
            return await s.prompt(prompt)
