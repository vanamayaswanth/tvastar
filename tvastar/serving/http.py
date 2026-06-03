"""FastAPI HTTP + WebSocket server for an agent.

Endpoints:
    GET  /                      health + agent info
    GET  /sessions              list known session ids
    POST /sessions              create a session -> {session_id}
    POST /sessions/{id}/prompt  {"text": "..."} -> {"text", "usage", "steps"}
    WS   /sessions/{id}/stream  send {"text": "..."}, receive StreamEvent JSON

Requires ``tvastar[serve]``. Sessions live for the lifetime of the harness
(durable checkpoints persist across restarts when a FileStore is used).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Optional

from ..agent import AgentSpec
from ..harness import Harness
from ..memory.store import FileStore, Store


def create_app(spec: AgentSpec, *, store: Optional[Store] = None) -> Any:
    try:
        from fastapi import FastAPI, WebSocket, WebSocketDisconnect
        from pydantic import BaseModel
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("Serving needs: uv pip install 'tvastar[serve]'") from e

    harness = Harness(spec, store=store or FileStore(".tvastar-state"))
    app = FastAPI(title=f"Tvastar · {spec.name}")

    class PromptIn(BaseModel):
        text: str

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

    @app.websocket("/sessions/{sid}/stream")
    async def stream(ws: WebSocket, sid: str) -> None:
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

    return app


def serve(
    spec: AgentSpec,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    store: Optional[Store] = None,
) -> None:
    import uvicorn

    uvicorn.run(create_app(spec, store=store), host=host, port=port)
