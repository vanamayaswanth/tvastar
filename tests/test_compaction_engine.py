"""Unit tests for CompactionEngine — stage execution pipeline.

Tests verify:
  - current_usage_ratio with token_estimator and word-count heuristic
  - pending_stages returns correct ascending stages for usage
  - execute orchestrator: snapshot → run stage → on failure restore + log → mark executed
  - Post-execution: re-run AUTO_COMPACT when still >95%
  - Second compaction updates summary in-place (no recursive nesting)
  - Tool output deduplication retains most recent unique per tool name

Requirements: 1.2, 1.8, 1.11
"""

import pytest

from tvastar.compaction import (
    CompactionEngine,
    CompactionStage,
    ProgressiveCompactionPolicy,
    STAGE_THRESHOLDS,
)
from tvastar.types import Message, TextBlock, ToolResultBlock, ToolUseBlock


# ── Helpers ───────────────────────────────────────────────────────────────────


def _msg(role, text):
    return Message(role, [TextBlock(text=text)])


def _tool_use_msg(tool_name, tool_use_id):
    return Message("assistant", [ToolUseBlock(name=tool_name, input={}, id=tool_use_id)])


def _tool_result_msg(tool_use_id, content):
    return Message("tool", [ToolResultBlock(tool_use_id=tool_use_id, content=content)])


# ── current_usage_ratio ───────────────────────────────────────────────────────


def test_usage_ratio_with_token_estimator():
    """Uses policy.token_estimator when provided."""
    policy = ProgressiveCompactionPolicy(
        max_context_tokens=1000,
        token_estimator=lambda msgs: 500,
    )
    engine = CompactionEngine(policy)
    msgs = [_msg("user", "hello")]
    assert engine.current_usage_ratio(msgs) == pytest.approx(0.5)


def test_usage_ratio_word_count_heuristic():
    """Falls back to word-count heuristic when no token_estimator."""
    policy = ProgressiveCompactionPolicy(max_context_tokens=1000)
    engine = CompactionEngine(policy)
    # 10 words * 1.3 factor = 13 tokens → 13/1000 = 0.013
    msgs = [_msg("user", "one two three four five six seven eight nine ten")]
    ratio = engine.current_usage_ratio(msgs)
    assert 0.01 < ratio < 0.02


def test_usage_ratio_zero_max_tokens():
    """Returns 0.0 when max_context_tokens is 0 (avoid division by zero)."""
    policy = ProgressiveCompactionPolicy(max_context_tokens=0)
    engine = CompactionEngine(policy)
    msgs = [_msg("user", "hello world")]
    assert engine.current_usage_ratio(msgs) == 0.0


# ── pending_stages ────────────────────────────────────────────────────────────


def test_pending_stages_ascending_order():
    """Returns stages in ascending order when threshold <= usage."""
    policy = ProgressiveCompactionPolicy()
    engine = CompactionEngine(policy)
    # 0.85 usage → BUDGET_REDUCTION (0.60), SNIP (0.70), MICROCOMPACT (0.80) are pending
    stages = engine.pending_stages(0.85)
    assert stages == [
        CompactionStage.BUDGET_REDUCTION,
        CompactionStage.SNIP,
        CompactionStage.MICROCOMPACT,
    ]


def test_pending_stages_excludes_already_executed():
    """Excludes stages already in policy.stages_executed."""
    policy = ProgressiveCompactionPolicy()
    policy.stages_executed.add(CompactionStage.BUDGET_REDUCTION)
    engine = CompactionEngine(policy)
    stages = engine.pending_stages(0.85)
    assert CompactionStage.BUDGET_REDUCTION not in stages
    assert CompactionStage.SNIP in stages


def test_pending_stages_all_at_95():
    """At 95% usage, all five stages are pending."""
    policy = ProgressiveCompactionPolicy()
    engine = CompactionEngine(policy)
    stages = engine.pending_stages(0.95)
    assert len(stages) == 5
    assert stages == sorted(CompactionStage)


def test_pending_stages_none_below_60():
    """Below 60% usage, no stages are pending."""
    policy = ProgressiveCompactionPolicy()
    engine = CompactionEngine(policy)
    assert engine.pending_stages(0.50) == []


# ── execute pipeline ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_marks_stages_as_executed():
    """Stages are marked executed on success."""
    policy = ProgressiveCompactionPolicy(
        max_context_tokens=100,
        token_estimator=lambda msgs: 75,  # 75% usage → BUDGET_REDUCTION + SNIP
    )
    engine = CompactionEngine(policy)
    msgs = [_msg("user", "hello")]
    result = await engine.execute(msgs, model=None)
    # Both stages should be marked executed
    assert CompactionStage.BUDGET_REDUCTION in policy.stages_executed
    assert CompactionStage.SNIP in policy.stages_executed


@pytest.mark.asyncio
async def test_execute_restores_snapshot_on_failure():
    """On stage failure, snapshot is restored and execution continues."""
    policy = ProgressiveCompactionPolicy(
        max_context_tokens=100,
        token_estimator=lambda msgs: 75,  # 75% → BUDGET_REDUCTION + SNIP
    )
    engine = CompactionEngine(policy)
    msgs = [_msg("user", "hello")]

    # Make _budget_reduction raise
    def _failing_budget_reduction(messages):
        raise RuntimeError("stage failure")

    engine._budget_reduction = _failing_budget_reduction
    result = await engine.execute(msgs, model=None)
    # BUDGET_REDUCTION failed → not marked executed
    assert CompactionStage.BUDGET_REDUCTION not in policy.stages_executed
    # SNIP still executed (it's a stub that returns messages unchanged)
    assert CompactionStage.SNIP in policy.stages_executed
    # Messages should be preserved (stubs return unchanged)
    assert len(result) == 1


@pytest.mark.asyncio
async def test_execute_reruns_auto_compact_when_still_over_95():
    """If still >95% after all stages, re-run AUTO_COMPACT."""
    call_count = [0]

    async def _tracking_auto_compact(messages, model):
        call_count[0] += 1
        return messages

    policy = ProgressiveCompactionPolicy(
        max_context_tokens=100,
        token_estimator=lambda msgs: 96,  # 96% — always over 95
    )
    engine = CompactionEngine(policy)
    engine._auto_compact = _tracking_auto_compact
    msgs = [_msg("user", "hello")]
    await engine.execute(msgs, model=None)
    # AUTO_COMPACT called once in normal stage execution, once in re-run
    assert call_count[0] == 2


# ── update_summary_in_place ───────────────────────────────────────────────────


def test_update_summary_in_place_removes_older_summary():
    """Second compaction updates summary in-place, removing the older one."""
    policy = ProgressiveCompactionPolicy()
    engine = CompactionEngine(policy)
    msgs = [
        _msg("user", "[Context compacted: 10 earlier messages summarised]"),
        _msg("assistant", "Old summary"),
        _msg("user", "Some chat"),
        _msg("user", "[Context compacted: 5 more messages summarised]"),
        _msg("assistant", "New summary"),
        _msg("user", "Latest message"),
    ]
    result = engine._update_summary_in_place(msgs)
    # Should remove the oldest compaction pair
    assert len(result) == 4
    # The newer summary should remain
    assert any("[Context compacted: 5" in m.text for m in result)
    assert not any("[Context compacted: 10" in m.text for m in result)


# ── _deduplicate_tool_outputs ─────────────────────────────────────────────────


def test_deduplicate_retains_most_recent_tool_output():
    """Retains only the most recent tool output per tool name."""
    policy = ProgressiveCompactionPolicy()
    engine = CompactionEngine(policy)
    msgs = [
        _tool_use_msg("read_file", "call_1"),
        _tool_result_msg("call_1", "first content"),
        _tool_use_msg("read_file", "call_2"),
        _tool_result_msg("call_2", "second content"),
        _msg("user", "thanks"),
    ]
    result = engine._deduplicate_tool_outputs(msgs)
    # The first tool result (call_1) should be removed, call_2 kept
    tool_results = [
        block
        for msg in result
        for block in msg.blocks
        if isinstance(block, ToolResultBlock)
    ]
    assert len(tool_results) == 1
    assert tool_results[0].content == "second content"


def test_deduplicate_different_tools_preserved():
    """Different tool names both kept."""
    policy = ProgressiveCompactionPolicy()
    engine = CompactionEngine(policy)
    msgs = [
        _tool_use_msg("read_file", "call_1"),
        _tool_result_msg("call_1", "file content"),
        _tool_use_msg("list_dir", "call_2"),
        _tool_result_msg("call_2", "dir listing"),
    ]
    result = engine._deduplicate_tool_outputs(msgs)
    tool_results = [
        block
        for msg in result
        for block in msg.blocks
        if isinstance(block, ToolResultBlock)
    ]
    assert len(tool_results) == 2
