"""Health check endpoint for loop operational status.

GET /health → 200 with {status: "healthy", loops: {...}}
           → 503 with unhealthy details when any loop is unhealthy.

A loop is unhealthy if:
  - State is SUSPENDED, OR
  - Has a non-manual schedule and no successful run in 3× the scheduled interval.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..loop.registry import LoopRegistry


def create_health_router(registry: "LoopRegistry"):
    """Create a FastAPI router with GET /health endpoint."""
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse

    router = APIRouter()

    @router.get("/health")
    async def health():
        loops = registry.all()
        if not loops:
            return JSONResponse({"status": "healthy", "loops": {}}, status_code=200)

        loop_statuses = {}
        any_unhealthy = False

        for name, loop in loops.items():
            status = _check_loop_health(loop)
            loop_statuses[name] = status
            if status["status"] == "unhealthy":
                any_unhealthy = True

        overall = "unhealthy" if any_unhealthy else "healthy"
        code = 503 if any_unhealthy else 200
        return JSONResponse({"status": overall, "loops": loop_statuses}, status_code=code)

    return router


def _check_loop_health(loop) -> dict:
    """Classify a single loop as healthy or unhealthy."""
    from ..loop import LoopState

    # SUSPENDED → unhealthy
    if loop.state == LoopState.SUSPENDED:
        return {"status": "unhealthy", "reason": "suspended"}

    # Manual loops → always healthy
    if loop.config.schedule == "@manual":
        return {"status": "healthy"}

    # Non-manual: check last success vs 3× interval
    interval = _schedule_interval(loop.config.schedule)
    if interval is None:
        return {"status": "healthy"}  # ponytail: can't compute → assume healthy

    threshold = 3 * interval
    history = loop.history(limit=50)

    # Find last successful run
    last_success_time: float | None = None
    for run in reversed(history):
        if run.state == LoopState.PASS:
            last_success_time = run.ended_at or run.started_at
            break

    if last_success_time is None:
        # No successful run ever — check if loop has existed long enough
        if not history:
            return {"status": "healthy"}
        oldest = history[0]
        if time.time() - oldest.started_at > threshold:
            return {"status": "unhealthy", "reason": f"no success in {threshold:.0f}s"}
        return {"status": "healthy"}

    elapsed = time.time() - last_success_time
    if elapsed > threshold:
        return {"status": "unhealthy", "reason": f"no success in {threshold:.0f}s"}

    return {"status": "healthy"}


def _schedule_interval(schedule: str) -> float | None:
    """Compute the interval in seconds between two consecutive scheduled runs."""
    if schedule == "@hourly":
        return 3600.0
    elif schedule == "@daily":
        return 86400.0
    elif schedule == "@weekly":
        return 604800.0
    else:
        # Cron expression — compute via next_run_time
        try:
            from ..loop.schedule import next_run_time

            now = datetime.now(tz=timezone.utc)
            first = next_run_time(schedule, now)
            second = next_run_time(schedule, first)
            return (second - first).total_seconds()
        except Exception:
            return None
