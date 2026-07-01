"""Trajectory loader — parses JSONL trajectory files into Tvastar Message objects.

Handles file I/O errors gracefully, skips malformed lines with warnings,
and validates that the resulting message list forms a coherent conversation.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from tvastar.types import (
    ContentBlock,
    ImageBlock,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

logger = logging.getLogger(__name__)


def _parse_content_block(raw: dict) -> ContentBlock | None:
    """Parse a raw dict into a typed ContentBlock, or None if unrecognized."""
    block_type = raw.get("type")
    if block_type == "text":
        return TextBlock(text=raw.get("text", ""))
    elif block_type == "tool_use":
        return ToolUseBlock(
            name=raw.get("name", ""),
            input=raw.get("input", {}),
            id=raw.get("id", ""),
        )
    elif block_type == "tool_result":
        return ToolResultBlock(
            tool_use_id=raw.get("tool_use_id", ""),
            content=raw.get("content", ""),
            is_error=raw.get("is_error", False),
        )
    elif block_type == "image":
        return ImageBlock(
            data=raw.get("data", ""),
            media_type=raw.get("media_type", "image/jpeg"),
            source_type=raw.get("source_type", "base64"),
        )
    return None


def _parse_content(raw_content) -> str | list[ContentBlock]:
    """Parse the content field from a JSON object into Message-compatible form."""
    if isinstance(raw_content, str):
        return raw_content
    if isinstance(raw_content, list):
        blocks: list[ContentBlock] = []
        for item in raw_content:
            if isinstance(item, dict):
                block = _parse_content_block(item)
                if block is not None:
                    blocks.append(block)
            elif isinstance(item, str):
                blocks.append(TextBlock(text=item))
        return blocks
    return str(raw_content) if raw_content is not None else ""


def _json_to_message(data: dict) -> Message:
    """Convert a parsed JSON dict into a Tvastar Message object."""
    role = data.get("role", "user")
    raw_content = data.get("content", "")
    content = _parse_content(raw_content)

    kwargs: dict = {"role": role, "content": content}

    if "id" in data:
        kwargs["id"] = data["id"]
    if "created_at" in data:
        kwargs["created_at"] = float(data["created_at"])
    if "metadata" in data and isinstance(data["metadata"], dict):
        kwargs["metadata"] = data["metadata"]

    return Message(**kwargs)


def load_trajectory(path: Path) -> list[Message]:
    """Parse a JSONL trajectory file into Message objects.

    Each line in the file is expected to be a valid JSON object representing
    a message in the conversation. Lines that are not valid JSON are skipped
    with a warning logged that includes the line number.

    Args:
        path: Path to the JSONL trajectory file.

    Returns:
        A list of Tvastar Message objects parsed from the file.

    Raises:
        FileNotFoundError: If the file does not exist, with a descriptive
            message including the file path.
        PermissionError: If the file cannot be read due to permissions,
            with a descriptive message including the file path.
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Trajectory file not found: {path}")

    try:
        text = path.read_text(encoding="utf-8")
    except PermissionError:
        raise PermissionError(f"Permission denied reading trajectory file: {path}")

    messages: list[Message] = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue

        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as e:
            logger.warning("Skipping malformed JSON at line %d: %s", line_number, e)
            continue

        if not isinstance(data, dict):
            logger.warning(
                "Skipping non-object JSON at line %d: expected dict, got %s",
                line_number,
                type(data).__name__,
            )
            continue

        messages.append(_json_to_message(data))

    return messages


def validate_trajectory(messages: list[Message]) -> list[Message]:
    """Validate that the message list forms a coherent conversation.

    Checks:
    - The list is non-empty.
    - Messages have valid roles.
    - The conversation starts with a user or system message (not assistant/tool).
    - Tool result messages reference a preceding tool use.

    Returns the validated list unchanged if valid. Logs warnings for any
    coherence issues found but does not remove messages.

    Args:
        messages: List of Message objects to validate.

    Returns:
        The same list of messages (validated).

    Raises:
        ValueError: If the trajectory is empty.
    """
    if not messages:
        raise ValueError("Trajectory is empty: no messages to validate")

    valid_roles = {"system", "user", "assistant", "tool"}

    for i, msg in enumerate(messages):
        if msg.role not in valid_roles:
            logger.warning("Message %d has invalid role '%s'", i, msg.role)

    # Check that conversation doesn't start with assistant or tool
    if messages[0].role in ("assistant", "tool"):
        logger.warning(
            "Trajectory starts with '%s' message; expected 'user' or 'system'",
            messages[0].role,
        )

    # Track tool_use IDs to validate tool_result references
    seen_tool_use_ids: set[str] = set()

    for i, msg in enumerate(messages):
        # Collect tool_use IDs from assistant messages
        for block in msg.blocks:
            if isinstance(block, ToolUseBlock):
                seen_tool_use_ids.add(block.id)

        # Validate tool_result references
        if msg.role == "tool":
            for block in msg.blocks:
                if isinstance(block, ToolResultBlock):
                    if block.tool_use_id not in seen_tool_use_ids:
                        logger.warning(
                            "Message %d: tool_result references unknown tool_use_id '%s'",
                            i,
                            block.tool_use_id,
                        )

    return messages
