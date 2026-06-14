"""Context compaction — auto-summarise old messages before hitting token limits.

Long-running agent sessions accumulate messages until they exceed the model's
context window. Compaction prevents silent truncation failures by summarising
the oldest messages into a single compact entry, preserving the tail for
immediate context continuity.

Usage (automatic, inside a session)::

    agent = create_agent(
        "assistant",
        model=model,
        compaction=CompactionPolicy(max_messages=40, keep_last=10),
    )

Usage (manual, from application code)::

    from tvastar.compaction import compact_session

    await compact_session(session, policy=CompactionPolicy())

Design:
  - Cheap heuristic: count messages (not tokens) unless ``token_estimator`` is
    provided. Token counting is language-model specific and optional.
  - When triggered: summarise messages[0 : len - keep_last] via the model,
    replace them with a single System/User message pair.
  - Never compacts if fewer than ``min_messages`` exist (no-op guard).
  - Isolated: a compaction failure never breaks a live session.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional

from .types import Message

if TYPE_CHECKING:  # pragma: no cover
    from .session import Session


@dataclass
class CompactionPolicy:
    """Controls when and how context compaction fires.

    Attributes:
        max_messages: Compact when the message count exceeds this value.
                      Set to 0 to disable message-count trigger.
        max_tokens_estimate: Compact when estimated token count exceeds this.
                             Uses a simple word-count heuristic unless you pass
                             ``token_estimator``. Set to 0 to disable.
        keep_last: Number of recent messages to *always* keep uncompacted.
                   These provide immediate context continuity.
        min_messages: Never compact unless there are at least this many messages
                      (prevents thrashing on short sessions).
        summary_instruction: Instruction sent to the model when summarising.
        token_estimator: Optional ``(messages) -> int`` callable for accurate
                         token counting (e.g. tiktoken for OpenAI models).
    """

    max_messages: int = 60
    max_tokens_estimate: int = 80_000
    keep_last: int = 10
    min_messages: int = 20
    summary_instruction: str = (
        "Produce a concise factual summary of the conversation so far. "
        "Preserve: key decisions made, files created or edited, tool results "
        "that matter, and any open questions. Be dense — this replaces the "
        "original messages to save context space."
    )
    token_estimator: Optional[Callable[[list[Message]], int]] = None
    summary_model: Optional[Any] = None


def _estimate_tokens(messages: list[Message]) -> int:
    """Rough token estimate: ~1.3 tokens per word across all message text."""
    words = sum(len(re.findall(r"\S+", m.text)) for m in messages)
    return max(1, int(words * 1.3))


def should_compact(messages: list[Message], policy: CompactionPolicy) -> bool:
    """Return True if compaction should fire given the current message list."""
    if len(messages) < policy.min_messages:
        return False
    if policy.max_messages > 0 and len(messages) > policy.max_messages:
        return True
    if policy.max_tokens_estimate > 0:
        estimator = policy.token_estimator or _estimate_tokens
        if estimator(messages) > policy.max_tokens_estimate:
            return True
    return False


async def compact_messages(
    messages: list[Message],
    model: Any,
    policy: CompactionPolicy,
    *,
    system: Optional[str] = None,
) -> list[Message]:
    """Summarise the old portion of ``messages`` and return a compacted list.

    The returned list always ends with the same ``keep_last`` messages.
    The summarised portion is replaced by two messages:
      - A user message describing what was compacted.
      - An assistant message containing the summary.

    If the model call fails, the original message list is returned unchanged
    (compaction failures must never break a live session).
    """
    keep = max(policy.keep_last, 1)
    if len(messages) <= keep:
        return messages  # nothing to compact

    to_summarise = messages[:-keep] if keep > 0 else messages
    tail = messages[-keep:] if keep > 0 else []

    # Build a minimal conversation for the summariser
    summary_messages = [
        Message("user", policy.summary_instruction),
    ]
    # Include a flattened transcript of what we're about to compact
    transcript_parts = []
    for m in to_summarise:
        role_label = m.role.upper()
        text = m.text.strip()
        if text:
            transcript_parts.append(f"[{role_label}]: {text[:500]}")
    transcript = "\n".join(transcript_parts)
    summary_messages.append(Message("user", f"Conversation to summarise:\n\n{transcript}"))

    effective_model = policy.summary_model or model
    try:
        from .types import ModelResponse

        resp: ModelResponse = await effective_model.generate(
            summary_messages,
            system=system or "You are a helpful summariser.",
            tools=None,
            max_tokens=1024,
            temperature=0.3,
        )
        summary_text = resp.message.text.strip()
    except Exception:
        return messages  # compaction failed silently — keep original

    compacted_count = len(to_summarise)
    compact_notice = Message(
        "user",
        f"[Context compacted: {compacted_count} earlier messages summarised to save space]",
    )
    summary_msg = Message("assistant", summary_text)

    return [compact_notice, summary_msg, *tail]


async def compact_session(
    session: "Session",
    *,
    policy: Optional[CompactionPolicy] = None,
    force: bool = False,
) -> bool:
    """Compact a session's messages in-place if the policy threshold is met.

    Args:
        session: The Session to compact.
        policy: CompactionPolicy to use. Falls back to session.spec.compaction
                if available, then to CompactionPolicy() defaults.
        force: Skip the threshold check and always compact.

    Returns:
        True if compaction was performed, False otherwise.
    """
    eff_policy = policy or getattr(session.spec, "compaction", None) or CompactionPolicy()

    if not force and not should_compact(session.messages, eff_policy):
        return False

    compacted = await compact_messages(
        session.messages,
        session.spec.model,
        eff_policy,
        system=session.spec.build_system_prompt(),
    )

    if compacted is not session.messages:
        session.messages = compacted
        session._checkpoint()
        return True

    return False
