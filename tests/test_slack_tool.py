"""Unit tests for the Slack integration tool (src/tvastar/tools/slack.py)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from tvastar.errors import ToolError
from tvastar.tools.base import Tool, ToolContext
from tvastar.tools.slack import (
    _get_token,
    slack_post_message,
    slack_post_thread,
    slack_read_messages,
    slack_toolset,
)


# ── Factory tests ─────────────────────────────────────────────────────────────


def test_slack_toolset_returns_list_of_tools():
    tools = slack_toolset()
    assert isinstance(tools, list)
    assert len(tools) == 3
    assert all(isinstance(t, Tool) for t in tools)
    names = {t.name for t in tools}
    assert names == {"slack_post_message", "slack_post_thread", "slack_read_messages"}


# ── Auth resolution tests ─────────────────────────────────────────────────────


def test_get_token_from_context_memory():
    ctx = ToolContext(memory={"slack_token": "xoxb-from-ctx"})
    assert _get_token(ctx) == "xoxb-from-ctx"


def test_get_token_from_env(monkeypatch):
    ctx = ToolContext()
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-from-env")
    assert _get_token(ctx) == "xoxb-from-env"


def test_get_token_context_takes_priority_over_env(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-from-env")
    ctx = ToolContext(memory={"slack_token": "xoxb-from-ctx"})
    assert _get_token(ctx) == "xoxb-from-ctx"


def test_get_token_raises_tool_error_when_missing(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    ctx = ToolContext()
    with pytest.raises(ToolError, match="No Slack token configured"):
        _get_token(ctx)


# ── ImportError tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_import_error_when_slack_sdk_missing(monkeypatch):
    """When slack_sdk is missing, invoke wraps the ImportError in a ToolError."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    # Hide slack_sdk from the import system
    with patch.dict(sys.modules, {"slack_sdk": None}):
        monkeypatch.delitem(sys.modules, "slack_sdk", raising=False)
        with pytest.raises(ToolError, match="Install tvastar\\[slack\\] for Slack tools"):
            await slack_post_message.invoke(
                {"channel": "#test", "text": "hello"},
                ctx=ToolContext(memory={"slack_token": "xoxb-test"}),
            )


# ── Post message tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_message_success():
    mock_sdk = MagicMock()
    mock_client = MagicMock()
    mock_client.chat_postMessage.return_value = {
        "ok": True,
        "ts": "1234567890.123456",
        "channel": "C01234",
    }
    mock_sdk.WebClient.return_value = mock_client

    with patch.dict(sys.modules, {"slack_sdk": mock_sdk}):
        result = await slack_post_message.invoke(
            {"channel": "#general", "text": "hello"},
            ctx=ToolContext(memory={"slack_token": "xoxb-test"}),
        )

    assert "1234567890.123456" in result
    mock_client.chat_postMessage.assert_called_once_with(
        channel="#general", text="hello"
    )


@pytest.mark.asyncio
async def test_post_message_slack_error():
    mock_sdk = MagicMock()
    mock_client = MagicMock()
    mock_client.chat_postMessage.return_value = {
        "ok": False,
        "error": "channel_not_found",
    }
    mock_sdk.WebClient.return_value = mock_client

    with patch.dict(sys.modules, {"slack_sdk": mock_sdk}):
        with pytest.raises(ToolError, match="Slack error: channel_not_found"):
            await slack_post_message.invoke(
                {"channel": "#nope", "text": "hello"},
                ctx=ToolContext(memory={"slack_token": "xoxb-test"}),
            )


# ── Post thread tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_thread_success():
    mock_sdk = MagicMock()
    mock_client = MagicMock()
    mock_client.chat_postMessage.return_value = {
        "ok": True,
        "ts": "1234567890.999999",
        "channel": "C01234",
    }
    mock_sdk.WebClient.return_value = mock_client

    with patch.dict(sys.modules, {"slack_sdk": mock_sdk}):
        result = await slack_post_thread.invoke(
            {"channel": "C01234", "thread_ts": "1234567890.123456", "text": "reply"},
            ctx=ToolContext(memory={"slack_token": "xoxb-test"}),
        )

    assert "1234567890.999999" in result
    mock_client.chat_postMessage.assert_called_once_with(
        channel="C01234", thread_ts="1234567890.123456", text="reply"
    )


# ── Read messages tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_messages_default_limit():
    mock_sdk = MagicMock()
    mock_client = MagicMock()
    mock_client.conversations_history.return_value = {
        "ok": True,
        "messages": [{"text": "hi", "ts": "1"}],
    }
    mock_sdk.WebClient.return_value = mock_client

    with patch.dict(sys.modules, {"slack_sdk": mock_sdk}):
        result = await slack_read_messages.invoke(
            {"channel": "C01234"},
            ctx=ToolContext(memory={"slack_token": "xoxb-test"}),
        )

    assert "hi" in result
    mock_client.conversations_history.assert_called_once_with(
        channel="C01234", limit=20
    )


@pytest.mark.asyncio
async def test_read_messages_limit_clamped_to_max():
    mock_sdk = MagicMock()
    mock_client = MagicMock()
    mock_client.conversations_history.return_value = {"ok": True, "messages": []}
    mock_sdk.WebClient.return_value = mock_client

    with patch.dict(sys.modules, {"slack_sdk": mock_sdk}):
        await slack_read_messages.invoke(
            {"channel": "C01234", "limit": 999},
            ctx=ToolContext(memory={"slack_token": "xoxb-test"}),
        )

    mock_client.conversations_history.assert_called_once_with(
        channel="C01234", limit=200
    )


@pytest.mark.asyncio
async def test_read_messages_limit_clamped_to_min():
    mock_sdk = MagicMock()
    mock_client = MagicMock()
    mock_client.conversations_history.return_value = {"ok": True, "messages": []}
    mock_sdk.WebClient.return_value = mock_client

    with patch.dict(sys.modules, {"slack_sdk": mock_sdk}):
        await slack_read_messages.invoke(
            {"channel": "C01234", "limit": -5},
            ctx=ToolContext(memory={"slack_token": "xoxb-test"}),
        )

    mock_client.conversations_history.assert_called_once_with(
        channel="C01234", limit=1
    )


@pytest.mark.asyncio
async def test_read_messages_slack_error():
    mock_sdk = MagicMock()
    mock_client = MagicMock()
    mock_client.conversations_history.return_value = {
        "ok": False,
        "error": "not_in_channel",
    }
    mock_sdk.WebClient.return_value = mock_client

    with patch.dict(sys.modules, {"slack_sdk": mock_sdk}):
        with pytest.raises(ToolError, match="Slack error: not_in_channel"):
            await slack_read_messages.invoke(
                {"channel": "C01234"},
                ctx=ToolContext(memory={"slack_token": "xoxb-test"}),
            )
