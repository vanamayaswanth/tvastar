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

Requires ``tvastar[serve]``.  Sessions live for the lifetime of the harness
(durable checkpoints persist across restarts when a FileStore is used).
"""

import json
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

from ..agent import AgentSpec
from ..harness import Harness
from ..memory.store import FileStore, Store

if TYPE_CHECKING:
    from ..loop.metrics import MetricsCollector
    from ..loop.registry import LoopRegistry


def create_app(
    spec: "AgentSpec",
    *,
    store: Optional["Store"] = None,
    registry: Optional["LoopRegistry"] = None,
    metrics_collector: Optional["MetricsCollector"] = None,
    webhook_secrets: Optional[dict] = None,
) -> Any:
    try:
        from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
        from fastapi.responses import StreamingResponse
    except ImportError as e:
        raise RuntimeError("Serving needs: uv pip install 'tvastar[serve]'") from e

    from pydantic import BaseModel

    harness = Harness(spec, store=store or FileStore(".tvastar-state"))
    app = FastAPI(title=f"Tvastar · {spec.name}")

    class PromptIn(BaseModel):
        text: str

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
    def list_sessions() -> dict:
        return {"sessions": harness.list_sessions()}

    @app.post("/sessions")
    def new_session() -> dict:
        return {"session_id": harness.session().id}

    # ── Non-streaming prompt ──────────────────────────────────────────────

    @app.post("/sessions/{sid}/prompt")
    async def prompt(sid: str, body: PromptIn) -> dict:
        sess = harness.resume(sid) or harness.session(session_id=sid)
        async with sess:
            result = await sess.prompt(body.text)
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
        await ws.accept()
        sess = harness.resume(sid) or harness.session(session_id=sid)
        await sess.start()
        try:
            while True:
                data = await ws.receive_json()
                async for ev in sess.stream(data.get("text", "")):
                    await ws.send_json({"type": ev.type, "data": ev.data})
                await ws.send_json({"type": "done", "data": {}})
        except WebSocketDisconnect:
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
        sess = harness.resume(sid) or harness.session(session_id=sid)

        async def _event_generator() -> AsyncIterator[str]:
            await sess.start()
            try:
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
) -> None:
    import uvicorn

    uvicorn.run(
        create_app(
            spec,
            store=store,
            registry=registry,
            metrics_collector=metrics_collector,
            webhook_secrets=webhook_secrets,
        ),
        host=host,
        port=port,
    )
