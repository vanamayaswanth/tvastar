"""Fleet alerting — deliver observer alerts to external channels.

Zero dependencies in core. Sends HTTP POST requests using urllib (stdlib).
For advanced integrations, users can register custom handlers.

Usage:
    from tvastar.fleet.alerting import SlackAlertHandler, WebhookAlertHandler

    fleet.bus.subscribe("fleet.alert.quality", SlackAlertHandler(webhook_url="..."))
    fleet.bus.subscribe("fleet.alert.error_rate", WebhookAlertHandler(url="..."))
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class SlackAlertHandler:
    """Sends fleet alerts to a Slack incoming webhook URL.

    Uses stdlib urllib — no external dependencies.
    """

    def __init__(self, webhook_url: str, *, channel: str | None = None) -> None:
        self._url = webhook_url
        self._channel = channel

    def __call__(self, event: Any) -> None:
        """Handle a FleetEvent by posting to Slack."""
        payload = (
            event.payload if isinstance(event.payload, dict) else {"message": str(event.payload)}
        )
        alert_type = payload.get("alert_type", event.topic)

        text = f"\U0001f6a8 *Fleet Alert: {alert_type}*\n"
        for key, value in payload.items():
            if key != "alert_type":
                text += f"\u2022 {key}: {value}\n"
        text += f"_Source: {event.source_agent}_"

        slack_payload: dict[str, Any] = {"text": text}
        if self._channel:
            slack_payload["channel"] = self._channel

        try:
            data = json.dumps(slack_payload).encode("utf-8")
            req = urllib.request.Request(
                self._url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception:
            pass  # alerting failure must never break fleet operations


class WebhookAlertHandler:
    """Sends fleet alerts to any HTTP endpoint via POST.

    Uses stdlib urllib — no external dependencies.
    """

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._url = url
        self._headers = headers or {}
        self._timeout = timeout

    def __call__(self, event: Any) -> None:
        """Handle a FleetEvent by POSTing JSON to the configured URL."""
        payload = {
            "topic": event.topic,
            "source_agent": event.source_agent,
            "timestamp": event.timestamp,
            "data": (
                event.payload
                if isinstance(event.payload, dict)
                else {"message": str(event.payload)}
            ),
        }
        if event.correlation_id:
            payload["correlation_id"] = event.correlation_id

        try:
            data = json.dumps(payload).encode("utf-8")
            headers = {"Content-Type": "application/json", **self._headers}
            req = urllib.request.Request(
                self._url,
                data=data,
                headers=headers,
                method="POST",
            )
            urllib.request.urlopen(req, timeout=self._timeout)
        except Exception:
            pass  # alerting failure must never break fleet operations


class LogAlertHandler:
    """Logs fleet alerts to stderr (useful for development/debugging)."""

    def __call__(self, event: Any) -> None:
        import sys

        payload = (
            event.payload if isinstance(event.payload, dict) else {"message": str(event.payload)}
        )
        alert_type = payload.get("alert_type", event.topic)
        print(f"[FLEET ALERT] {alert_type}: {payload}", file=sys.stderr)
