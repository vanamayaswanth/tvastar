"""Graceful shutdown for loop processes.

Installs signal handlers (SIGTERM + SIGINT) that drain running loops
before exiting. On timeout, force-cancels remaining tasks and logs
which loops were interrupted.

Requirements validated: 12.1, 12.2, 12.3, 12.4, 12.5
"""

from __future__ import annotations

import asyncio
import signal
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .registry import LoopRegistry

# States that are safe stopping points — no in-flight work to lose.
_CHECKPOINTABLE = {"idle", "pass", "fail"}


def install_signal_handlers(registry: "LoopRegistry", drain_timeout: float = 30.0) -> None:
    """Register SIGTERM + SIGINT handlers for graceful loop drain.

    On signal:
      1. Call stop() on all registered loops (stops accepting triggers).
      2. Wait up to *drain_timeout* seconds for RUNNING/VERIFYING loops
         to reach a checkpointable state (IDLE, PASS, FAIL).
      3. On timeout: cancel remaining asyncio tasks, log force-cancelled
         loops (name + run_id) to stderr.

    On Windows, only SIGINT is registered (SIGTERM is unsupported via
    asyncio signal handlers). No error is raised.
    """
    loop = asyncio.get_event_loop()

    async def _shutdown() -> None:
        import sys as _sys

        # 1. Stop all loops — stops schedulers and pending retries
        loops = registry.all()
        for lp in loops.values():
            await lp.stop()

        # 2. Wait for running loops to reach a checkpointable state
        deadline = asyncio.get_event_loop().time() + drain_timeout
        while True:
            still_running = [lp for lp in loops.values() if lp.state.value not in _CHECKPOINTABLE]
            if not still_running:
                break
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                # 3. Timeout: force cancel and log
                for lp in still_running:
                    last = lp.last_run()
                    run_id = last.run_id if last else "unknown"
                    print(
                        f"shutdown: force-cancelled loop={lp.name} run_id={run_id}",
                        file=_sys.stderr,
                    )
                # Cancel all remaining asyncio tasks (except ourselves)
                current = asyncio.current_task()
                for task in asyncio.all_tasks():
                    if task is not current and not task.done():
                        task.cancel()
                break
            await asyncio.sleep(min(0.1, remaining))

    def _handler() -> None:
        asyncio.ensure_future(_shutdown())

    # Register signals.
    # On Windows, ProactorEventLoop doesn't support add_signal_handler at all.
    # Use signal.signal() as fallback — it schedules the async shutdown via
    # call_soon_threadsafe since signal handlers run on the main thread.
    if sys.platform == "win32":

        def _win_handler(signum: int, frame: object) -> None:
            loop.call_soon_threadsafe(_handler)

        signal.signal(signal.SIGINT, _win_handler)
    else:
        loop.add_signal_handler(signal.SIGTERM, _handler)
        loop.add_signal_handler(signal.SIGINT, _handler)
