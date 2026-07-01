"""Tests for tvastar.quality.score_pipeline — pipeline-level quality scoring."""

from __future__ import annotations

from tvastar.detect import Finding, Severity
from tvastar.quality import LoopQualityReport, score_pipeline, score_run


# ---------------------------------------------------------------------------
# Minimal fake RunResult — only what score_run reads
# ---------------------------------------------------------------------------


class _R:
    """Minimal stand-in for RunResult — only the fields score_run reads."""

    def __init__(self, findings=None, stopped="end_turn"):
        self.findings = findings if findings is not None else []
        self.stopped = stopped


def _err(msg="agent lied", detector="unverified_completion"):
    return Finding(detector, Severity.ERROR, msg)


def _warn(msg="tool looping", detector="thrash_loop"):
    return Finding(detector, Severity.WARNING, msg)


def _info(msg="step count high", detector="step_limit"):
    return Finding(detector, Severity.INFO, msg)


# ---------------------------------------------------------------------------
# "worst" strategy (default)
# ---------------------------------------------------------------------------


class TestWorstStrategy:
    def test_returns_min_score(self):
        r1 = _R()  # score=100
        r2 = _R([_err()])  # score=70
        r3 = _R([_warn()])  # score=90
        report = score_pipeline([r1, r2, r3], strategy="worst")
        assert report.score == 70

    def test_single_result_equals_score_run(self):
        r = _R([_warn()])
        pipeline_report = score_pipeline([r], strategy="worst")
        single_report = score_run(r)
        assert pipeline_report.score == single_report.score

    def test_all_clean_results_score_100(self):
        results = [_R(), _R(), _R()]
        report = score_pipeline(results, strategy="worst")
        assert report.score == 100
        assert report.grade == "PASS"

    def test_empty_list_returns_clean_report(self):
        report = score_pipeline([], strategy="worst")
        assert report.score == 100
        assert report.grade == "PASS"
        assert report.findings == []
        assert report.summary == "No issues detected."


# ---------------------------------------------------------------------------
# "average" strategy
# ---------------------------------------------------------------------------


class TestAverageStrategy:
    def test_returns_mean_score(self):
        r1 = _R()  # score=100
        r2 = _R([_err()])  # score=70
        report = score_pipeline([r1, r2], strategy="average")
        assert report.score == 85  # (100 + 70) / 2 = 85

    def test_single_result_equals_score_run(self):
        r = _R([_err()])
        pipeline_report = score_pipeline([r], strategy="average")
        single_report = score_run(r)
        assert pipeline_report.score == single_report.score

    def test_all_clean_results_score_100(self):
        results = [_R(), _R(), _R()]
        report = score_pipeline(results, strategy="average")
        assert report.score == 100

    def test_truncates_to_int(self):
        # 100, 70, 90 → (100+70+90)/3 = 86.66... → 86
        r1 = _R()
        r2 = _R([_err()])
        r3 = _R([_warn()])
        report = score_pipeline([r1, r2, r3], strategy="average")
        assert report.score == 86
        assert isinstance(report.score, int)


# ---------------------------------------------------------------------------
# "all_pass" strategy
# ---------------------------------------------------------------------------


class TestAllPassStrategy:
    def test_returns_100_when_all_pass(self):
        # All results have grade PASS (score >= 80)
        r1 = _R()  # 100 → PASS
        r2 = _R([_warn()])  # 90 → PASS
        r3 = _R([_warn(), _warn()])  # 80 → PASS
        report = score_pipeline([r1, r2, r3], strategy="all_pass")
        assert report.score == 100

    def test_returns_min_when_any_fails(self):
        r1 = _R()  # 100 → PASS
        r2 = _R([_err()])  # 70 → WARN (not PASS)
        report = score_pipeline([r1, r2], strategy="all_pass")
        assert report.score == 70

    def test_single_passing_result_is_100(self):
        r = _R()
        report = score_pipeline([r], strategy="all_pass")
        assert report.score == 100

    def test_single_failing_result_returns_its_score(self):
        r = _R([_err()])  # 70 → WARN, not PASS
        report = score_pipeline([r], strategy="all_pass")
        assert report.score == 70


# ---------------------------------------------------------------------------
# Findings aggregation
# ---------------------------------------------------------------------------


class TestFindingsAggregation:
    def test_findings_aggregated_from_all_results(self):
        f1 = _err("error in step 1")
        f2 = _warn("warning in step 2")
        f3 = _info("info in step 3")
        r1 = _R([f1])
        r2 = _R([f2])
        r3 = _R([f3])
        report = score_pipeline([r1, r2, r3])
        assert f1 in report.findings
        assert f2 in report.findings
        assert f3 in report.findings
        assert len(report.findings) == 3

    def test_errors_list_only_contains_errors(self):
        r1 = _R([_err()])
        r2 = _R([_warn()])
        report = score_pipeline([r1, r2])
        assert all(f.severity == Severity.ERROR for f in report.errors)
        assert len(report.errors) == 1

    def test_warnings_list_only_contains_warnings(self):
        r1 = _R([_err()])
        r2 = _R([_warn()])
        report = score_pipeline([r1, r2])
        assert all(f.severity == Severity.WARNING for f in report.warnings)
        assert len(report.warnings) == 1


# ---------------------------------------------------------------------------
# Return type and grading
# ---------------------------------------------------------------------------


class TestReturnType:
    def test_returns_loop_quality_report(self):
        assert isinstance(score_pipeline([_R()]), LoopQualityReport)

    def test_grade_pass_when_score_gte_80(self):
        report = score_pipeline([_R(), _R([_warn()])], strategy="worst")
        # worst score = 90 → PASS
        assert report.grade == "PASS"

    def test_grade_warn_when_score_60_to_79(self):
        report = score_pipeline([_R([_err()])], strategy="worst")
        # score = 70 → WARN
        assert report.grade == "WARN"

    def test_grade_fail_when_score_below_60(self):
        report = score_pipeline([_R([_err(), _err()])], strategy="worst")
        # score = 40 → FAIL
        assert report.grade == "FAIL"

    def test_summary_has_content(self):
        report = score_pipeline([_R([_err("bad thing")])])
        assert "bad thing" in report.summary

    def test_no_findings_summary(self):
        report = score_pipeline([_R()])
        assert report.summary == "No issues detected."


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_invalid_strategy_raises(self):
        import pytest

        with pytest.raises(ValueError, match="Unknown strategy"):
            score_pipeline([_R()], strategy="invalid")

    def test_passed_property_works(self):
        report = score_pipeline([_R()])
        assert report.passed is True

        report2 = score_pipeline([_R([_err()])])
        assert report2.passed is False
