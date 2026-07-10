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
from typing import TYPE_CHECKING, Any, Optional

from .compaction_policy import (
    CompactionPolicy,
    CompactionStage,
    ProgressiveCompactionPolicy,
    STAGE_THRESHOLDS,
)
from .types import Message, ToolResultBlock, ToolUseBlock

if TYPE_CHECKING:  # pragma: no cover
    from .session import Session


def _estimate_tokens(messages: list[Message]) -> int:
    """Rough token estimate: ~1.3 tokens per word across all message text."""
    words = sum(sum(1 for _ in re.finditer(r"\S+", m.text)) for m in messages)
    return max(1, int(words * 1.3))


# ponytail: word-count factor matches _estimate_tokens above
_WORD_TOKEN_FACTOR = 1.3


class CompactionEngine:
    """Executes progressive compaction stages on a message list.

    Each stage is idempotent within a session. Failed stages are logged
    and skipped — never break the active session.
    """

    def __init__(self, policy: ProgressiveCompactionPolicy) -> None:
        self._policy = policy
        self._logger = __import__("logging").getLogger(__name__)

    def current_usage_ratio(self, messages: list[Message]) -> float:
        """Estimate token usage as a ratio of max_context_tokens."""
        if self._policy.token_estimator is not None:
            tokens = self._policy.token_estimator(messages)
        else:
            # Word-count heuristic
            words = sum(sum(1 for _ in re.finditer(r"\S+", m.text)) for m in messages)
            tokens = max(1, int(words * _WORD_TOKEN_FACTOR))
        return (
            tokens / self._policy.max_context_tokens if self._policy.max_context_tokens > 0 else 0.0
        )

    def pending_stages(self, usage_ratio: float) -> list[CompactionStage]:
        """Return stages that should fire (ascending order) for given usage.

        A stage is pending if its threshold <= usage AND it has not yet been
        executed in this session.
        """
        return sorted(
            stage
            for stage, threshold in STAGE_THRESHOLDS.items()
            if threshold <= usage_ratio and stage not in self._policy.stages_executed
        )

    async def execute(
        self,
        messages: list[Message],
        model: Any,
        *,
        system: Optional[str] = None,
    ) -> list[Message]:
        """Run all pending stages. Returns compacted message list.

        - Stages execute in ascending order up to current threshold.
        - Failed stages: log, discard partial mutations, proceed.
        - If still >95% after all stages: re-run AUTO_COMPACT.
        - Second+ compaction: update existing summary in-place.
        """
        usage = self.current_usage_ratio(messages)
        stages = self.pending_stages(usage)

        for stage in stages:
            snapshot = list(messages)
            try:
                messages = await self._run_stage(stage, messages, model, system=system)
                self._policy.stages_executed.add(stage)
            except Exception as exc:
                self._logger.warning(
                    "Compaction stage %s failed: %s — restoring snapshot",
                    stage.name,
                    exc,
                )
                messages = snapshot

        # Post-execution: if still >95%, re-run AUTO_COMPACT
        usage = self.current_usage_ratio(messages)
        if usage > 0.95:
            snapshot = list(messages)
            try:
                messages = await self._run_stage(
                    CompactionStage.AUTO_COMPACT, messages, model, system=system
                )
                # Second compaction: update existing summary in-place
                messages = self._update_summary_in_place(messages)
            except Exception as exc:
                self._logger.warning(
                    "Re-run AUTO_COMPACT failed: %s — restoring snapshot",
                    exc,
                )
                messages = snapshot

        # Deduplicate tool outputs
        messages = self._deduplicate_tool_outputs(messages)
        return messages

    async def _run_stage(
        self,
        stage: CompactionStage,
        messages: list[Message],
        model: Any,
        *,
        system: Optional[str] = None,
    ) -> list[Message]:
        """Dispatch to the appropriate stage strategy."""
        if stage == CompactionStage.BUDGET_REDUCTION:
            return self._budget_reduction(messages)
        elif stage == CompactionStage.SNIP:
            return self._snip(messages)
        elif stage == CompactionStage.MICROCOMPACT:
            return await self._microcompact(messages, model)
        elif stage == CompactionStage.CONTEXT_COLLAPSE:
            return await self._context_collapse(messages, model)
        elif stage == CompactionStage.AUTO_COMPACT:
            return await self._auto_compact(messages, model)
        return messages  # pragma: no cover

    def _update_summary_in_place(self, messages: list[Message]) -> list[Message]:
        """Find existing summary message and update in-place (no recursive nesting).

        Looks for the most recent '[Context compacted:' marker and replaces it
        rather than prepending a new one.
        """
        # Find existing compaction notice + summary pair
        compact_indices = [
            i
            for i, m in enumerate(messages)
            if m.role == "user" and "[Context compacted:" in m.text
        ]
        if len(compact_indices) >= 2:
            # Keep only the most recent summary pair, remove older ones
            oldest = compact_indices[0]
            # Remove the oldest compaction notice and its following summary
            if oldest + 1 < len(messages) and messages[oldest + 1].role == "assistant":
                messages = messages[:oldest] + messages[oldest + 2 :]
        return messages

    # --- Stage strategies ---

    def _budget_reduction(self, messages: list[Message]) -> list[Message]:
        """Truncate tool results older than keep_last to budget_reduction_max_tokens.

        Requirement 1.3: reduce tool output verbosity for old results.
        """
        keep_last = self._policy.keep_last
        max_tokens = self._policy.budget_reduction_max_tokens
        # ponytail: estimate max chars from token budget using word heuristic
        # tokens ≈ words * 1.3, so max_words ≈ max_tokens / 1.3, chars ≈ words * 5
        max_chars = int((max_tokens / _WORD_TOKEN_FACTOR) * 5)

        if len(messages) <= keep_last:
            return messages

        old_messages = messages[:-keep_last] if keep_last > 0 else messages
        tail = messages[-keep_last:] if keep_last > 0 else []

        result = []
        for msg in old_messages:
            if msg.role == "tool":
                new_blocks = []
                for block in msg.blocks:
                    if isinstance(block, ToolResultBlock) and len(block.content) > max_chars:
                        new_blocks.append(
                            ToolResultBlock(
                                tool_use_id=block.tool_use_id,
                                content=block.content[:max_chars] + "…",
                                is_error=block.is_error,
                            )
                        )
                    else:
                        new_blocks.append(block)
                result.append(
                    Message(
                        msg.role,
                        new_blocks,
                        id=msg.id,
                        created_at=msg.created_at,
                        metadata=msg.metadata,
                    )
                )
            else:
                result.append(msg)
        return result + tail

    def _snip(self, messages: list[Message]) -> list[Message]:
        """Remove oldest turns with no tool results and no decision_record flag.

        Requirement 1.4: snip conversational noise while preserving tool context
        and explicit decisions.
        """
        keep_last = self._policy.keep_last
        if len(messages) <= keep_last:
            return messages

        old_messages = messages[:-keep_last] if keep_last > 0 else messages
        tail = messages[-keep_last:] if keep_last > 0 else []

        def _is_protected(msg: Message) -> bool:
            """Keep messages with tool results or decision_record flag."""
            if msg.role == "tool":
                return True
            if msg.metadata.get("decision_record"):
                return True
            # Keep messages that have tool_use blocks (assistant requesting tools)
            if any(isinstance(b, ToolUseBlock) for b in msg.blocks):
                return True
            # Keep system messages
            if msg.role == "system":
                return True
            return False

        # Remove unprotected messages from oldest first
        result = [m for m in old_messages if _is_protected(m)]
        return result + tail

    async def _microcompact(self, messages: list[Message], model: Any) -> list[Message]:
        """Summarize contiguous segments of ≤10 messages to ≤150 tokens.

        Requirement 1.5: preserve all tool results and decision-flagged messages
        verbatim. Only summarize contiguous non-tool, non-decision segments.
        If no model provided, truncate each segment to 150-token equivalent.
        """
        max_segment = self._policy.microcompact_segment_size
        max_summary_tokens = self._policy.microcompact_max_summary_tokens
        # ponytail: 150 tokens ≈ 115 words ≈ 575 chars
        max_summary_chars = int((max_summary_tokens / _WORD_TOKEN_FACTOR) * 5)

        keep_last = self._policy.keep_last
        if len(messages) <= keep_last:
            return messages

        old_messages = messages[:-keep_last] if keep_last > 0 else messages
        tail = messages[-keep_last:] if keep_last > 0 else []

        def _is_preserved(msg: Message) -> bool:
            if msg.role == "tool":
                return True
            if msg.metadata.get("decision_record"):
                return True
            if any(isinstance(b, ToolUseBlock) for b in msg.blocks):
                return True
            if msg.role == "system":
                return True
            return False

        # Group into segments: preserved messages stay, contiguous non-preserved get summarized
        result: list[Message] = []
        segment: list[Message] = []

        def _flush_segment() -> None:
            if not segment:
                return
            # Process in chunks of max_segment
            for i in range(0, len(segment), max_segment):
                chunk = segment[i : i + max_segment]
                summary_text = self._summarize_segment_sync(chunk, max_summary_chars, model)
                result.append(
                    Message(
                        "assistant",
                        f"[Summary] {summary_text}",
                        metadata={"compaction_summary": True},
                    )
                )
            segment.clear()

        for msg in old_messages:
            if _is_preserved(msg):
                _flush_segment()
                result.append(msg)
            else:
                segment.append(msg)

        _flush_segment()
        return result + tail

    def _summarize_segment_sync(self, segment: list[Message], max_chars: int, model: Any) -> str:
        """Produce a summary for a segment. No model → truncation fallback."""
        # ponytail: without a model, concatenate and truncate
        parts = []
        for m in segment:
            text = m.text.strip()
            if text:
                parts.append(text)
        combined = " | ".join(parts)
        if len(combined) > max_chars:
            combined = combined[:max_chars] + "…"
        return combined

    async def _context_collapse(self, messages: list[Message], model: Any) -> list[Message]:
        """Collapse to structured handoff: goal/decisions/state sections.

        Requirement 1.6: keep system prompt, most recent summary, and keep_last.
        Collapse everything else into a structured handoff message.
        If no model, extract goal/decisions/state heuristically.
        """
        keep_last = self._policy.keep_last
        if len(messages) <= keep_last:
            return messages

        tail = messages[-keep_last:] if keep_last > 0 else []
        old_messages = messages[:-keep_last] if keep_last > 0 else messages

        # Separate system messages and find most recent summary
        system_msgs: list[Message] = []
        most_recent_summary: Message | None = None
        collapse_msgs: list[Message] = []

        for msg in old_messages:
            if msg.role == "system":
                system_msgs.append(msg)
            elif msg.metadata.get("compaction_summary") or "[Context compacted:" in msg.text:
                most_recent_summary = msg
            else:
                collapse_msgs.append(msg)

        # Extract structured handoff from collapsible messages
        goal = self._extract_goal(collapse_msgs)
        decisions = self._extract_decisions(collapse_msgs)
        state = self._extract_tool_state(collapse_msgs)

        handoff_text = (
            f"[Structured Handoff]\n"
            f"## Goal\n{goal}\n\n"
            f"## Decisions\n{decisions}\n\n"
            f"## State\n{state}"
        )
        handoff_msg = Message("assistant", handoff_text, metadata={"compaction_summary": True})

        result = system_msgs[:]
        if most_recent_summary:
            result.append(most_recent_summary)
        result.append(handoff_msg)
        return result + tail

    def _extract_goal(self, messages: list[Message]) -> str:
        """Heuristic: last user message or first user message as the goal."""
        user_msgs = [m for m in messages if m.role == "user" and m.text.strip()]
        if user_msgs:
            return user_msgs[-1].text.strip()[:200]
        return "Continue current task"

    def _extract_decisions(self, messages: list[Message]) -> str:
        """Extract decision-flagged messages."""
        decisions = [
            m.text.strip()[:100]
            for m in messages
            if m.metadata.get("decision_record") and m.text.strip()
        ]
        if decisions:
            return "\n".join(f"- {d}" for d in decisions[:10])
        return "- No explicit decisions recorded"

    def _extract_tool_state(self, messages: list[Message]) -> str:
        """Extract active tool names and their last outputs."""
        tool_states: dict[str, str] = {}
        tool_use_names: dict[str, str] = {}  # tool_use_id -> tool_name

        for msg in messages:
            for block in msg.blocks:
                if isinstance(block, ToolUseBlock):
                    tool_use_names[block.id] = block.name
                elif isinstance(block, ToolResultBlock):
                    name = tool_use_names.get(block.tool_use_id, "unknown")
                    tool_states[name] = block.content[:100]

        if tool_states:
            return "\n".join(f"- {name}: {output}" for name, output in tool_states.items())
        return "- No active tool state"

    async def _auto_compact(self, messages: list[Message], model: Any) -> list[Message]:
        """Emergency full-context summarization.

        Requirement 1.7: preserve only current goal, active tool state, and last 3.
        If no model, extract heuristically.
        """
        # Keep last 3 messages always
        last_n = 3
        tail = messages[-last_n:] if len(messages) > last_n else messages[:]
        old_messages = messages[:-last_n] if len(messages) > last_n else []

        if not old_messages:
            return messages

        # Extract goal and tool state from old messages
        goal = self._extract_goal(old_messages)
        state = self._extract_tool_state(old_messages)

        summary_text = f"[Emergency Compact]\nGoal: {goal}\nTool State: {state}"
        summary_msg = Message("assistant", summary_text, metadata={"compaction_summary": True})

        # Keep system messages
        system_msgs = [m for m in old_messages if m.role == "system"]
        return system_msgs + [summary_msg] + tail

    def _deduplicate_tool_outputs(self, messages: list[Message]) -> list[Message]:
        """Remove orphaned ToolResultBlocks whose tool_use_id has no corresponding ToolUseBlock."""

        # First pass: collect all tool_use_ids that have a ToolUseBlock present
        present_tool_use_ids: set[str] = {
            block.id for msg in messages for block in msg.blocks if isinstance(block, ToolUseBlock)
        }

        # Second pass: keep ToolResultBlocks only if their tool_use_id is present
        result = []
        for msg in messages:
            if msg.role == "tool":
                keep_blocks = [
                    block
                    for block in msg.blocks
                    if not isinstance(block, ToolResultBlock)
                    or block.tool_use_id in present_tool_use_ids
                ]
                if keep_blocks:
                    result.append(
                        Message(
                            msg.role,
                            keep_blocks,
                            id=msg.id,
                            created_at=msg.created_at,
                            metadata=msg.metadata,
                        )
                    )
            else:
                result.append(msg)
        return result


def should_compact(messages: list[Message], policy: CompactionPolicy) -> bool:
    """Return True if compaction should fire given the current message list."""
    if len(messages) < policy.min_messages:
        return False
    if policy.max_messages > 0 and len(messages) > policy.max_messages:
        return True
    if policy.max_tokens_estimate > 0:
        if _estimate_tokens(messages) > policy.max_tokens_estimate:
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
            max_tokens=policy.summary_max_tokens,
            temperature=policy.summary_temperature,
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
