"""Webhook receiver — maps incoming HTTP events to Loop triggers or EventBus publishes.

Requires: pip install tvastar[serve]

Usage:
    from tvastar.serving.webhooks import create_webhook_router

    router = create_webhook_router(fleet)
    app.include_router(router, prefix="/webhooks")

    # Then POST /webhooks/trigger/{agent_name} triggers that agent's loop
    # POST /webhooks/event/{topic} publishes to EventBus
"""
from __future__ import annotations

from typing import Any


def create_webhook_router(fleet: Any) -> Any:
    """Create a FastAPI router for webhook-triggered agent operations.

    Requires fastapi to be installed (tvastar[serve] extra).
    """
    try:
        from fastapi import APIRouter, HTTPException, Request
    except ImportError:
        raise ImportError(
            "Webhook receiver requires the 'tvastar[serve]' extra. "
            "Install it with: pip install tvastar[serve]"
        ) from None

    router = APIRouter(tags=["webhooks"])

    @router.post("/trigger/{agent_name}")
    async def trigger_agent(agent_name: str, request: Request):
        """Trigger a registered agent's loop via webhook."""
        entry = fleet.registry.get(agent_name)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Agent {agent_name!r} not found")

        body: dict = {}
        try:
            body = await request.json()
        except Exception:
            pass

        loop = entry.loop
        if loop is None or not hasattr(loop, "trigger"):
            raise HTTPException(
                status_code=400, detail=f"Agent {agent_name!r} has no triggerable loop"
            )

        try:
            run = await loop.trigger(context={"webhook": True, **body})
            # Record outcome in observer for health tracking
            from tvastar.loop import LoopState
            is_error = run.state not in (LoopState.PASS, LoopState.VERIFYING)
            fleet.observer.record_outcome(is_error=is_error)
            return {"status": "triggered", "run_id": run.run_id, "agent": agent_name, "state": run.state.value}
        except RuntimeError as e:
            fleet.observer.record_outcome(is_error=True)
            raise HTTPException(status_code=409, detail=str(e))

    @router.post("/event/{topic}")
    async def publish_event(topic: str, request: Request):
        """Publish an event to the fleet EventBus via webhook."""
        body: dict = {}
        try:
            body = await request.json()
        except Exception:
            pass

        fleet.bus.publish(
            topic,
            body,
            source_agent="webhook",
        )
        return {"status": "published", "topic": topic}

    @router.get("/health")
    async def webhook_health():
        """Health check for the webhook receiver."""
        return {
            "status": "ok",
            "fleet": fleet.config.name,
            "agents": fleet.registry.count(),
        }

    return router
