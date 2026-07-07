"""PagerDuty handoff channel — fire a PagerDuty incident via Events API v2."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..handoff import HandoffPolicy

if TYPE_CHECKING:
    from .. import LoopRun


_FAILED_STATES = frozenset({"fail", "handoff", "handoff_failed"})


@dataclass
class PagerDutyHandoff(HandoffPolicy):
    """Create a PagerDuty incident when a loop exhausts retries.

    Requires ``httpx`` — install via ``pip install tvastar[pagerduty]``.
    """

    routing_key: str | None = None

    def __post_init__(self) -> None:
        try:
            import httpx  # noqa: F401
        except ImportError:
            raise ImportError("Install tvastar[pagerduty] for PagerDuty handoff")
        if not self.routing_key:
            self.routing_key = os.environ.get("PAGERDUTY_ROUTING_KEY")

    async def escalate(self, run: "LoopRun", history: list["LoopRun"]) -> None:
        import httpx

        # Count consecutive failures at the tail of history
        consecutive = 0
        for past_run in reversed(history):
            if past_run.state.value in _FAILED_STATES:
                consecutive += 1
            else:
                break

        severity = "critical" if consecutive >= 3 else "warning"

        summary = f"{run.failure_kind.value if run.failure_kind else 'unknown'}: {run.error or ''}"
        summary = summary[:1024]

        payload = {
            "routing_key": self.routing_key,
            "event_action": "trigger",
            "dedup_key": run.run_id,
            "payload": {
                "summary": summary,
                "source": run.loop_name,
                "severity": severity,
            },
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://events.pagerduty.com/v2/enqueue", json=payload
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"PagerDuty API returned HTTP {resp.status_code}"
                )
