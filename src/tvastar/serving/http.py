"""FastAPI HTTP + WebSocket + SSE server for an agent.

Endpoints:
    GET  /                          health + agent info
    GET  /sessions                  list known session ids
    POST /sessions                  create a session -> {session_id}
    POST /sessions/{id}/prompt      {"text": "..."} -> {"text", "usage", "steps"}
    WS   /sessions/{id}/stream      send {"text": "..."}, receive StreamEvent JSON
    GET  /sessions/{id}/stream      SSE — ?text=... -> text/event-stream

The SSE endpoint (GET /sessions/{id}/stream) lets browser clients and CLI tools
stream agent responses without a WebSocket library. Each StreamEvent is emitted
as a ``data: <json>`` SSE line. The stream ends with ``data: [DONE]``.

Requires ``tvastar[serve]``. Sessions live for the lifetime of the harness
(durable checkpoints persist across restarts when a FileStore is used).
"""

import asyncio
import inspect
import json
import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable, Optional

from ..agent import AgentSpec
from ..harness import Harness
from ..memory.store import FileStore, Store

if TYPE_CHECKING:
    from ..loop.metrics import MetricsCollector
    from ..loop.registry import LoopRegistry


@dataclass(frozen=True)
class Principal:
    """Authenticated caller identity used to scope HTTP serving sessions."""

    tenant_id: str
    subject_id: str


Authenticator = Callable[[Any], Principal | Awaitable[Principal]]


class _AuthenticationFailed(Exception):
    pass


class _SessionUnavailable(Exception):
    pass


class _RunLimitReached(Exception):
    pass


def create_app(
    spec: "AgentSpec",
    *,
    store: Optional["Store"] = None,
    registry: Optional["LoopRegistry"] = None,
    metrics_collector: Optional["MetricsCollector"] = None,
    webhook_secrets: Optional[dict] = None,
    authenticator: Optional[Authenticator] = None,
    max_prompt_size: Optional[int] = None,
    max_active_runs: Optional[int] = None,
) -> Any:
    """Create the optional FastAPI serving application.

    When *authenticator* is supplied it receives each session request or
    websocket and must return a :class:`Principal` (synchronously or
    asynchronously). Sessions created through the API persist that principal's
    tenant ownership; unknown and foreign sessions are indistinguishable.
    Prompt size is measured in characters.
    """
    if max_prompt_size is not None and max_prompt_size < 0:
        raise ValueError("max_prompt_size must be non-negative")
    if max_active_runs is not None and max_active_runs < 1:
        raise ValueError("max_active_runs must be at least 1")

    try:
        from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
        from fastapi.responses import StreamingResponse
    except ImportError as e:
        raise RuntimeError("Serving needs: uv pip install 'tvastar[serve]'") from e

    from pydantic import BaseModel

    harness = Harness(spec, store=store or FileStore(".tvastar-state"))
    app = FastAPI(title=f"Tvastar · {spec.name}")
    run_slots = asyncio.BoundedSemaphore(max_active_runs) if max_active_runs is not None else None
    session_locks: dict[str, asyncio.Lock] = {}

    class PromptIn(BaseModel):
        text: str

    async def _authenticate(connection: Any) -> Optional[Principal]:
        if authenticator is None:
            return None
        try:
            principal = authenticator(connection)
            if inspect.isawaitable(principal):
                principal = await principal
        except Exception as exc:
            raise _AuthenticationFailed from exc
        if (
            not isinstance(principal, Principal)
            or not principal.tenant_id
            or not principal.subject_id
        ):
            raise _AuthenticationFailed
        return principal

    def _owner_key(sid: str) -> str:
        return f"serving_owner:{sid}"

    def _owner(sid: str) -> Optional[Principal]:
        value = harness.store.get(_owner_key(sid))
        if not isinstance(value, dict):
            meta = harness.store.get(f"session_meta:{sid}")
            value = meta.get("serving_owner") if isinstance(meta, dict) else None
        if not isinstance(value, dict):
            return None
        tenant_id = value.get("tenant_id")
        subject_id = value.get("subject_id")
        if not isinstance(tenant_id, str) or not isinstance(subject_id, str):
            return None
        if not tenant_id or not subject_id:
            return None
        return Principal(tenant_id=tenant_id, subject_id=subject_id)

    def _persist_session(sid: str, principal: Optional[Principal]) -> None:
        owner = (
            None
            if principal is None
            else {"tenant_id": principal.tenant_id, "subject_id": principal.subject_id}
        )
        meta: dict[str, Any] = {"id": sid, "last_activity": time.time()}
        if owner is not None:
            meta["serving_owner"] = owner
        harness.store.set(f"session_meta:{sid}", meta)
        if owner is not None:
            harness.store.set(_owner_key(sid), owner)

    def _session_is_known(sid: str) -> bool:
        return any(info.id == sid for info in harness.list_sessions())

    def _get_session(sid: str, principal: Optional[Principal]) -> Any:
        if principal is not None:
            owner = _owner(sid)
            if (
                owner is None
                or owner.tenant_id != principal.tenant_id
                or owner.subject_id != principal.subject_id
                or not _session_is_known(sid)
            ):
                raise _SessionUnavailable
        return harness.resume(sid) or harness.session(session_id=sid)

    def _check_prompt_size(text: str) -> None:
        if max_prompt_size is not None and len(text) > max_prompt_size:
            raise HTTPException(status_code=413, detail="Prompt exceeds the configured size limit")

    async def _acquire_run(sid: str) -> asyncio.Lock:
        # ponytail: locks remain for app lifetime; replace with bounded LRU locks if untrusted IDs grow large.
        lock = session_locks.setdefault(sid, asyncio.Lock())
        await lock.acquire()
        try:
            if run_slots is not None:
                if run_slots.locked():
                    raise _RunLimitReached
                await run_slots.acquire()
        except BaseException:
            lock.release()
            raise
        return lock

    def _release_run(lock: asyncio.Lock) -> None:
        if run_slots is not None:
            run_slots.release()
        lock.release()

    # ── Info / session management ─────────────────────────────────────────

    @app.get("/")
    def root() -> dict:
        return {
            "framework": "tvastar",
            "agent": spec.name,
            "model": spec.model.name,
            "tools": spec.tools.names(),
            "skills": spec.skills.names(),
        }

    @app.get("/sessions")
    async def list_sessions(request: Request) -> dict:
        try:
            principal = await _authenticate(request)
        except _AuthenticationFailed:
            raise HTTPException(status_code=401, detail="Unauthorized") from None
        sessions = harness.list_sessions()
        if principal is not None:
            sessions = [
                info
                for info in sessions
                if (owner := _owner(info.id)) is not None
                and owner.tenant_id == principal.tenant_id
                and owner.subject_id == principal.subject_id
            ]
        return {"sessions": sessions}

    @app.post("/sessions")
    async def new_session(request: Request) -> dict:
        try:
            principal = await _authenticate(request)
        except _AuthenticationFailed:
            raise HTTPException(status_code=401, detail="Unauthorized") from None
        session = harness.session()
        _persist_session(session.id, principal)
        return {"session_id": session.id}

    # ── Non-streaming prompt ──────────────────────────────────────────────

    @app.post("/sessions/{sid}/prompt")
    async def prompt(sid: str, body: PromptIn, request: Request) -> dict:
        try:
            principal = await _authenticate(request)
            sess = _get_session(sid, principal)
        except _AuthenticationFailed:
            raise HTTPException(status_code=401, detail="Unauthorized") from None
        except _SessionUnavailable:
            raise HTTPException(status_code=404, detail="Not found") from None
        _check_prompt_size(body.text)
        try:
            lock = await _acquire_run(sid)
        except _RunLimitReached:
            raise HTTPException(status_code=429, detail="Too many active runs") from None
        try:
            async with sess:
                result = await sess.prompt(body.text)
        finally:
            _release_run(lock)
        return {
            "session_id": sid,
            "text": result.text,
            "steps": result.steps,
            "stopped": result.stopped,
            "usage": asdict(result.usage),
        }

    # ── WebSocket streaming ───────────────────────────────────────────────

    @app.websocket("/sessions/{sid}/stream")
    async def ws_stream(ws: WebSocket, sid: str) -> None:
        try:
            principal = await _authenticate(ws)
            sess = _get_session(sid, principal)
        except (_AuthenticationFailed, _SessionUnavailable):
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            while True:
                data = await ws.receive_json()
                if not isinstance(data, dict) or not isinstance(data.get("text", ""), str):
                    await ws.close(code=1008)
                    return
                text = data.get("text", "")
                if max_prompt_size is not None and len(text) > max_prompt_size:
                    await ws.close(code=1009)
                    return
                try:
                    lock = await _acquire_run(sid)
                except _RunLimitReached:
                    await ws.close(code=1013)
                    return
                try:
                    await sess.start()
                    async for ev in sess.stream(text):
                        await ws.send_json({"type": ev.type, "data": ev.data})
                    await ws.send_json({"type": "done", "data": {}})
                finally:
                    _release_run(lock)
        except WebSocketDisconnect:
            pass
        finally:
            await sess.close()

    # ── SSE streaming ────────────────────────────────────────────────────

    @app.get("/sessions/{sid}/stream")
    async def sse_stream(sid: str, request: Request, text: str = "") -> StreamingResponse:
        """Server-Sent Events endpoint.

        Usage::

            curl -N 'http://localhost:8000/sessions/sess_abc/stream?text=Hello'

        Each event is a JSON-encoded StreamEvent::

            data: {"type": "text_delta", "data": {"text": "Hello "}}
            data: {"type": "turn_end", "data": {"text": "Hello world"}}
            data: [DONE]
        """
        try:
            principal = await _authenticate(request)
            sess = _get_session(sid, principal)
        except _AuthenticationFailed:
            raise HTTPException(status_code=401, detail="Unauthorized") from None
        except _SessionUnavailable:
            raise HTTPException(status_code=404, detail="Not found") from None
        _check_prompt_size(text)
        try:
            lock = await _acquire_run(sid)
        except _RunLimitReached:
            raise HTTPException(status_code=429, detail="Too many active runs") from None

        async def _event_generator() -> AsyncIterator[str]:
            try:
                await sess.start()
                async for ev in sess.stream(text):
                    if await request.is_disconnected():
                        break
                    payload = json.dumps({"type": ev.type, "data": ev.data})
                    yield f"data: {payload}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as exc:
                error = json.dumps({"type": "error", "data": {"message": str(exc)}})
                yield f"data: {error}\n\n"
                yield "data: [DONE]\n\n"
            finally:
                try:
                    await sess.close()
                finally:
                    _release_run(lock)

        return StreamingResponse(
            _event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # nginx: disable response buffering
                "Connection": "keep-alive",
            },
        )

    # Wire loop infrastructure endpoints if registry is provided
    if registry is not None:
        from .health import create_health_router
        from .loop_webhooks import create_loop_webhook_router

        app.include_router(create_health_router(registry))
        app.include_router(create_loop_webhook_router(registry, secrets=webhook_secrets))

        if metrics_collector is not None:
            from .metrics import create_metrics_router

            app.include_router(create_metrics_router(metrics_collector))

    return app


def serve(
    spec: AgentSpec,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    store: Optional[Store] = None,
    registry: Optional["LoopRegistry"] = None,
    metrics_collector: Optional["MetricsCollector"] = None,
    webhook_secrets: Optional[dict] = None,
    authenticator: Optional[Authenticator] = None,
    max_prompt_size: Optional[int] = None,
    max_active_runs: Optional[int] = None,
) -> None:
    """Run the HTTP server with the same optional controls as :func:`create_app`."""
    import uvicorn

    uvicorn.run(
        create_app(
            spec,
            store=store,
            registry=registry,
            metrics_collector=metrics_collector,
            webhook_secrets=webhook_secrets,
            authenticator=authenticator,
            max_prompt_size=max_prompt_size,
            max_active_runs=max_active_runs,
        ),
        host=host,
        port=port,
    )
