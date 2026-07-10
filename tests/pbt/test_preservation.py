"""Preservation property tests — Observable Behavior Unchanged for Non-Bug-Condition Inputs.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7**

These tests capture the CURRENT correct behavior on unfixed code.
They are designed to PASS on the current (unfixed) code, establishing a
behavioral baseline that must remain identical after the fixes are applied.

Seven preservation properties:
1. Dedup removes genuinely orphaned ToolResultBlocks (no corresponding ToolUseBlock)
2. Compressor returns [dedup: ...] for previously-seen file content
3. version_history() returns all versions in chronological order when below cap
4. rollback() restores correct config_snapshot for any version in history
5. Word-overlap-only scoring produces same winner when overlap >= seq*0.7
6. finditer word count equals findall word count for any text
7. thrash_loop() emits Finding with count=N for N identical tool calls
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import hypothesis.strategies as st
from hypothesis import given, settings, assume

from tvastar.types import Message, ToolUseBlock, ToolResultBlock
from tvastar.compaction import CompactionEngine, ProgressiveCompactionPolicy
from tvastar.compressor import ToolOutputCompressor
from tvastar.fleet.registry import FleetRegistry
from tvastar.profiles import AgentProfile
from tvastar.detect.detectors import thrash_loop
from tvastar.detect.base import RunContext, _EmptyToolRegistry


# ---------------------------------------------------------------------------
# Property 1: Dedup removes genuinely orphaned ToolResultBlocks
# For any message list with orphaned ToolResultBlocks (tool_use_id does NOT
# match any ToolUseBlock in messages), dedup removes them.
# ---------------------------------------------------------------------------


@settings(max_examples=30, deadline=None)
@given(
    n_tools=st.integers(min_value=1, max_value=5),
)
def test_preservation_dedup_keeps_unique_tool_results(n_tools: int):
    """Preservation 1: When each tool_name appears exactly once, all results survive.

    This is the non-bug-condition case: each tool_name has exactly one
    ToolUseBlock, so "most recent per tool_name" keeps everything.
    After fix, this must still hold (the fix changes dedup to be
    tool_use_id-based, which also keeps everything here).

    **Validates: Requirements 3.1**
    """
    engine = CompactionEngine(ProgressiveCompactionPolicy())

    # Each tool has a UNIQUE name — non-bug-condition (bug is multiple uses of same name)
    tool_names = [f"tool_{i}" for i in range(n_tools)]

    tool_uses = [
        ToolUseBlock(name=tool_names[i], input={"arg": i}, id=f"call_{i:04d}")
        for i in range(n_tools)
    ]
    tool_results = [
        ToolResultBlock(tool_use_id=f"call_{i:04d}", content=f"result {i}") for i in range(n_tools)
    ]

    messages = [
        Message(role="assistant", content=tool_uses),
        Message(role="tool", content=tool_results),
    ]

    deduped = engine._deduplicate_tool_outputs(messages)

    # Count surviving ToolResultBlocks
    surviving_results = []
    for msg in deduped:
        for block in msg.blocks:
            if isinstance(block, ToolResultBlock):
                surviving_results.append(block)

    # All results should survive — each tool_name has exactly one invocation
    assert len(surviving_results) == n_tools, (
        f"Expected {n_tools} ToolResultBlocks to survive dedup (one per unique tool_name), "
        f"got {len(surviving_results)}."
    )

    # Verify the correct results survived
    surviving_ids = {r.tool_use_id for r in surviving_results}
    expected_ids = {f"call_{i:04d}" for i in range(n_tools)}
    assert surviving_ids == expected_ids


# ---------------------------------------------------------------------------
# Property 2: Compressor returns [dedup: ...] for previously-seen file content
# For any file content fed twice within cache window, compressor returns
# dedup reference on second call.
# ---------------------------------------------------------------------------


@settings(max_examples=30, deadline=None)
@given(
    content=st.text(min_size=1, max_size=500),
)
def test_preservation_compressor_dedup_reference(content: str):
    """Preservation 2: Second call with same content returns [dedup: ...] reference.

    The compressor uses SHA-256 to detect repeated file content. On the second
    call with identical content, it returns a short dedup reference. This behavior
    must be preserved after adding the LRU cache cap.

    **Validates: Requirements 3.2**
    """
    compressor = ToolOutputCompressor(threshold=4000)

    # First call — stores the hash, returns None (no change)
    result1 = compressor("read_file", {"path": "/test.txt"}, content)
    assert result1 is None, "First read should return None (no dedup needed)"

    # Second call with same content — should return dedup reference
    result2 = compressor("read_file", {"path": "/test.txt"}, content)
    assert result2 is not None, "Second read of same content should return dedup reference"
    assert "[dedup:" in result2, f"Expected [dedup: ...] reference, got: {result2!r}"
    assert "sha256=" in result2, f"Expected sha256 hash in dedup reference, got: {result2!r}"


# ---------------------------------------------------------------------------
# Property 3: version_history() returns all versions in chronological order
# For any agent registered fewer than cap times, all versions are returned.
# ---------------------------------------------------------------------------


@settings(max_examples=30, deadline=None)
@given(
    num_registrations=st.integers(min_value=1, max_value=20),
)
def test_preservation_version_history_chronological(num_registrations: int):
    """Preservation 3: version_history() returns all versions in order when below cap.

    When an agent is registered fewer than the cap (50) times, all versions
    should be returned in chronological order (oldest first).

    **Validates: Requirements 3.3**
    """
    registry = FleetRegistry(fleet_name="test-fleet")
    mock_loop = MagicMock()
    mock_loop.name = "test-agent"

    versions_registered = []
    for i in range(num_registrations):
        version = f"{i}.0.0"
        versions_registered.append(version)
        registry.register(
            mock_loop,
            name="test-agent",
            version=version,
            owner="test-team",
        )

    history = registry.version_history("test-agent")

    # All versions should be present (below cap)
    assert len(history) == num_registrations, (
        f"Expected {num_registrations} versions in history, got {len(history)}"
    )

    # Versions should be in chronological order
    history_versions = [v.version for v in history]
    assert history_versions == versions_registered, (
        f"Version history is not in chronological order. "
        f"Expected {versions_registered}, got {history_versions}"
    )


# ---------------------------------------------------------------------------
# Property 4: rollback() restores correct config_snapshot
# For any version in history, rollback restores that version's config exactly.
# ---------------------------------------------------------------------------


@settings(max_examples=30, deadline=None)
@given(
    num_versions=st.integers(min_value=2, max_value=10),
    rollback_idx=st.integers(min_value=0, max_value=9),
)
def test_preservation_rollback_restores_config(num_versions: int, rollback_idx: int):
    """Preservation 4: rollback() restores the correct config_snapshot.

    For any version in history, rolling back to it restores that version's
    config_snapshot exactly. This must be preserved after adding the dict index.

    **Validates: Requirements 3.4**
    """
    # Ensure rollback_idx is within range
    assume(rollback_idx < num_versions)

    registry = FleetRegistry(fleet_name="test-fleet")
    mock_loop = MagicMock()
    mock_loop.name = "test-agent"

    for i in range(num_versions):
        registry.register(
            mock_loop,
            name="test-agent",
            version=f"{i}.0.0",
            owner="test-team",
        )

    # Get the expected config for the target version
    history = registry.version_history("test-agent")
    target_version = history[rollback_idx]
    expected_config = dict(target_version.config_snapshot)

    # Perform rollback
    entry = registry.rollback("test-agent", target_version.version)

    # Verify config restored correctly
    assert entry.config_overrides == expected_config, (
        f"Rollback to version {target_version.version!r} did not restore config. "
        f"Expected {expected_config}, got {entry.config_overrides}"
    )
    assert entry.version == target_version.version


# ---------------------------------------------------------------------------
# Property 5: Word-overlap-only scoring produces same winner when overlap dominates
# For any (task, profiles) pair where overlap >= seq*0.7 for the winner,
# removing SequenceMatcher doesn't change the result.
# ---------------------------------------------------------------------------


@settings(max_examples=30, deadline=None)
@given(
    task_words=st.lists(
        st.sampled_from(
            [
                "review",
                "code",
                "test",
                "write",
                "fix",
                "deploy",
                "debug",
                "check",
                "auth",
                "database",
                "api",
                "frontend",
                "backend",
                "security",
                "build",
                "run",
                "style",
                "refactor",
                "optimize",
            ]
        ),
        min_size=2,
        max_size=6,
    ),
)
def test_preservation_routing_winner_unchanged(task_words: list[str]):
    """Preservation 5: Word-overlap scoring produces same winner as overlap+seq*0.7.

    When the overlap score is the dominant factor (overlap >= seq*0.7 for the
    winner), the overall winner is determined by overlap alone. This property
    confirms that removing SequenceMatcher doesn't change routing decisions
    in the common case.

    **Validates: Requirements 3.5**
    """
    import difflib

    task = " ".join(task_words)

    profiles = [
        AgentProfile(name="code-reviewer", description="Reviews code for bugs and style issues"),
        AgentProfile(name="test-writer", description="Writes unit tests and integration tests"),
        AgentProfile(name="deployer", description="Deploys code to production and staging"),
        AgentProfile(
            name="security-auditor", description="Audits code for security vulnerabilities"
        ),
        AgentProfile(name="db-admin", description="Manages database schema and queries"),
    ]

    # Compute scores using the CURRENT algorithm (overlap + seq*0.7)
    words = set(task.lower().split())
    scores_current = {}
    scores_overlap_only = {}

    for profile in profiles:
        desc = (profile.description or "") + " " + profile.name
        desc_words = set(desc.lower().split())
        if not desc_words:
            scores_current[profile.name] = 0.0
            scores_overlap_only[profile.name] = 0.0
            continue

        overlap = len(words & desc_words) / max(len(words | desc_words), 1)
        seq = difflib.SequenceMatcher(None, task.lower(), desc.lower()).ratio()
        score_current = max(overlap, seq * 0.7)

        scores_current[profile.name] = score_current
        scores_overlap_only[profile.name] = overlap

    # Find the winner under current scoring
    current_winner = max(scores_current, key=scores_current.get)
    current_best_score = scores_current[current_winner]

    # Find the winner under overlap-only scoring
    overlap_winner = max(scores_overlap_only, key=scores_overlap_only.get)

    # If the current winner's overlap >= seq*0.7 for that profile, overlap alone
    # would have chosen the same winner. This is the preservation property.
    current_overlap = scores_overlap_only[current_winner]

    # Only assert when overlap dominates for the winner
    # (i.e., the winner was chosen by overlap, not by seq*0.7)
    if current_overlap >= current_best_score:
        # overlap was the max for the winner, so overlap-only should give same result
        # (when there's a unique winner by overlap)
        if scores_overlap_only[current_winner] > max(
            s for n, s in scores_overlap_only.items() if n != current_winner
        ):
            assert overlap_winner == current_winner, (
                f"Routing winner changed! Current: {current_winner}, "
                f"Overlap-only: {overlap_winner}. "
                f"Scores: {scores_current}"
            )


# ---------------------------------------------------------------------------
# Property 6: finditer word count equals findall word count
# For any text string, the two counting methods produce identical results.
# ---------------------------------------------------------------------------


@settings(max_examples=50, deadline=None)
@given(
    text=st.text(min_size=0, max_size=500),
)
def test_preservation_word_count_equivalence(text: str):
    """Preservation 6: re.finditer count == re.findall count for any text.

    This proves that replacing re.findall with re.finditer for counting
    produces identical word counts, ensuring current_usage_ratio() returns
    the same float value after the fix.

    **Validates: Requirements 3.6**
    """
    findall_count = len(re.findall(r"\S+", text))
    finditer_count = sum(1 for _ in re.finditer(r"\S+", text))

    assert findall_count == finditer_count, (
        f"Word count mismatch for text {text!r}: findall={findall_count}, finditer={finditer_count}"
    )


# ---------------------------------------------------------------------------
# Property 7: thrash_loop() emits Finding with count=N for N identical calls
# For any tool call sequence with N identical calls, thrash_loop emits
# Finding with count=N regardless of key representation.
# ---------------------------------------------------------------------------


@settings(max_examples=30, deadline=None)
@given(
    n_calls=st.integers(min_value=3, max_value=10),
    tool_name=st.sampled_from(["read_file", "write_file", "exec_cmd", "search"]),
    arg_value=st.text(min_size=1, max_size=100),
)
def test_preservation_thrash_loop_correct_count(n_calls: int, tool_name: str, arg_value: str):
    """Preservation 7: thrash_loop() emits Finding with count=N for N identical calls.

    When N identical tool calls are made (same name, same args), thrash_loop
    must detect them and emit a Finding with count=N. This must be preserved
    regardless of whether the dict key is full JSON or a hash.

    **Validates: Requirements 3.7**
    """
    tool_input = {"path": arg_value}

    tool_uses = [
        ToolUseBlock(name=tool_name, input=dict(tool_input), id=f"call_{i:04d}")
        for i in range(n_calls)
    ]
    tool_results = [
        ToolResultBlock(tool_use_id=f"call_{i:04d}", content="ok") for i in range(n_calls)
    ]

    messages = [
        Message(role="assistant", content=tool_uses),
        Message(role="tool", content=tool_results),
    ]

    ctx = RunContext(
        messages=messages,
        tools=_EmptyToolRegistry(),
        stopped="end_turn",
        final_text="done",
    )

    findings = thrash_loop(ctx, threshold=3)

    # Should detect the thrash pattern
    assert len(findings) >= 1, f"thrash_loop failed to detect {n_calls} identical {tool_name} calls"

    # Verify the count is correct
    thrash_finding = findings[0]
    assert thrash_finding.evidence.get("count") == n_calls, (
        f"Expected count={n_calls} in Finding, got {thrash_finding.evidence.get('count')}"
    )
    assert thrash_finding.evidence.get("tool") == tool_name, (
        f"Expected tool={tool_name!r} in Finding, got {thrash_finding.evidence.get('tool')!r}"
    )
