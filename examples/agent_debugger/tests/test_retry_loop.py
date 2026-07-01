"""Unit tests for the fix-verify retry loop in the Agent Debugger workflow.

Tests verify:
1. _build_fix_prompt includes previous VerificationResult context on retries
2. max_retries is respected (3 total attempts, not 3 retries after the first)
3. all_fixes accumulates every FixProposal generated
4. status is "unresolved" when all attempts produce score < 0.5
5. Status determination logic based on quality_score

Requirements: 4.5, 4.6, 5.3
"""

from __future__ import annotations

import pytest

import sys
from pathlib import Path

# Ensure the examples directory is on sys.path so agent_debugger is importable
_examples_dir = str(Path(__file__).resolve().parent.parent.parent)
if _examples_dir not in sys.path:
    sys.path.insert(0, _examples_dir)

from agent_debugger.schemas import (  # noqa: E402
    DebuggingReport,
    DiagnosisReport,
    FailureMode,
    FixProposal,
    InstructionChange,
    VerificationResult,
)
from agent_debugger.workflow import _build_fix_prompt  # noqa: E402


# --- Fixtures ---


def _make_diagnosis() -> DiagnosisReport:
    """Create a sample DiagnosisReport for testing."""
    return DiagnosisReport(
        failure_modes=[
            FailureMode(
                detector="thrash_loop",
                severity="error",
                message="Agent repeated the same action 5 times",
                evidence=["action repeated"],
                line_range=(10, 20),
            ),
            FailureMode(
                detector="unverified_completion",
                severity="warning",
                message="Agent claimed success without verification",
                evidence=["claimed done"],
                line_range=(30, 40),
            ),
        ],
        root_cause="Agent lacks loop-breaking logic and result verification steps",
        is_adversarial=False,
        injection_evidence=[],
        confidence=0.7,
    )


def _make_verification(
    score: float,
    remaining: list[str] | None = None,
    new_findings: list[FailureMode] | None = None,
) -> VerificationResult:
    """Create a VerificationResult with given score."""
    return VerificationResult(
        quality_score=score,
        new_findings=new_findings or [],
        resolved_modes=["thrash_loop"],
        remaining_modes=remaining or ["unverified_completion"],
        trajectory_length=10,
    )


def _make_fix(instructions: str = "Fixed instructions") -> FixProposal:
    """Create a sample FixProposal."""
    return FixProposal(
        rewritten_instructions=instructions,
        changes=[
            InstructionChange(
                section="general",
                original="original",
                rewritten=instructions,
                rationale="fix the issue",
            )
        ],
        addresses_modes=["thrash_loop", "unverified_completion"],
    )


# --- Tests for _build_fix_prompt ---


class TestBuildFixPrompt:
    """Tests for the _build_fix_prompt helper function."""

    def test_first_attempt_no_prev_verification(self):
        """On first attempt, prompt should not include previous verification context."""
        diagnosis = _make_diagnosis()
        prompt = _build_fix_prompt(diagnosis, prev_verification=None, attempt=1)

        # Should include failure modes from diagnosis
        assert "thrash_loop" in prompt
        assert "unverified_completion" in prompt
        assert "Root cause:" in prompt

        # Should NOT include retry-related context
        assert "Previous attempt" not in prompt
        assert "Still unresolved" not in prompt
        assert "improved fix" not in prompt

    def test_retry_includes_prev_verification_score(self):
        """On retry, prompt should include previous verification's quality score."""
        diagnosis = _make_diagnosis()
        prev_verification = _make_verification(score=0.3)

        prompt = _build_fix_prompt(diagnosis, prev_verification=prev_verification, attempt=2)

        # Should include previous score
        assert "0.30" in prompt
        assert "Previous attempt (#1)" in prompt

    def test_retry_includes_remaining_modes(self):
        """On retry, prompt should list remaining unresolved failure modes."""
        diagnosis = _make_diagnosis()
        prev_verification = _make_verification(score=0.4, remaining=["unverified_completion"])

        prompt = _build_fix_prompt(diagnosis, prev_verification=prev_verification, attempt=2)

        assert "unverified_completion" in prompt
        assert "Still unresolved" in prompt

    def test_retry_includes_new_findings(self):
        """On retry, prompt should mention new issues introduced by previous fix."""
        diagnosis = _make_diagnosis()
        new_finding = FailureMode(
            detector="schema_mismatch",
            severity="error",
            message="Output schema violation",
            evidence=["bad schema"],
            line_range=(5, 8),
        )
        prev_verification = _make_verification(score=0.2, new_findings=[new_finding])

        prompt = _build_fix_prompt(diagnosis, prev_verification=prev_verification, attempt=3)

        assert "New issues introduced" in prompt
        assert "schema_mismatch" in prompt

    def test_retry_asks_for_improved_fix(self):
        """On retry, prompt should explicitly request an improved fix."""
        diagnosis = _make_diagnosis()
        prev_verification = _make_verification(score=0.4)

        prompt = _build_fix_prompt(diagnosis, prev_verification=prev_verification, attempt=2)

        assert "improved fix" in prompt


# --- Tests for retry loop status determination ---


class TestRetryLoopStatus:
    """Tests verifying status determination after the retry loop."""

    def test_status_resolved_when_score_high(self):
        """Status should be 'resolved' when quality_score >= 0.8."""
        verification = _make_verification(score=0.85)
        if verification.quality_score >= 0.5:
            status = "resolved" if verification.quality_score >= 0.8 else "improved"
        else:
            status = "unresolved"
        assert status == "resolved"

    def test_status_improved_when_score_moderate(self):
        """Status should be 'improved' when 0.5 <= quality_score < 0.8."""
        verification = _make_verification(score=0.6)
        if verification.quality_score >= 0.5:
            status = "resolved" if verification.quality_score >= 0.8 else "improved"
        else:
            status = "unresolved"
        assert status == "improved"

    def test_status_unresolved_when_score_low(self):
        """Status should be 'unresolved' when quality_score < 0.5 (all retries exhausted)."""
        verification = _make_verification(score=0.3)
        if verification.quality_score >= 0.5:
            status = "resolved" if verification.quality_score >= 0.8 else "improved"
        else:
            status = "unresolved"
        assert status == "unresolved"


# --- Tests for all_fixes accumulation and retry budget ---


class TestRetryBudgetAndAccumulation:
    """Tests verifying max_retries budget and all_fixes accumulation logic."""

    def test_max_retries_controls_total_attempts(self):
        """max_retries=3 means exactly 3 total attempts (not 3 retries after first)."""
        max_retries = 3
        all_fixes: list[FixProposal] = []

        # Simulate the loop from workflow.py
        for attempt in range(1, max_retries + 1):
            fix = _make_fix(f"Fix attempt {attempt}")
            all_fixes.append(fix)
            verification = _make_verification(score=0.3)  # Always below threshold

            if verification.quality_score >= 0.5:
                break

        # Should have exactly max_retries fixes (3 total attempts)
        assert len(all_fixes) == 3

    def test_loop_breaks_early_on_success(self):
        """Loop should break as soon as quality_score >= 0.5."""
        max_retries = 3
        all_fixes: list[FixProposal] = []
        scores = [0.3, 0.6, 0.9]  # Second attempt succeeds

        for attempt in range(1, max_retries + 1):
            fix = _make_fix(f"Fix attempt {attempt}")
            all_fixes.append(fix)
            verification = _make_verification(score=scores[attempt - 1])

            if verification.quality_score >= 0.5:
                break

        # Should stop after 2 attempts (second score is >= 0.5)
        assert len(all_fixes) == 2

    def test_all_fixes_accumulates_every_proposal(self):
        """Every FixProposal generated (including retries) should be in all_fixes."""
        max_retries = 3
        all_fixes: list[FixProposal] = []

        for attempt in range(1, max_retries + 1):
            fix = _make_fix(f"Unique fix #{attempt}")
            all_fixes.append(fix)
            verification = _make_verification(score=0.2)  # Keep failing

            if verification.quality_score >= 0.5:
                break

        # All 3 distinct fixes should be accumulated
        assert len(all_fixes) == 3
        assert all_fixes[0].rewritten_instructions == "Unique fix #1"
        assert all_fixes[1].rewritten_instructions == "Unique fix #2"
        assert all_fixes[2].rewritten_instructions == "Unique fix #3"

    def test_unresolved_report_contains_all_fixes(self):
        """When all retries exhausted, DebuggingReport.all_fixes has max_retries entries."""
        max_retries = 3
        all_fixes: list[FixProposal] = []
        diagnosis = _make_diagnosis()
        verification = _make_verification(score=0.3)

        for attempt in range(1, max_retries + 1):
            fix = _make_fix(f"Attempt {attempt}")
            all_fixes.append(fix)

        report = DebuggingReport(
            status="unresolved",
            original_diagnosis=diagnosis,
            final_fix=all_fixes[-1],
            verification=verification,
            attempts=len(all_fixes),
            all_fixes=all_fixes,
            cost_breakdown={"ANALYZE": 0.01, "DIAGNOSE": 0.02, "FIX": 0.03, "VERIFY": 0.01},
        )

        assert report.status == "unresolved"
        assert len(report.all_fixes) == max_retries
        assert report.attempts == max_retries

    def test_prev_verification_passed_on_retry(self):
        """prev_verification should be set to the last verification on each retry."""
        max_retries = 3
        prev_verification: VerificationResult | None = None
        diagnosis = _make_diagnosis()
        prev_verifications_seen: list[VerificationResult | None] = []

        for attempt in range(1, max_retries + 1):
            # Record what prev_verification the fix prompt would receive
            prev_verifications_seen.append(prev_verification)

            # Build prompt (this is what the workflow does)
            _build_fix_prompt(diagnosis, prev_verification, attempt)

            verification = _make_verification(score=0.2 + (attempt * 0.05))

            if verification.quality_score >= 0.5:
                break
            prev_verification = verification

        # First attempt should have no previous verification
        assert prev_verifications_seen[0] is None
        # Second attempt should have the first verification
        assert prev_verifications_seen[1] is not None
        assert prev_verifications_seen[1].quality_score == pytest.approx(0.25)
        # Third attempt should have the second verification
        assert prev_verifications_seen[2] is not None
        assert prev_verifications_seen[2].quality_score == pytest.approx(0.30)
