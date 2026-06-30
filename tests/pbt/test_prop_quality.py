"""Property-based tests for quality score arithmetic.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

Property 8: Quality score arithmetic
- For any RunResult with E error findings, W warning findings, and stopped
  reason S: score = max(0, 100 - 30*E - 10*W - penalty(S))
  where penalty is 20 for max_steps, 50 for error, 20 for budget, 0 otherwise.
- Grade boundaries: score >= 80 → "PASS", score >= 60 → "WARN", score < 60 → "FAIL"
"""

from __future__ import annotations

import hypothesis.strategies as st
from hypothesis import given, settings

from tvastar.detect import Finding, Severity
from tvastar.quality import score_run
from tvastar.session import RunResult
from tvastar.types import Message, Usage


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

st_stopped_reasons = st.sampled_from(["end_turn", "max_steps", "error", "budget"])

st_findings_list = st.lists(
    st.sampled_from([Severity.ERROR, Severity.WARNING]),
    min_size=0,
    max_size=10,
)


def _penalty(stopped: str) -> int:
    """Compute the stop-reason penalty matching quality.py logic."""
    if stopped == "max_steps":
        return 20
    elif stopped == "error":
        return 50
    elif stopped == "budget":
        return 20
    return 0


def _make_finding(severity: Severity) -> Finding:
    """Create a minimal Finding with the given severity."""
    return Finding(
        detector="test_detector",
        severity=severity,
        message=f"test {severity.value} finding",
    )


def _make_run_result(findings: list[Finding], stopped: str) -> RunResult:
    """Create a minimal RunResult with the given findings and stopped reason."""
    return RunResult(
        text="done",
        messages=[Message(role="user", content="hello")],
        usage=Usage(input_tokens=100, output_tokens=50),
        steps=1,
        stopped=stopped,
        findings=findings,
    )


# ---------------------------------------------------------------------------
# Property 8: Quality score arithmetic
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(severities=st_findings_list, stopped=st_stopped_reasons)
def test_score_matches_formula(severities: list[Severity], stopped: str):
    """For any combination of errors, warnings, and stopped reason, the
    computed score matches: max(0, 100 - 30*E - 10*W - penalty(S)).

    **Validates: Requirements 3.1, 3.2**
    """
    findings = [_make_finding(s) for s in severities]
    result = _make_run_result(findings, stopped)

    report = score_run(result)

    num_errors = severities.count(Severity.ERROR)
    num_warnings = severities.count(Severity.WARNING)
    expected_score = max(0, 100 - 30 * num_errors - 10 * num_warnings - _penalty(stopped))

    assert report.score == expected_score


@settings(max_examples=100, deadline=None)
@given(severities=st_findings_list, stopped=st_stopped_reasons)
def test_grade_boundaries_correct(severities: list[Severity], stopped: str):
    """For any computed score, the grade assignment respects:
    score >= 80 → "PASS", score >= 60 → "WARN", score < 60 → "FAIL".

    **Validates: Requirements 3.3, 3.4, 3.5**
    """
    findings = [_make_finding(s) for s in severities]
    result = _make_run_result(findings, stopped)

    report = score_run(result)

    if report.score >= 80:
        assert report.grade == "PASS"
    elif report.score >= 60:
        assert report.grade == "WARN"
    else:
        assert report.grade == "FAIL"
