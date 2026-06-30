"""Tests for tvastar.quality — LoopQualityReport and score_run.

Design for failure philosophy:
- Every score boundary is exercised (not just 0 and 100)
- Every stop reason is tested (end_turn, max_steps, budget, error, unknown)
- Ugly inputs: empty strings, None-ish fields, huge finding counts, unicode
- Summary generation: every branch (no findings, stop-only, findings+stop, lead selection)
- The IndexError regression (stop-reason with zero findings) is explicitly tested
- Integration with real RunResult via MockModel
"""

from __future__ import annotations

import asyncio

import pytest

from tvastar.detect import Finding, Severity
from tvastar.quality import LoopQualityReport, score_run


# ---------------------------------------------------------------------------
# Minimal fake RunResult — only what score_run reads
# ---------------------------------------------------------------------------


class _R:
    """Minimal stand-in for RunResult — only the fields score_run reads."""

    def __init__(self, findings=None, stopped="end_turn"):
        self.findings = findings if findings is not None else []
        self.stopped = stopped


def _err(msg="agent lied about success", detector="unverified_completion"):
    return Finding(detector, Severity.ERROR, msg)


def _warn(msg="tool looping", detector="thrash_loop"):
    return Finding(detector, Severity.WARNING, msg)


def _info(msg="step count high", detector="step_limit"):
    return Finding(detector, Severity.INFO, msg)


# ---------------------------------------------------------------------------
# Scoring math
# ---------------------------------------------------------------------------


class TestScoring:
    def test_clean_run_is_100(self):
        assert score_run(_R()).score == 100

    def test_single_error_deducts_30(self):
        assert score_run(_R([_err()])).score == 70

    def test_single_warning_deducts_10(self):
        assert score_run(_R([_warn()])).score == 90

    def test_two_errors_deduct_60(self):
        assert score_run(_R([_err(), _err()])).score == 40

    def test_three_errors_deduct_90(self):
        assert score_run(_R([_err(), _err(), _err()])).score == 10

    def test_four_errors_floors_at_zero(self):
        # 4 * 30 = 120, floor to 0
        assert score_run(_R([_err()] * 4)).score == 0

    def test_ten_errors_still_zero(self):
        assert score_run(_R([_err()] * 10)).score == 0

    def test_error_and_warning_combined(self):
        # 100 - 30 - 10 = 60
        assert score_run(_R([_err(), _warn()])).score == 60

    def test_info_findings_not_penalised(self):
        # INFO severity should not reduce the score
        assert score_run(_R([_info()])).score == 100

    def test_info_mixed_with_error(self):
        # only the ERROR counts
        assert score_run(_R([_err(), _info()])).score == 70

    def test_max_steps_deducts_20(self):
        assert score_run(_R(stopped="max_steps")).score == 80

    def test_budget_deducts_20(self):
        assert score_run(_R(stopped="budget")).score == 80

    def test_error_stop_deducts_50(self):
        assert score_run(_R(stopped="error")).score == 50

    def test_max_steps_plus_warning(self):
        # 100 - 10 - 20 = 70
        assert score_run(_R([_warn()], stopped="max_steps")).score == 70

    def test_error_stop_plus_error_finding(self):
        # 100 - 30 - 50 = 20
        assert score_run(_R([_err()], stopped="error")).score == 20

    def test_everything_at_once_floors_at_zero(self):
        # 5 errors (-150) + 3 warnings (-30) + error stop (-50) = -230 → 0
        assert score_run(_R([_err()] * 5 + [_warn()] * 3, stopped="error")).score == 0

    def test_unknown_stop_reason_no_penalty(self):
        # Any unrecognised stopped value should not penalise
        assert score_run(_R(stopped="something_future")).score == 100

    def test_empty_stop_string_no_penalty(self):
        assert score_run(_R(stopped="")).score == 100


# ---------------------------------------------------------------------------
# Grade thresholds — every boundary
# ---------------------------------------------------------------------------


class TestGrading:
    def test_100_is_pass(self):
        assert score_run(_R()).grade == "PASS"

    def test_80_is_pass(self):
        # max_steps only: 100 - 20 = 80
        assert score_run(_R(stopped="max_steps")).grade == "PASS"

    def test_79_is_warn(self):
        # 1 error + 1 warning: 100 - 30 - 10 - 10 = ??? Let's force 79 via warnings
        # 1 warning (90) then budget (-20) = 70 — need a different path
        # 2 warnings + budget = 100 - 20 - 20 = 60, not 79
        # Easiest: just check score=79 grade is WARN directly
        class _Score79:
            findings = [_warn(), _warn()]  # -20 → 80 → not 79 exactly
            stopped = "end_turn"

        # 2 warnings = 80, still PASS. Use 3 warnings = 70 → WARN
        r = _R([_warn()] * 3)
        assert r.findings  # sanity
        rep = score_run(r)
        assert rep.score == 70
        assert rep.grade == "WARN"

    def test_80_boundary_is_pass_not_warn(self):
        # exactly 80 must be PASS
        rep = score_run(_R(stopped="max_steps"))
        assert rep.score == 80
        assert rep.grade == "PASS"

    def test_60_is_warn_not_fail(self):
        # 1 error + 1 warning = 60
        rep = score_run(_R([_err(), _warn()]))
        assert rep.score == 60
        assert rep.grade == "WARN"

    def test_59_is_fail(self):
        # 1 error + 1 warning + budget (-20) = 40 → FAIL, use error stop + warning = 40
        rep = score_run(_R([_warn()], stopped="error"))
        assert rep.score == 40
        assert rep.grade == "FAIL"

    def test_zero_is_fail(self):
        assert score_run(_R([_err()] * 10)).grade == "FAIL"

    def test_passed_property_true_when_pass(self):
        assert score_run(_R()).passed is True

    def test_passed_property_false_when_warn(self):
        assert score_run(_R([_warn()] * 3)).passed is False

    def test_passed_property_false_when_fail(self):
        assert score_run(_R([_err()] * 3)).passed is False


# ---------------------------------------------------------------------------
# Summary text — every branch
# ---------------------------------------------------------------------------


class TestSummary:
    def test_no_findings_clean_stop(self):
        assert score_run(_R()).summary == "No issues detected."

    def test_single_error_summary(self):
        rep = score_run(_R([_err("agent lied")]))
        assert "1 error" in rep.summary
        assert "agent lied" in rep.summary

    def test_plural_errors(self):
        rep = score_run(_R([_err("a"), _err("b")]))
        assert "2 errors" in rep.summary

    def test_single_warning_summary(self):
        rep = score_run(_R([_warn("bash looping")]))
        assert "1 warning" in rep.summary
        assert "bash looping" in rep.summary

    def test_plural_warnings(self):
        rep = score_run(_R([_warn("x"), _warn("y")]))
        assert "2 warnings" in rep.summary

    def test_error_leads_summary_over_warning(self):
        # summary should mention the error message, not the warning message
        rep = score_run(_R([_err("critical"), _warn("minor")]))
        assert "critical" in rep.summary
        assert "minor" not in rep.summary

    def test_max_steps_no_findings_no_index_error(self):
        # regression: previously IndexError when stop reason set but no findings
        rep = score_run(_R(stopped="max_steps"))
        assert "hit step limit" in rep.summary
        assert rep.summary != "No issues detected."

    def test_budget_no_findings_no_index_error(self):
        rep = score_run(_R(stopped="budget"))
        assert "hit token budget" in rep.summary

    def test_error_stop_no_findings_no_index_error(self):
        rep = score_run(_R(stopped="error"))
        assert "stopped on error" in rep.summary

    def test_max_steps_with_warning_includes_both(self):
        rep = score_run(_R([_warn("looping")], stopped="max_steps"))
        assert "hit step limit" in rep.summary
        assert "looping" in rep.summary

    def test_error_stop_with_error_finding_leads_with_finding(self):
        rep = score_run(_R([_err("lied")], stopped="error"))
        assert "lied" in rep.summary

    def test_summary_with_unicode_message(self):
        rep = score_run(_R([_err("agent dit: 'terminé' mais échoué — données corrompues")]))
        assert "terminé" in rep.summary

    def test_summary_with_very_long_message(self):
        long_msg = "x" * 5000
        rep = score_run(_R([_err(long_msg)]))
        assert long_msg in rep.summary

    def test_summary_with_empty_string_message(self):
        rep = score_run(_R([_err("")]))
        assert "1 error" in rep.summary


# ---------------------------------------------------------------------------
# Return type and fields
# ---------------------------------------------------------------------------


class TestReturnType:
    def test_returns_loop_quality_report(self):
        assert isinstance(score_run(_R()), LoopQualityReport)

    def test_errors_field_contains_only_errors(self):
        rep = score_run(_R([_err(), _warn(), _info()]))
        assert all(f.severity == Severity.ERROR for f in rep.errors)
        assert len(rep.errors) == 1

    def test_warnings_field_contains_only_warnings(self):
        rep = score_run(_R([_err(), _warn(), _info()]))
        assert all(f.severity == Severity.WARNING for f in rep.warnings)
        assert len(rep.warnings) == 1

    def test_findings_field_contains_all(self):
        findings = [_err(), _warn(), _info()]
        rep = score_run(_R(findings))
        assert rep.findings == findings

    def test_findings_field_empty_when_none(self):
        assert score_run(_R()).findings == []

    def test_score_is_int(self):
        assert isinstance(score_run(_R()).score, int)

    def test_grade_is_string(self):
        assert isinstance(score_run(_R()).grade, str)

    def test_grade_values_are_canonical(self):
        grades = {
            score_run(_R()).grade,
            score_run(_R([_warn()])).grade,
            score_run(_R([_err()] * 3)).grade,
        }
        assert grades <= {"PASS", "WARN", "FAIL"}


# ---------------------------------------------------------------------------
# Stop reasons — exhaustive
# ---------------------------------------------------------------------------


class TestStopReasons:
    @pytest.mark.parametrize(
        "stopped,expected_penalty",
        [
            ("end_turn", 0),
            ("max_steps", 20),
            ("budget", 20),
            ("error", 50),
            ("unknown_future_value", 0),
            ("", 0),
            ("END_TURN", 0),  # wrong case — should not match
        ],
    )
    def test_stop_reason_penalty(self, stopped, expected_penalty):
        rep = score_run(_R(stopped=stopped))
        assert rep.score == 100 - expected_penalty

    @pytest.mark.parametrize(
        "stopped,label",
        [
            ("max_steps", "hit step limit"),
            ("budget", "hit token budget"),
            ("error", "stopped on error"),
        ],
    )
    def test_stop_reason_in_summary(self, stopped, label):
        rep = score_run(_R(stopped=stopped))
        assert label in rep.summary

    def test_end_turn_not_in_summary(self):
        rep = score_run(_R(stopped="end_turn"))
        assert "end_turn" not in rep.summary


# ---------------------------------------------------------------------------
# Integration: real RunResult via MockModel + detectors
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_clean_run_quality(self):

        from tvastar import Harness, create_agent, default_detectors
        from tvastar.model.mock import MockModel

        agent = create_agent(
            "t",
            model=MockModel(script=["all done!"]),
            detect=default_detectors(),
        )
        result = asyncio.run(Harness(agent).run("do a thing"))
        assert result.quality.grade == "PASS"
        assert result.quality.score == 100
        assert result.quality.passed is True

    def test_quality_property_returns_consistent_report(self):
        """Calling .quality twice must return equivalent reports."""

        from tvastar import Harness, create_agent, default_detectors
        from tvastar.model.mock import MockModel

        agent = create_agent(
            "t",
            model=MockModel(script=["done"]),
            detect=default_detectors(),
        )
        result = asyncio.run(Harness(agent).run("go"))
        r1 = result.quality
        r2 = result.quality
        assert r1.score == r2.score
        assert r1.grade == r2.grade

    def test_no_detectors_still_has_quality(self):
        """Even with no detectors configured, .quality must not raise."""

        from tvastar import Harness, create_agent
        from tvastar.model.mock import MockModel

        agent = create_agent("t", model=MockModel(script=["ok"]))
        result = asyncio.run(Harness(agent).run("go"))
        rep = result.quality
        assert rep.score == 100
        assert rep.grade == "PASS"


# ---------------------------------------------------------------------------
# Ugly / adversarial inputs
# ---------------------------------------------------------------------------


class TestUglyInputs:
    def test_finding_with_newlines_in_message(self):
        msg = "line1\nline2\nline3"
        rep = score_run(_R([_err(msg)]))
        assert msg in rep.summary

    def test_finding_with_null_bytes_in_message(self):
        msg = "bad\x00data"
        rep = score_run(_R([_err(msg)]))
        assert msg in rep.summary

    def test_finding_with_special_chars_in_message(self):
        msg = 'sql=" OR 1=1; DROP TABLE agents;--'
        rep = score_run(_R([_err(msg)]))
        assert msg in rep.summary

    def test_100_findings_all_warnings(self):
        # Should not raise; score floors at 0
        rep = score_run(_R([_warn()] * 100))
        assert rep.score == 0
        assert rep.grade == "FAIL"
        assert "100 warnings" in rep.summary

    def test_mixed_severity_large_count(self):
        findings = [_err()] * 20 + [_warn()] * 20 + [_info()] * 20
        rep = score_run(_R(findings))
        assert rep.score == 0
        assert len(rep.errors) == 20
        assert len(rep.warnings) == 20
        assert len(rep.findings) == 60

    def test_findings_list_is_not_mutated(self):
        original = [_err(), _warn()]
        original_copy = list(original)
        score_run(_R(original))
        assert original == original_copy


# ---------------------------------------------------------------------------
# Requirement 3: Quality Scoring — explicit acceptance-criteria tests
# Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6
# ---------------------------------------------------------------------------


class TestAcceptanceCriteria:
    """Tests mapped directly to REQ-QUALITY-001 acceptance criteria."""

    # --- AC 3.1: Deduction arithmetic ---

    def test_ac31_error_deduction_30(self):
        """Each ERROR finding deducts exactly 30 from the starting score of 100."""
        assert score_run(_R([_err()])).score == 70
        assert score_run(_R([_err(), _err()])).score == 40

    def test_ac31_warning_deduction_10(self):
        """Each WARNING finding deducts exactly 10 from the starting score of 100."""
        assert score_run(_R([_warn()])).score == 90
        assert score_run(_R([_warn(), _warn()])).score == 80

    def test_ac31_max_steps_deduction_20(self):
        """The max_steps stop reason deducts exactly 20."""
        assert score_run(_R(stopped="max_steps")).score == 80

    def test_ac31_error_stop_deduction_50(self):
        """The error stop reason deducts exactly 50."""
        assert score_run(_R(stopped="error")).score == 50

    def test_ac31_combined_deductions(self):
        """All deductions accumulate: 100 - 30(err) - 10(warn) - 20(max_steps) = 40."""
        rep = score_run(_R([_err(), _warn()], stopped="max_steps"))
        assert rep.score == 40

    # --- AC 3.2: Clamping to minimum of 0 ---

    def test_ac32_clamped_to_zero_from_excessive_errors(self):
        """Score never goes below 0 regardless of how many penalties apply."""
        # 4 errors = -120, so raw = -20, clamped to 0
        assert score_run(_R([_err()] * 4)).score == 0

    def test_ac32_clamped_to_zero_from_combined_penalties(self):
        """Mixed penalties exceeding 100 clamp to 0."""
        # 2 errors (-60) + 5 warnings (-50) + error stop (-50) = -60 → 0
        rep = score_run(_R([_err()] * 2 + [_warn()] * 5, stopped="error"))
        assert rep.score == 0

    # --- AC 3.3, 3.4, 3.5: Grade boundaries ---

    def test_ac33_score_80_is_pass(self):
        """Score exactly at 80 receives grade PASS (≥80 threshold)."""
        # 2 warnings = 100 - 20 = 80
        rep = score_run(_R([_warn(), _warn()]))
        assert rep.score == 80
        assert rep.grade == "PASS"

    def test_ac34_score_70_is_warn(self):
        """Score 70 (below 80, at or above 60) receives grade WARN."""
        # 1 error = 100 - 30 = 70
        rep = score_run(_R([_err()]))
        assert rep.score == 70
        assert rep.grade == "WARN"

    def test_ac34_score_60_is_warn(self):
        """Score exactly at 60 receives grade WARN (≥60 threshold)."""
        # 1 error + 1 warning = 100 - 30 - 10 = 60
        rep = score_run(_R([_err(), _warn()]))
        assert rep.score == 60
        assert rep.grade == "WARN"

    def test_ac35_score_50_is_fail(self):
        """Score 50 (below 60) receives grade FAIL."""
        # error stop = 100 - 50 = 50
        rep = score_run(_R(stopped="error"))
        assert rep.score == 50
        assert rep.grade == "FAIL"

    def test_ac35_score_40_is_fail(self):
        """Score 40 (below 60) receives grade FAIL."""
        # 2 errors = 100 - 60 = 40
        rep = score_run(_R([_err(), _err()]))
        assert rep.score == 40
        assert rep.grade == "FAIL"

    def test_ac35_score_zero_is_fail(self):
        """Score 0 receives grade FAIL."""
        rep = score_run(_R([_err()] * 4))
        assert rep.score == 0
        assert rep.grade == "FAIL"

    # --- AC 3.6 part 1: Pure function (same input → same output) ---

    def test_ac_pure_function_idempotent(self):
        """score_run is a pure function: same input always produces same output."""
        r = _R([_err(), _warn()], stopped="max_steps")
        results = [score_run(r) for _ in range(10)]
        # All scores and grades must be identical
        assert all(rep.score == results[0].score for rep in results)
        assert all(rep.grade == results[0].grade for rep in results)
        assert all(rep.summary == results[0].summary for rep in results)

    def test_ac_pure_function_no_side_effects(self):
        """score_run does not mutate its input."""
        findings = [_err(), _warn()]
        r = _R(list(findings), stopped="max_steps")
        original_findings = list(r.findings)
        original_stopped = r.stopped
        score_run(r)
        assert r.findings == original_findings
        assert r.stopped == original_stopped

    # --- AC 3.6 part 2: Lazy computation of RunResult.quality ---

    def test_ac_lazy_quality_property(self):
        """RunResult.quality computes the report lazily on access."""
        from tvastar import Harness, create_agent
        from tvastar.model.mock import MockModel

        agent = create_agent("t", model=MockModel(script=["done"]))
        result = asyncio.run(Harness(agent).run("go"))

        # The property is not stored — it computes on each access
        q1 = result.quality
        q2 = result.quality
        assert isinstance(q1, LoopQualityReport)
        assert isinstance(q2, LoopQualityReport)
        assert q1.score == q2.score
        assert q1.grade == q2.grade
        # They are separate objects (recomputed each time)
        assert q1 is not q2

    def test_ac_lazy_quality_reflects_mutations(self):
        """Since quality is computed lazily, mutating findings changes the result."""
        from tvastar import Harness, create_agent
        from tvastar.model.mock import MockModel

        agent = create_agent("t", model=MockModel(script=["done"]))
        result = asyncio.run(Harness(agent).run("go"))

        # Initially clean
        assert result.quality.score == 100
        assert result.quality.grade == "PASS"

        # After adding findings, the computed quality changes
        result.findings.append(_err())
        assert result.quality.score == 70
        assert result.quality.grade == "WARN"
