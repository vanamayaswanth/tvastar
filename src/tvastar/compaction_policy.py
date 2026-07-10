"""Pure compaction policy types — innermost ring, no session/agent dependencies.

These dataclasses define *when* and *how* compaction fires. They import only
from stdlib and .types. Extracted from compaction.py to break the import cycle:
agent.py → compaction.py → (TYPE_CHECKING) session.py → agent.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Optional

from .types import Message


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
        cooldown: Seconds between compaction attempts. While a previous
                  compaction occurred less than ``cooldown`` seconds ago,
                  reactive overflow compaction is skipped. Set to 0.0 to allow
                  compaction on every overflow event.
        summary_max_tokens: Maximum tokens for the summary generation model
                            call. Defaults to 1024.
        summary_temperature: Temperature for the summary generation model call.
                             Defaults to 0.3.
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
    summary_model: Optional[Any] = None
    cooldown: float = 30.0
    """Seconds between compaction attempts. While a previous compaction occurred
    less than ``cooldown`` seconds ago, reactive overflow compaction is skipped.
    Set to 0.0 to allow compaction on every overflow event."""
    summary_max_tokens: int = 1024
    """Maximum tokens for the summary generation model call."""
    summary_temperature: float = 0.3
    """Temperature for the summary generation model call."""


class CompactionStage(IntEnum):
    """Five progressive compaction stages, ordered by severity."""

    BUDGET_REDUCTION = 0  # 60% threshold
    SNIP = 1  # 70% threshold
    MICROCOMPACT = 2  # 80% threshold
    CONTEXT_COLLAPSE = 3  # 90% threshold
    AUTO_COMPACT = 4  # 95% threshold


STAGE_THRESHOLDS: dict[CompactionStage, float] = {
    CompactionStage.BUDGET_REDUCTION: 0.60,
    CompactionStage.SNIP: 0.70,
    CompactionStage.MICROCOMPACT: 0.80,
    CompactionStage.CONTEXT_COLLAPSE: 0.90,
    CompactionStage.AUTO_COMPACT: 0.95,
}


@dataclass
class ProgressiveCompactionPolicy:
    """Five-stage progressive compaction configuration.

    Extends CompactionPolicy with stage-based thresholds relative to
    the model's max context window.
    """

    max_context_tokens: int = 128_000
    keep_last: int = 10
    budget_reduction_max_tokens: int = 200
    microcompact_max_summary_tokens: int = 150
    microcompact_segment_size: int = 10
    summary_model: Optional[Any] = None
    token_estimator: Optional[Callable[[list[Message]], int]] = None
    stages_executed: set[CompactionStage] = field(default_factory=set)
