"""Slack integration tools — post messages, threads, read channel history.

Requires the ``slack_sdk`` package (install via ``pip install tvastar[slack]``).
Auth resolves in order: ToolContext.memory['slack_token'] → SLACK_BOT_TOKEN env var.

Use :func:`slack_toolset` to get all Slack tools as a list.
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from ..errors import ToolError
from .base import Tool, ToolContext, tool


def _get_token(ctx: ToolContext) -> str:
    """Resolve Slack bot token from context or env."""
    # 1. Check ToolContext memory
    if ctx.memory is not None:
        if isinstance(ctx.memory, dict):
            token = ctx.memory.get("slack_token")
            if token:
                return token
        # ponytail: if memory is some other object with a slack_token attr, skip — YAGNI
    # 2. Fall back to env var
    token = os.environ.get("SLACK_BOT_TOKEN")
    if token:
        return token
    raise ToolError("No Slack token configured. Provide via ToolContext or SLACK_BOT_TOKEN env var")


def _ensure_slack_sdk():
    """Lazy-import slack_sdk, raising ImportError with install hint if missing."""
    try:
        import slack_sdk  # noqa: F401

        return slack_sdk
    except ImportError:
        raise ImportError("Install tvastar[slack] for Slack tools")


@tool
async def slack_post_message(ctx: ToolContext, channel: str, text: str) -> dict:
    """Post a message to a Slack channel.

    Args:
        channel: Channel ID or name (e.g. '#general' or 'C01234ABCDE').
        text: Message text to post.
    """
    slack_sdk = _ensure_slack_sdk()
    token = _get_token(ctx)
    client = slack_sdk.WebClient(token=token)

    resp = await asyncio.to_thread(client.chat_postMessage, channel=channel, text=text)
    if not resp.get("ok"):
        raise ToolError(f"Slack error: {resp.get('error', 'unknown_error')}")
    return {"ok": True, "ts": resp["ts"], "channel": resp["channel"]}


@tool
async def slack_post_thread(ctx: ToolContext, channel: str, thread_ts: str, text: str) -> dict:
    """Post a reply to an existing Slack thread.

    Args:
        channel: Channel ID or name.
        thread_ts: Timestamp of the parent message (the thread root).
        text: Reply text to post.
    """
    slack_sdk = _ensure_slack_sdk()
    token = _get_token(ctx)
    client = slack_sdk.WebClient(token=token)

    resp = await asyncio.to_thread(
        client.chat_postMessage, channel=channel, thread_ts=thread_ts, text=text
    )
    if not resp.get("ok"):
        raise ToolError(f"Slack error: {resp.get('error', 'unknown_error')}")
    return {"ok": True, "ts": resp["ts"], "channel": resp["channel"]}


@tool
async def slack_read_messages(ctx: ToolContext, channel: str, limit: Optional[int] = None) -> dict:
    """Read recent messages from a Slack channel.

    Args:
        channel: Channel ID (e.g. 'C01234ABCDE').
        limit: Number of messages to fetch (default 20, max 200).
    """
    slack_sdk = _ensure_slack_sdk()
    token = _get_token(ctx)
    client = slack_sdk.WebClient(token=token)

    # Clamp limit: default 20, max 200
    if limit is None:
        limit = 20
    limit = max(1, min(limit, 200))

    resp = await asyncio.to_thread(client.conversations_history, channel=channel, limit=limit)
    if not resp.get("ok"):
        raise ToolError(f"Slack error: {resp.get('error', 'unknown_error')}")
    return {"ok": True, "messages": resp.get("messages", [])}


def slack_toolset() -> list[Tool]:
    """All Slack tools as a list, ready to register."""
    return [slack_post_message, slack_post_thread, slack_read_messages]
