"""Property-based tests for silent-failure detectors.

**Validates: Requirements 2.8, 2.1, 2.2**

Property 5: Detector execution completeness
- For any completed RunResult and N configured detectors, all N execute
  and their combined findings appear in RunResult.findings.
- Also tests fault isolation: a crashing detector doesn't prevent others
  from running.

Property 6: Unverified completion detection
- For any RunResult where the final assistant text contains a success claim AND
  the last tool result contains a failure signal, the unverified_completion
  detector SHALL emit a Finding with Severity.ERROR.

Property 7: Thrash loop detection
- For any message history where the same tool+args combination appears more
  than threshold times, the thrash_loop detector SHALL emit a Finding with
  Severity.WARNING.
"""

from __future__ import annotations

import hypothesis.strategies as st
from hypothesis import given, settings

from tvastar.detect import (
    Finding,
    RunContext,
    Severity,
    run_detectors,
    thrash_loop,
    unverified_completion,
)
from tvastar.detect.base import Detector
from tvastar.tools.base import ToolRegistry
from tvastar.types import Message, ToolResultBlock, ToolUseBlock


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


@st.composite
def st_detector_names(draw: st.DrawFn) -> list[str]:
    """Generate a list of 1–7 unique detector names."""
    n = draw(st.integers(min_value=1, max_value=7))
    names = draw(
        st.lists(
            st.from_regex(r"det_[a-z]{3,8}", fullmatch=True),
            min_size=n,
            max_size=n,
            unique=True,
        )
    )
    return names


def _make_detector(name: str) -> Detector:
    """Create a detector function that returns a single Finding with the given name."""

    def detector(ctx: RunContext) -> list[Finding]:
        return [
            Finding(
                detector=name,
                severity=Severity.WARNING,
                message=f"finding from {name}",
            )
        ]

    detector.__name__ = name
    return detector


def _make_crashing_detector(name: str) -> Detector:
    """Create a detector that always raises RuntimeError."""

    def detector(ctx: RunContext) -> list[Finding]:
        raise RuntimeError(f"crash in {name}")

    detector.__name__ = name
    return detector


def _make_run_context() -> RunContext:
    """Create a minimal RunContext for testing detector dispatch."""
    return RunContext(
        messages=[Message(role="user", content="hello")],
        tools=ToolRegistry(),
        stopped="end_turn",
        final_text="done",
    )


# ---------------------------------------------------------------------------
# Property 5: Detector execution completeness
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(names=st_detector_names())
def test_all_detectors_execute_and_findings_present(names: list[str]):
    """For N configured detectors, all N execute and their combined findings
    appear in the result.

    **Validates: Requirements 2.8**
    """
    detectors = [_make_detector(name) for name in names]
    ctx = _make_run_context()

    findings = run_detectors(ctx, detectors)

    # All N detectors produced findings
    assert len(findings) == len(names)
    # Every detector name is represented exactly once
    found_names = [f.detector for f in findings]
    assert set(found_names) == set(names)


@settings(max_examples=100, deadline=None)
@given(names=st_detector_names())
def test_fault_isolation_crashing_detector_doesnt_block_others(names: list[str]):
    """A crashing detector doesn't prevent others from running.

    Insert a crashing detector in the middle of the list and verify
    all non-crashing detectors still produce their findings.

    **Validates: Requirements 2.8**
    """
    # Build the list: good detectors with one crasher inserted
    good_detectors = [_make_detector(name) for name in names]
    crash_name = "crash_detector"
    crasher = _make_crashing_detector(crash_name)

    # Insert crasher at position len//2 (middle)
    all_detectors = list(good_detectors)
    insert_pos = len(all_detectors) // 2
    all_detectors.insert(insert_pos, crasher)

    ctx = _make_run_context()
    findings = run_detectors(ctx, all_detectors)

    # All good detectors still produced findings
    good_findings = [f for f in findings if f.detector != crash_name]
    assert len(good_findings) == len(names)
    assert set(f.detector for f in good_findings) == set(names)

    # The crasher produced an INFO finding about the crash (fault isolation)
    crash_findings = [f for f in findings if f.detector == crash_name]
    assert len(crash_findings) == 1
    assert crash_findings[0].severity == Severity.INFO
    assert "RuntimeError" in crash_findings[0].message


# ---------------------------------------------------------------------------
# Strategies for Property 7: Thrash loop detection
# ---------------------------------------------------------------------------


@st.composite
def st_tool_name(draw: st.DrawFn) -> str:
    """Generate a valid tool name."""
    return draw(st.from_regex(r"[a-z][a-z_]{2,12}", fullmatch=True))


@st.composite
def st_tool_input(draw: st.DrawFn) -> dict:
    """Generate a simple tool input dict."""
    n_keys = draw(st.integers(min_value=0, max_value=4))
    keys = draw(
        st.lists(
            st.from_regex(r"[a-z]{2,6}", fullmatch=True),
            min_size=n_keys,
            max_size=n_keys,
            unique=True,
        )
    )
    values = draw(
        st.lists(
            st.one_of(
                st.text(
                    min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=("L", "N"))
                ),
                st.integers(min_value=-100, max_value=100),
                st.booleans(),
            ),
            min_size=n_keys,
            max_size=n_keys,
        )
    )
    return dict(zip(keys, values))


def _build_thrash_context(tool_name: str, tool_input: dict, repeat_count: int) -> RunContext:
    """Build a RunContext with the same tool+args repeated `repeat_count` times."""
    blocks = [
        ToolUseBlock(name=tool_name, input=tool_input, id=f"call_{i:04d}")
        for i in range(repeat_count)
    ]
    # Place all tool use blocks in a single assistant message
    messages = [
        Message(role="user", content="do something"),
        Message(role="assistant", content=blocks),
    ]
    return RunContext(
        messages=messages,
        tools=ToolRegistry(),
        stopped="end_turn",
        final_text="done",
    )


# ---------------------------------------------------------------------------
# Property 7: Thrash loop detection
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    tool_name=st_tool_name(),
    tool_input=st_tool_input(),
    extra=st.integers(min_value=1, max_value=10),
    threshold=st.integers(min_value=2, max_value=6),
)
def test_thrash_loop_fires_above_threshold(
    tool_name: str, tool_input: dict, extra: int, threshold: int
):
    """For any message history where the same tool+args combination appears
    more than threshold times, the thrash_loop detector emits a Finding with
    Severity.WARNING.

    **Validates: Requirements 2.2**
    """
    repeat_count = threshold + extra  # always > threshold
    ctx = _build_thrash_context(tool_name, tool_input, repeat_count)

    findings = thrash_loop(ctx, threshold=threshold)

    # Must emit at least one WARNING finding
    assert len(findings) >= 1
    # Check the finding properties
    finding = findings[0]
    assert finding.detector == "thrash_loop"
    assert finding.severity == Severity.WARNING
    assert tool_name in finding.message
    assert str(repeat_count) in finding.message


@settings(max_examples=100, deadline=None)
@given(
    tool_name=st_tool_name(),
    tool_input=st_tool_input(),
    threshold=st.integers(min_value=2, max_value=6),
)
def test_thrash_loop_does_not_fire_below_threshold(
    tool_name: str, tool_input: dict, threshold: int
):
    """At threshold-1 repetitions, the thrash_loop detector does NOT fire.

    This tests the boundary condition: exactly threshold-1 repetitions of
    the same tool+args should not trigger a finding.

    **Validates: Requirements 2.2**
    """
    repeat_count = threshold - 1  # always below threshold
    ctx = _build_thrash_context(tool_name, tool_input, repeat_count)

    findings = thrash_loop(ctx, threshold=threshold)

    # Must NOT emit any findings
    assert len(findings) == 0


# ---------------------------------------------------------------------------
# Strategies for Property 6: Unverified completion detection
# ---------------------------------------------------------------------------

# Words from the _SUCCESS_CLAIM pattern that trigger the detector.
_SUCCESS_CLAIMS = [
    "done",
    "success",
    "successful",
    "successfully",
    "complete",
    "completed",
    "fixed",
    "passing",
    "passes",
    "all tests pass",
    "works now",
    "resolved",
    "ready",
]

# Phrases from the _FAILURE_SIGNAL pattern that indicate tool failure.
# Note: the detector uses \b word boundaries, so signals that start with
# non-word characters (like "[exit") need a word character before them,
# while others just need proper word boundaries around them.
_FAILURE_SIGNALS_WORD_BOUNDARY = [
    "failed",
    "failure",
    "error",
    "traceback",
    "exception",
    "AssertionError",
    "exit code 1",
    "1 failed",
    "3 failed",
    "12 failed",
]


@st.composite
def st_success_claim_text(draw: st.DrawFn) -> str:
    """Generate final assistant text containing a success claim keyword."""
    claim = draw(st.sampled_from(_SUCCESS_CLAIMS))
    prefix = draw(st.text(alphabet="abcdefghijklmnop ", min_size=0, max_size=30))
    suffix = draw(st.text(alphabet="abcdefghijklmnop .", min_size=0, max_size=30))
    return f"{prefix} {claim} {suffix}"


@st.composite
def st_failure_tool_result(draw: st.DrawFn) -> ToolResultBlock:
    """Generate a ToolResultBlock whose content contains a failure signal.

    Uses signals that have proper word boundaries (the detector regex uses \\b).
    """
    signal = draw(st.sampled_from(_FAILURE_SIGNALS_WORD_BOUNDARY))
    prefix = draw(st.text(alphabet="abcdefghijklmnop\n ", min_size=0, max_size=40))
    suffix = draw(st.text(alphabet="abcdefghijklmnop\n .", min_size=0, max_size=40))
    # Ensure word boundary: add space before and after the signal
    content = f"{prefix} {signal} {suffix}"
    return ToolResultBlock(tool_use_id="call_test123456", content=content, is_error=False)


# ---------------------------------------------------------------------------
# Property 6: Unverified completion detection
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    success_text=st_success_claim_text(),
    failure_result=st_failure_tool_result(),
)
def test_unverified_completion_emits_error_finding(
    success_text: str, failure_result: ToolResultBlock
):
    """For any RunContext where final_text contains a success claim AND
    the last tool result contains a failure signal, the unverified_completion
    detector emits a Finding with Severity.ERROR.

    **Validates: Requirements 2.1**
    """
    # Build messages with a tool use + tool result + final assistant text
    tool_use = ToolUseBlock(name="run_tests", input={}, id="call_test123456")
    messages = [
        Message(role="user", content="fix the bug"),
        Message(role="assistant", content=[tool_use]),
        Message(role="tool", content=[failure_result]),
        Message(role="assistant", content=success_text),
    ]

    ctx = RunContext(
        messages=messages,
        tools=ToolRegistry(),
        stopped="end_turn",
        final_text=success_text,
    )

    findings = unverified_completion(ctx)

    # Must emit at least one finding
    assert len(findings) >= 1
    # The finding must be from the unverified_completion detector
    assert findings[0].detector == "unverified_completion"
    # The finding must have Severity.ERROR
    assert findings[0].severity == Severity.ERROR


@settings(max_examples=100, deadline=None)
@given(
    success_text=st_success_claim_text(),
)
def test_unverified_completion_emits_error_for_is_error_result(
    success_text: str,
):
    """When final_text has a success claim and the last tool result has
    is_error=True, the detector fires regardless of result content.

    **Validates: Requirements 2.1**
    """
    # Tool result with is_error=True but no failure keywords in content
    failure_result = ToolResultBlock(
        tool_use_id="call_err123456",
        content="command exited",
        is_error=True,
    )
    tool_use = ToolUseBlock(name="exec_cmd", input={"cmd": "test"}, id="call_err123456")
    messages = [
        Message(role="user", content="run the tests"),
        Message(role="assistant", content=[tool_use]),
        Message(role="tool", content=[failure_result]),
        Message(role="assistant", content=success_text),
    ]

    ctx = RunContext(
        messages=messages,
        tools=ToolRegistry(),
        stopped="end_turn",
        final_text=success_text,
    )

    findings = unverified_completion(ctx)

    assert len(findings) >= 1
    assert findings[0].detector == "unverified_completion"
    assert findings[0].severity == Severity.ERROR


@settings(max_examples=100, deadline=None)
@given(
    failure_result=st_failure_tool_result(),
)
def test_unverified_completion_no_finding_without_success_claim(
    failure_result: ToolResultBlock,
):
    """When final_text does NOT contain a success claim, the detector
    should not fire, even if tool results show failure.

    **Validates: Requirements 2.1**
    """
    # Use text that has no success claim keywords
    neutral_text = "I tried running the tests but encountered issues."

    tool_use = ToolUseBlock(name="run_tests", input={}, id="call_test123456")
    messages = [
        Message(role="user", content="fix the bug"),
        Message(role="assistant", content=[tool_use]),
        Message(role="tool", content=[failure_result]),
        Message(role="assistant", content=neutral_text),
    ]

    ctx = RunContext(
        messages=messages,
        tools=ToolRegistry(),
        stopped="end_turn",
        final_text=neutral_text,
    )

    findings = unverified_completion(ctx)

    # No findings — success claim is absent
    assert len(findings) == 0
