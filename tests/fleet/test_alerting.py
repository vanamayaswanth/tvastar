"""Tests for fleet alerting handlers."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch


from tvastar.fleet.alerting import LogAlertHandler, SlackAlertHandler, WebhookAlertHandler


@dataclass
class FakeEvent:
    """Minimal FleetEvent stand-in for testing."""

    topic: str
    payload: Any
    source_agent: str
    timestamp: float = 1700000000.0
    correlation_id: str | None = None


class TestLogAlertHandler:
    """Tests for LogAlertHandler."""

    def test_prints_to_stderr(self, capsys):
        """LogAlertHandler prints alert info to stderr."""
        handler = LogAlertHandler()
        event = FakeEvent(
            topic="fleet.alert.quality",
            payload={"alert_type": "quality_drop", "score": 42},
            source_agent="test-agent",
        )
        handler(event)
        captured = capsys.readouterr()
        assert "quality_drop" in captured.err
        assert "score" in captured.err
        assert "42" in captured.err

    def test_handles_non_dict_payload(self, capsys):
        """LogAlertHandler handles string payloads gracefully."""
        handler = LogAlertHandler()
        event = FakeEvent(
            topic="fleet.alert.error",
            payload="something went wrong",
            source_agent="test-agent",
        )
        handler(event)
        captured = capsys.readouterr()
        assert "something went wrong" in captured.err


class TestSlackAlertHandler:
    """Tests for SlackAlertHandler."""

    @patch("urllib.request.urlopen")
    def test_constructs_correct_payload(self, mock_urlopen):
        """SlackAlertHandler sends correct JSON to Slack webhook."""
        handler = SlackAlertHandler(
            webhook_url="https://hooks.slack.com/services/T/B/X",
            channel="#alerts",
        )
        event = FakeEvent(
            topic="fleet.alert.quality",
            payload={"alert_type": "quality_drop", "agent": "researcher", "score": 35},
            source_agent="observer",
        )
        handler(event)

        # Verify urlopen was called
        mock_urlopen.assert_called_once()
        call_args = mock_urlopen.call_args
        req = call_args[0][0]

        assert req.full_url == "https://hooks.slack.com/services/T/B/X"
        assert req.method == "POST"
        assert req.get_header("Content-type") == "application/json"

        body = json.loads(req.data.decode("utf-8"))
        assert "text" in body
        assert "quality_drop" in body["text"]
        assert body["channel"] == "#alerts"

    @patch("urllib.request.urlopen")
    def test_no_channel_omits_field(self, mock_urlopen):
        """SlackAlertHandler without channel omits channel from payload."""
        handler = SlackAlertHandler(webhook_url="https://hooks.slack.com/services/T/B/X")
        event = FakeEvent(
            topic="fleet.alert.error",
            payload={"alert_type": "error_rate", "rate": 0.8},
            source_agent="observer",
        )
        handler(event)

        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data.decode("utf-8"))
        assert "channel" not in body

    @patch("urllib.request.urlopen", side_effect=Exception("network error"))
    def test_swallows_exceptions(self, mock_urlopen):
        """SlackAlertHandler never raises — alerting failure must not break fleet."""
        handler = SlackAlertHandler(webhook_url="https://hooks.slack.com/services/T/B/X")
        event = FakeEvent(
            topic="fleet.alert.error",
            payload={"alert_type": "error"},
            source_agent="observer",
        )
        # Should not raise
        handler(event)


class TestWebhookAlertHandler:
    """Tests for WebhookAlertHandler."""

    @patch("urllib.request.urlopen")
    def test_constructs_correct_payload(self, mock_urlopen):
        """WebhookAlertHandler sends structured JSON with event data."""
        handler = WebhookAlertHandler(
            url="https://alerting.example.com/hook",
            headers={"X-Api-Key": "secret123"},
            timeout=5.0,
        )
        event = FakeEvent(
            topic="fleet.alert.error_rate",
            payload={"rate": 0.75, "window": "1h"},
            source_agent="observer",
            correlation_id="corr-123",
        )
        handler(event)

        mock_urlopen.assert_called_once()
        call_args = mock_urlopen.call_args
        req = call_args[0][0]

        assert req.full_url == "https://alerting.example.com/hook"
        assert req.method == "POST"
        assert req.get_header("Content-type") == "application/json"
        assert req.get_header("X-api-key") == "secret123"

        body = json.loads(req.data.decode("utf-8"))
        assert body["topic"] == "fleet.alert.error_rate"
        assert body["source_agent"] == "observer"
        assert body["timestamp"] == 1700000000.0
        assert body["data"] == {"rate": 0.75, "window": "1h"}
        assert body["correlation_id"] == "corr-123"

        # Verify timeout
        assert call_args[1]["timeout"] == 5.0

    @patch("urllib.request.urlopen")
    def test_no_correlation_id_omits_field(self, mock_urlopen):
        """WebhookAlertHandler omits correlation_id when None."""
        handler = WebhookAlertHandler(url="https://example.com/hook")
        event = FakeEvent(
            topic="fleet.alert.test",
            payload={"msg": "hello"},
            source_agent="test",
            correlation_id=None,
        )
        handler(event)

        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data.decode("utf-8"))
        assert "correlation_id" not in body

    @patch("urllib.request.urlopen", side_effect=Exception("connection refused"))
    def test_swallows_exceptions(self, mock_urlopen):
        """WebhookAlertHandler never raises — alerting failure must not break fleet."""
        handler = WebhookAlertHandler(url="https://example.com/hook")
        event = FakeEvent(
            topic="fleet.alert.error",
            payload={"error": "test"},
            source_agent="observer",
        )
        # Should not raise
        handler(event)

    @patch("urllib.request.urlopen")
    def test_non_dict_payload_wrapped(self, mock_urlopen):
        """WebhookAlertHandler wraps non-dict payloads in a message dict."""
        handler = WebhookAlertHandler(url="https://example.com/hook")
        event = FakeEvent(
            topic="fleet.alert.test",
            payload="plain string alert",
            source_agent="test",
        )
        handler(event)

        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data.decode("utf-8"))
        assert body["data"] == {"message": "plain string alert"}
