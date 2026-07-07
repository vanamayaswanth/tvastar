"""Webhook handoff channel — POST a structured JSON payload to any URL."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..handoff import HandoffPolicy

if TYPE_CHECKING:
    from .. import LoopRun


@dataclass
class WebhookHandoff(HandoffPolicy):
    """POST a structured JSON handoff payload to an arbitrary URL.

    Requires ``httpx`` — install via ``pip install tvastar[http]``.
    """

    url: str
    headers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            import httpx  # noqa: F401

            self._httpx = httpx
        except ImportError:
            raise ImportError("Install tvastar[http] for Webhook handoff")

    async def escalate(self, run: "LoopRun", history: list["LoopRun"]) -> None:
        payload = {
            "loop_name": run.loop_name,
            "run_id": run.run_id,
            "state": run.state.value,
            "failure_kind": run.failure_kind.value if run.failure_kind else None,
            "error": (run.error or "")[:2000] if run.error else None,
            "duration_seconds": run.duration,
            "iteration": run.iteration,
            "timestamp_utc": datetime.fromtimestamp(run.started_at, tz=timezone.utc).isoformat(),
        }

        merged_headers = {"Content-Type": "application/json", **self.headers}

        try:
            async with self._httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(self.url, json=payload, headers=merged_headers)
        except self._httpx.TimeoutException:
            raise RuntimeError(f"Webhook POST to {self.url} timed out after 30s")

        if resp.status_code >= 300:
            body = resp.text[:500]
            raise RuntimeError(f"Webhook POST to {self.url} failed: {resp.status_code} — {body}")
