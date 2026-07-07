"""Unit tests for CompactionEngine individual stage strategies (task 1.3).

Tests verify:
  - _budget_reduction: truncates old tool results beyond keep_last
  - _snip: removes oldest turns with no tool results / decision_record
  - _microcompact: summarizes segments, preserves tool results and decisions
  - _context_collapse: produces structured handoff with goal/decisions/state
  - _auto_compact: emergency compaction keeps goal + tool state + last 3

Requirements: 1.3, 1.4, 1.5, 1.6, 1.7
"""

import pytest

from tvastar.compaction import (
    CompactionEngine,
    ProgressiveCompactionPolicy,
    _WORD_TOKEN_FACTOR,
)
from tvastar.types import Message, TextBlock, ToolResultBlock, ToolUseBlock


# ── Helpers ───────────────────────────────────────────────────────────────────


def _msg(role, text, **metadata):
    return Message(role, [TextBlock(text=text)], metadata=metadata)


def _tool_use_msg(tool_name, tool_use_id):
    return Message("assistant", [ToolUseBlock(name=tool_name, input={}, id=tool_use_id)])


def _tool_result_msg(tool_use_id, content):
    return Message("tool", [ToolResultBlock(tool_use_id=tool_use_id, content=content)])


def _engine(keep_last=3, budget_reduction_max_tokens=200):
    policy = ProgressiveCompactionPolicy(
        keep_last=keep_last,
        budget_reduction_max_tokens=budget_reduction_max_tokens,
    )
    return CompactionEngine(policy)


# ── _budget_reduction ─────────────────────────────────────────────────────────


class TestBudgetReduction:
    def test_truncates_old_tool_results(self):
        """Tool results older than keep_last get truncated."""
        engine = _engine(keep_last=1, budget_reduction_max_tokens=10)
        # 10 tokens ≈ 7.7 words ≈ 38 chars (10 / 1.3 * 5)
        max_chars = int((10 / _WORD_TOKEN_FACTOR) * 5)

        long_content = "x" * 200
        msgs = [
            _tool_use_msg("read_file", "call_1"),
            _tool_result_msg("call_1", long_content),
            _msg("user", "latest"),  # this is keep_last
        ]
        result = engine._budget_reduction(msgs)

        # Tool result (in old part) should be truncated
        tool_blocks = [b for m in result for b in m.blocks if isinstance(b, ToolResultBlock)]
        assert len(tool_blocks) == 1
        assert len(tool_blocks[0].content) <= max_chars + 5  # +5 for "…" char

    def test_preserves_keep_last_tool_results(self):
        """Tool results within keep_last are NOT truncated."""
        engine = _engine(keep_last=2, budget_reduction_max_tokens=10)
        long_content = "x" * 200
        msgs = [
            _msg("user", "old message"),
            _tool_use_msg("read_file", "call_1"),
            _tool_result_msg("call_1", long_content),  # within keep_last
        ]
        result = engine._budget_reduction(msgs)
        tool_blocks = [b for m in result for b in m.blocks if isinstance(b, ToolResultBlock)]
        assert tool_blocks[0].content == long_content

    def test_short_results_unchanged(self):
        """Tool results already under the limit are not modified."""
        engine = _engine(keep_last=1, budget_reduction_max_tokens=200)
        msgs = [
            _tool_use_msg("ls", "call_1"),
            _tool_result_msg("call_1", "short"),
            _msg("user", "latest"),
        ]
        result = engine._budget_reduction(msgs)
        tool_blocks = [b for m in result for b in m.blocks if isinstance(b, ToolResultBlock)]
        assert tool_blocks[0].content == "short"

    def test_no_op_when_all_within_keep_last(self):
        """If messages count <= keep_last, return unchanged."""
        engine = _engine(keep_last=10)
        msgs = [_msg("user", "hi"), _msg("assistant", "hello")]
        result = engine._budget_reduction(msgs)
        assert result == msgs


# ── _snip ─────────────────────────────────────────────────────────────────────


class TestSnip:
    def test_removes_conversational_turns(self):
        """Old user/assistant messages without tool results or decisions get snipped."""
        engine = _engine(keep_last=1)
        msgs = [
            _msg("user", "hi"),
            _msg("assistant", "hello there"),
            _msg("user", "how are you"),
            _msg("user", "latest"),  # keep_last
        ]
        result = engine._snip(msgs)
        # All old messages removed, only keep_last remains
        assert len(result) == 1
        assert result[0].text == "latest"

    def test_preserves_tool_results(self):
        """Messages with role='tool' are kept."""
        engine = _engine(keep_last=1)
        msgs = [
            _msg("user", "do something"),
            _tool_use_msg("read_file", "call_1"),
            _tool_result_msg("call_1", "file content"),
            _msg("user", "latest"),
        ]
        result = engine._snip(msgs)
        # tool_use and tool messages preserved + keep_last
        tool_msgs = [m for m in result if m.role == "tool"]
        assert len(tool_msgs) == 1

    def test_preserves_decision_record_messages(self):
        """Messages flagged with decision_record are kept."""
        engine = _engine(keep_last=1)
        msgs = [
            _msg("user", "I decided to use Python", decision_record=True),
            _msg("user", "some chat"),
            _msg("user", "latest"),
        ]
        result = engine._snip(msgs)
        decision_msgs = [m for m in result if m.metadata.get("decision_record")]
        assert len(decision_msgs) == 1

    def test_preserves_system_messages(self):
        """System messages are always preserved."""
        engine = _engine(keep_last=1)
        msgs = [
            _msg("system", "You are helpful"),
            _msg("user", "chat"),
            _msg("user", "latest"),
        ]
        result = engine._snip(msgs)
        system_msgs = [m for m in result if m.role == "system"]
        assert len(system_msgs) == 1

    def test_no_op_when_all_within_keep_last(self):
        """If messages count <= keep_last, return unchanged."""
        engine = _engine(keep_last=10)
        msgs = [_msg("user", "hi")]
        result = engine._snip(msgs)
        assert result == msgs


# ── _microcompact ─────────────────────────────────────────────────────────────


class TestMicrocompact:
    @pytest.mark.asyncio
    async def test_summarizes_contiguous_segments(self):
        """Contiguous non-tool messages get summarized into a single message."""
        engine = _engine(keep_last=1)
        msgs = [
            _msg("user", "hello"),
            _msg("assistant", "hi there"),
            _msg("user", "what's up"),
            _msg("user", "latest"),  # keep_last
        ]
        result = await engine._microcompact(msgs, model=None)
        # 3 old messages → 1 summary + 1 keep_last
        summaries = [m for m in result if "[Summary]" in m.text]
        assert len(summaries) == 1
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_preserves_tool_results_verbatim(self):
        """Tool results are not summarized."""
        engine = _engine(keep_last=1)
        msgs = [
            _msg("user", "read the file"),
            _tool_use_msg("read_file", "call_1"),
            _tool_result_msg("call_1", "file content here"),
            _msg("user", "thanks"),
            _msg("user", "latest"),
        ]
        result = await engine._microcompact(msgs, model=None)
        tool_msgs = [m for m in result if m.role == "tool"]
        assert len(tool_msgs) == 1
        # Tool use block preserved too
        tool_use_msgs = [m for m in result if any(isinstance(b, ToolUseBlock) for b in m.blocks)]
        assert len(tool_use_msgs) == 1

    @pytest.mark.asyncio
    async def test_preserves_decision_flagged_verbatim(self):
        """Decision-flagged messages are not summarized."""
        engine = _engine(keep_last=1)
        msgs = [
            _msg("user", "chat"),
            _msg("assistant", "We'll use PostgreSQL", decision_record=True),
            _msg("user", "ok"),
            _msg("user", "latest"),
        ]
        result = await engine._microcompact(msgs, model=None)
        decision_msgs = [m for m in result if m.metadata.get("decision_record")]
        assert len(decision_msgs) == 1

    @pytest.mark.asyncio
    async def test_segment_respects_max_size(self):
        """Segments larger than microcompact_segment_size are split."""
        policy = ProgressiveCompactionPolicy(keep_last=1, microcompact_segment_size=3)
        engine = CompactionEngine(policy)
        msgs = [_msg("user", f"msg {i}") for i in range(7)] + [_msg("user", "latest")]
        result = await engine._microcompact(msgs, model=None)
        summaries = [m for m in result if "[Summary]" in m.text]
        # 7 messages / 3 per segment = 3 summaries (3+3+1)
        assert len(summaries) == 3

    @pytest.mark.asyncio
    async def test_summary_respects_token_limit(self):
        """Each summary is bounded by max_summary_tokens equivalent chars."""
        policy = ProgressiveCompactionPolicy(
            keep_last=1,
            microcompact_max_summary_tokens=150,
        )
        engine = CompactionEngine(policy)
        # Create messages with lots of text
        msgs = [_msg("user", "word " * 200) for _ in range(5)] + [_msg("user", "latest")]
        result = await engine._microcompact(msgs, model=None)
        summaries = [m for m in result if "[Summary]" in m.text]
        max_chars = int((150 / _WORD_TOKEN_FACTOR) * 5)
        for s in summaries:
            # Remove "[Summary] " prefix for length check
            body = s.text[len("[Summary] ") :]
            assert len(body) <= max_chars + 5  # +5 for "…"


# ── _context_collapse ─────────────────────────────────────────────────────────


class TestContextCollapse:
    @pytest.mark.asyncio
    async def test_produces_structured_handoff(self):
        """Output contains goal, decisions, and state sections."""
        engine = _engine(keep_last=1)
        msgs = [
            _msg("system", "You are helpful"),
            _msg("user", "Build a web app"),
            _msg("assistant", "Sure, using React", decision_record=True),
            _tool_use_msg("create_file", "call_1"),
            _tool_result_msg("call_1", "file created"),
            _msg("user", "latest"),
        ]
        result = await engine._context_collapse(msgs, model=None)
        handoff = [m for m in result if "[Structured Handoff]" in m.text]
        assert len(handoff) == 1
        assert "## Goal" in handoff[0].text
        assert "## Decisions" in handoff[0].text
        assert "## State" in handoff[0].text

    @pytest.mark.asyncio
    async def test_preserves_system_messages(self):
        """System messages appear before the handoff."""
        engine = _engine(keep_last=1)
        msgs = [
            _msg("system", "System prompt"),
            _msg("user", "hello"),
            _msg("user", "latest"),
        ]
        result = await engine._context_collapse(msgs, model=None)
        assert result[0].role == "system"
        assert result[0].text == "System prompt"

    @pytest.mark.asyncio
    async def test_preserves_keep_last(self):
        """keep_last messages appear at the end."""
        engine = _engine(keep_last=2)
        msgs = [
            _msg("user", "old"),
            _msg("user", "second to last"),
            _msg("user", "last"),
        ]
        result = await engine._context_collapse(msgs, model=None)
        assert result[-1].text == "last"
        assert result[-2].text == "second to last"

    @pytest.mark.asyncio
    async def test_includes_tool_state(self):
        """Tool state section lists active tools and their last outputs."""
        engine = _engine(keep_last=1)
        msgs = [
            _tool_use_msg("read_file", "call_1"),
            _tool_result_msg("call_1", "content of file"),
            _msg("user", "latest"),
        ]
        result = await engine._context_collapse(msgs, model=None)
        handoff = [m for m in result if "[Structured Handoff]" in m.text]
        assert "read_file" in handoff[0].text


# ── _auto_compact ─────────────────────────────────────────────────────────────


class TestAutoCompact:
    @pytest.mark.asyncio
    async def test_keeps_last_3_messages(self):
        """Last 3 messages are always preserved."""
        engine = _engine(keep_last=3)
        msgs = [
            _msg("user", "old 1"),
            _msg("assistant", "old 2"),
            _msg("user", "old 3"),
            _msg("assistant", "recent 1"),
            _msg("user", "recent 2"),
            _msg("assistant", "recent 3"),
        ]
        result = await engine._auto_compact(msgs, model=None)
        # Last 3 must be preserved
        assert result[-1].text == "recent 3"
        assert result[-2].text == "recent 2"
        assert result[-3].text == "recent 1"

    @pytest.mark.asyncio
    async def test_produces_emergency_compact_summary(self):
        """Old messages are replaced by an emergency compact summary."""
        engine = _engine(keep_last=3)
        msgs = [
            _msg("user", "Build a REST API"),
            _tool_use_msg("create_file", "call_1"),
            _tool_result_msg("call_1", "created app.py"),
            _msg("user", "msg A"),
            _msg("user", "msg B"),
            _msg("user", "msg C"),
        ]
        result = await engine._auto_compact(msgs, model=None)
        compact_msgs = [m for m in result if "[Emergency Compact]" in m.text]
        assert len(compact_msgs) == 1
        assert "Goal:" in compact_msgs[0].text
        assert "Tool State:" in compact_msgs[0].text

    @pytest.mark.asyncio
    async def test_preserves_system_messages(self):
        """System messages from old portion are kept."""
        engine = _engine(keep_last=1)
        msgs = [
            _msg("system", "System prompt"),
            _msg("user", "old"),
            _msg("user", "old 2"),
            _msg("user", "latest"),
        ]
        result = await engine._auto_compact(msgs, model=None)
        system_msgs = [m for m in result if m.role == "system"]
        assert len(system_msgs) == 1

    @pytest.mark.asyncio
    async def test_short_messages_no_op(self):
        """If <= 3 messages total, returns unchanged."""
        engine = _engine(keep_last=3)
        msgs = [_msg("user", "hi"), _msg("assistant", "hello")]
        result = await engine._auto_compact(msgs, model=None)
        assert result == msgs

    @pytest.mark.asyncio
    async def test_includes_tool_state_in_summary(self):
        """Emergency compact includes tool state."""
        engine = _engine(keep_last=1)
        msgs = [
            _tool_use_msg("list_dir", "call_1"),
            _tool_result_msg("call_1", "/src /tests"),
            _msg("user", "some work"),
            _msg("assistant", "done"),
            _msg("user", "latest"),
        ]
        result = await engine._auto_compact(msgs, model=None)
        compact_msgs = [m for m in result if "[Emergency Compact]" in m.text]
        assert "list_dir" in compact_msgs[0].text
