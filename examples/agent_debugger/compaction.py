"""Context compaction — shrinks message lists that exceed the memory cap.

When a trajectory's total byte size exceeds the configured memory_cap_mb
threshold, earlier messages are summarized into a compact system message
while the most recent messages are preserved unchanged.
"""

from __future__ import annotations

from tvastar.types import Message


def _byte_size(messages: list[Message]) -> int:
    """Estimate total byte size of a message list based on text content."""
    total = 0
    for msg in messages:
        if isinstance(msg.content, str):
            total += len(msg.content.encode("utf-8"))
        else:
            for block in msg.blocks:
                total += len(getattr(block, "text", "").encode("utf-8"))
                total += len(str(getattr(block, "input", "")).encode("utf-8"))
                total += len(getattr(block, "content", "").encode("utf-8"))
    return total


def _summarize_messages(messages: list[Message]) -> str:
    """Produce a compact text summary of a list of messages."""
    if not messages:
        return "[Summary of 0 earlier messages]"

    roles_seen: set[str] = set()
    topics: list[str] = []

    for msg in messages:
        roles_seen.add(msg.role)
        # Extract a brief topic hint from the first text block
        text = msg.text.strip()
        if text:
            # Take the first 60 chars as a topic hint
            snippet = text[:60].replace("\n", " ")
            if snippet not in topics:
                topics.append(snippet)

    # Limit topic hints to keep the summary compact
    max_topics = 5
    topic_str = "; ".join(topics[:max_topics])
    if len(topics) > max_topics:
        topic_str += f"; ... and {len(topics) - max_topics} more"

    roles_str = ", ".join(sorted(roles_seen))
    return (
        f"[Summary of {len(messages)} earlier messages: "
        f"roles involved: {roles_str}; "
        f"topics discussed: {topic_str}]"
    )


def compact_messages(
    messages: list[Message],
    memory_cap_mb: float,
    keep_last: int = 10,
) -> list[Message]:
    """Apply context compaction if total byte size exceeds the memory cap.

    If the total byte size of *messages* is within *memory_cap_mb*, the list
    is returned unchanged.  Otherwise the most recent *keep_last* messages are
    kept intact and all earlier messages are replaced by a single summary
    system message.

    The output list length is guaranteed to be <= keep_last + 2.

    Args:
        messages: The full list of conversation messages.
        memory_cap_mb: Maximum allowed size in megabytes before compaction
            is triggered.
        keep_last: Number of tail messages to preserve verbatim.

    Returns:
        A (possibly compacted) list of Message objects.
    """
    cap_bytes = int(memory_cap_mb * 1024 * 1024)
    total_bytes = _byte_size(messages)

    # Under threshold — return as-is
    if total_bytes <= cap_bytes:
        return messages

    # If we have fewer messages than keep_last, nothing to compact
    if len(messages) <= keep_last:
        return messages

    # Split into earlier and tail
    tail = messages[-keep_last:]
    earlier = messages[:-keep_last]

    # Create a summary of the earlier messages
    summary_text = _summarize_messages(earlier)
    summary_msg = Message(role="system", content=summary_text)

    # Return summary + tail (total count = 1 + keep_last <= keep_last + 2)
    return [summary_msg] + tail
