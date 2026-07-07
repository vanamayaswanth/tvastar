"""Slack handoff channel — post structured handoff report to a Slack channel."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..handoff import HandoffPolicy

if TYPE_CHECKING:
    from .. import LoopRun


@dataclass
class SlackHandoff(HandoffPolicy):
    """Post a structured handoff report to a Slack channel.

    Requires ``slack_sdk`` — install via ``pip install tvastar[slack]``.
    """

    channel: str
    token: str | None = None

    def __post_init__(self) -> None:
        try:
            import slack_sdk  # noqa: F401
        except ImportError:
            raise ImportError("Install tvastar[slack] for Slack handoff")
        if not self.token:
            self.token = os.environ.get("SLACK_BOT_TOKEN")

    async def escalate(self, run: "LoopRun", history: list["LoopRun"]) -> None:
        from slack_sdk import WebClient

        client = WebClient(token=self.token)

        failure_kind = (
            run.failure_kind.value if hasattr(run.failure_kind, "value") else str(run.failure_kind)
        )
        error_text = (run.error or "")[:500]
        duration_str = f"{run.duration:.1f}s" if run.duration is not None else "unknown"

        message = (
            f"ACTION REQUIRED\n"
            f"Loop: {run.loop_name}\n"
            f"Run ID: {run.run_id}\n"
            f"Failure: {failure_kind}\n"
            f"Error: {error_text}\n"
            f"Duration: {duration_str}"
        )

        response = await asyncio.to_thread(
            client.chat_postMessage, channel=self.channel, text=message
        )

        if not response.get("ok"):
            error_code = response.get("error", "unknown_error")
            raise RuntimeError(f"Slack API error: {error_code}")
