"""Tests for tvastar.loop.channels.slack — SlackHandoff channel.

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from enum import Enum
from unittest.mock import MagicMock, patch

import pytest


# --- Minimal fakes for LoopRun ---


class FailureKind(Enum):
    AGENT_ERROR = "agent_error"
    TIMEOUT = "timeout"


class LoopState(Enum):
    FAIL = "fail"


@dataclass
class FakeLoopRun:
    run_id: str = "run_abc123"
    loop_name: str = "ci-sweeper"
    state: LoopState = LoopState.FAIL
    iteration: int = 3
    started_at: float = 1000.0
    ended_at: float | None = 1045.7
    failure_kind: FailureKind | None = FailureKind.AGENT_ERROR
    error: str | None = "Something went wrong in the agent"
    context: dict = field(default_factory=dict)

    @property
    def duration(self) -> float | None:
        if self.ended_at is None:
            return None
        return self.ended_at - self.started_at


# --- Tests ---


class TestSlackHandoffImportError:
    """Requirement 4.3: ImportError at construction when slack_sdk missing."""

    def test_raises_import_error_without_slack_sdk(self):
        with patch.dict(sys.modules, {"slack_sdk": None}):
            # Force reimport
            import importlib
            import tvastar.loop.channels.slack as mod

            importlib.reload(mod)
            with pytest.raises(ImportError, match="Install tvastar\\[slack\\] for Slack handoff"):
                mod.SlackHandoff(channel="#alerts")


class TestSlackHandoffAuth:
    """Requirement 4.5: Auth via constructor token or SLACK_BOT_TOKEN env var."""

    def test_uses_constructor_token(self):
        mock_sdk = MagicMock()
        with patch.dict(sys.modules, {"slack_sdk": mock_sdk}):
            from tvastar.loop.channels.slack import SlackHandoff

            handoff = SlackHandoff(channel="#alerts", token="xoxb-constructor")
            assert handoff.token == "xoxb-constructor"

    def test_falls_back_to_env_var(self, monkeypatch):
        mock_sdk = MagicMock()
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-env")
        with patch.dict(sys.modules, {"slack_sdk": mock_sdk}):
            from tvastar.loop.channels.slack import SlackHandoff

            handoff = SlackHandoff(channel="#alerts")
            assert handoff.token == "xoxb-env"


class TestSlackHandoffEscalate:
    """Requirement 4.1, 4.2, 4.4: escalate posts structured message, raises on failure."""

    @pytest.fixture
    def mock_webclient(self):
        mock_sdk = MagicMock()
        mock_client_instance = MagicMock()
        mock_sdk.WebClient.return_value = mock_client_instance
        return mock_sdk, mock_client_instance

    def test_posts_structured_message(self, mock_webclient):
        mock_sdk, mock_client = mock_webclient
        mock_client.chat_postMessage.return_value = {"ok": True}

        with patch.dict(sys.modules, {"slack_sdk": mock_sdk}):
            from tvastar.loop.channels.slack import SlackHandoff

            handoff = SlackHandoff(channel="#alerts", token="xoxb-test")
            run = FakeLoopRun()

            asyncio.run(handoff.escalate(run, []))

            mock_client.chat_postMessage.assert_called_once()
            call_kwargs = mock_client.chat_postMessage.call_args[1]
            assert call_kwargs["channel"] == "#alerts"

            msg = call_kwargs["text"]
            assert "ci-sweeper" in msg
            assert "run_abc123" in msg
            assert "agent_error" in msg
            assert "Something went wrong" in msg
            assert "45.7s" in msg
            assert "ACTION REQUIRED" in msg

    def test_truncates_error_to_500_chars(self, mock_webclient):
        mock_sdk, mock_client = mock_webclient
        mock_client.chat_postMessage.return_value = {"ok": True}

        with patch.dict(sys.modules, {"slack_sdk": mock_sdk}):
            from tvastar.loop.channels.slack import SlackHandoff

            handoff = SlackHandoff(channel="#alerts", token="xoxb-test")
            run = FakeLoopRun(error="x" * 1000)

            asyncio.run(handoff.escalate(run, []))

            msg = mock_client.chat_postMessage.call_args[1]["text"]
            # The error in the message should be at most 500 chars
            # Find the error line
            for line in msg.split("\n"):
                if line.startswith("Error:"):
                    error_content = line[len("Error: ") :]
                    assert len(error_content) <= 500
                    break

    def test_handles_none_duration(self, mock_webclient):
        mock_sdk, mock_client = mock_webclient
        mock_client.chat_postMessage.return_value = {"ok": True}

        with patch.dict(sys.modules, {"slack_sdk": mock_sdk}):
            from tvastar.loop.channels.slack import SlackHandoff

            handoff = SlackHandoff(channel="#alerts", token="xoxb-test")
            run = FakeLoopRun(ended_at=None)

            asyncio.run(handoff.escalate(run, []))

            msg = mock_client.chat_postMessage.call_args[1]["text"]
            assert "unknown" in msg

    def test_raises_on_api_failure(self, mock_webclient):
        mock_sdk, mock_client = mock_webclient
        mock_client.chat_postMessage.return_value = {"ok": False, "error": "channel_not_found"}

        with patch.dict(sys.modules, {"slack_sdk": mock_sdk}):
            from tvastar.loop.channels.slack import SlackHandoff

            handoff = SlackHandoff(channel="#nonexistent", token="xoxb-test")
            run = FakeLoopRun()

            with pytest.raises(RuntimeError, match="channel_not_found"):
                asyncio.run(handoff.escalate(run, []))
