"""Prometheus metrics endpoint for loop operational data."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..loop.metrics import MetricsCollector


def create_metrics_router(collector: "MetricsCollector"):
    """Create a FastAPI router that serves /metrics in Prometheus format."""
    from fastapi import APIRouter
    from fastapi.responses import PlainTextResponse

    router = APIRouter()

    @router.get("/metrics")
    async def metrics():
        return PlainTextResponse(
            collector.render(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return router
