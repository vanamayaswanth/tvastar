#!/usr/bin/env python3
"""Quality gate — CI step that aggregates quality metrics and enforces threshold.

Measures:
  1. ruff lint (zero errors required)
  2. ruff format (zero diff required)
  3. test pass rate (100% required)
  4. score_pipeline quality score (TVASTAR_QUALITY_THRESHOLD, default 70)

Exits non-zero if any metric fails. Reports per-metric breakdown.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

THRESHOLD = int(os.environ.get("TVASTAR_QUALITY_THRESHOLD", "70"))

TARGETS = ["src/tvastar/", "examples/", "tests/"]


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True)


def check_ruff_lint() -> tuple[int, str]:
    """Return (score, detail). 100 if zero errors, 0 otherwise."""
    result = _run(["ruff", "check", *TARGETS])
    if result.returncode == 0:
        return 100, "0 errors"
    # Count error lines (ruff outputs one line per issue)
    lines = [l for l in result.stdout.strip().splitlines() if l and not l.startswith("Found")]
    count = len(lines)
    return 0, f"{count} error(s)"


def check_ruff_format() -> tuple[int, str]:
    """Return (score, detail). 100 if formatted, 0 otherwise."""
    result = _run(["ruff", "format", "--check", *TARGETS])
    if result.returncode == 0:
        return 100, "all files formatted"
    changed = [l for l in result.stdout.strip().splitlines() if l.strip()]
    return 0, f"{len(changed)} file(s) need formatting"


def check_tests() -> tuple[int, str]:
    """Return (score 0-100, detail) based on pytest pass rate."""
    result = _run(["pytest", "-q", "--tb=no"])
    # Parse pytest summary line like "42 passed, 1 failed"
    output = result.stdout + result.stderr
    passed = failed = 0
    for line in output.splitlines():
        if "passed" in line or "failed" in line:
            m_passed = re.search(r"(\d+) passed", line)
            m_failed = re.search(r"(\d+) failed", line)
            if m_passed:
                passed = int(m_passed.group(1))
            if m_failed:
                failed = int(m_failed.group(1))
    total = passed + failed
    if total == 0:
        # No tests found or couldn't parse — trust exit code
        if result.returncode == 0:
            return 100, "passed (no count parsed)"
        return 0, "failed (no count parsed)"
    score = int((passed / total) * 100)
    return score, f"{passed}/{total} passed"


def check_score_pipeline() -> tuple[int, str]:
    """Run score_pipeline quality tests and return (score, detail).

    Runs only the quality-related test file to get score_pipeline coverage.
    If it passes, the score is 100; if it fails, score is below threshold.
    """
    # ponytail: score_pipeline needs RunResult objects from harness runs.
    # The test suite exercises this. We run the quality tests specifically.
    result = _run(["pytest", "tests/test_score_pipeline.py", "-q", "--tb=no"])
    if result.returncode == 0:
        return 100, "score_pipeline tests pass"
    return THRESHOLD - 1, "score_pipeline tests failed"


def main() -> int:
    metrics: list[tuple[str, int, int]] = []  # (name, achieved, required)

    lint_score, lint_detail = check_ruff_lint()
    metrics.append(("ruff_lint", lint_score, 100))

    fmt_score, fmt_detail = check_ruff_format()
    metrics.append(("ruff_format", fmt_score, 100))

    test_score, test_detail = check_tests()
    metrics.append(("test_pass_rate", test_score, 100))

    pipeline_score, pipeline_detail = check_score_pipeline()
    metrics.append(("score_pipeline", pipeline_score, THRESHOLD))

    # Report
    print(f"\n{'='*60}")
    print(f"  QUALITY GATE (threshold: {THRESHOLD})")
    print(f"{'='*60}")
    print(f"{'Metric':<20} {'Achieved':>10} {'Required':>10} {'Delta':>10} {'Status':>8}")
    print(f"{'-'*60}")

    failed = False
    details = [lint_detail, fmt_detail, test_detail, pipeline_detail]
    for (name, achieved, required), detail in zip(metrics, details):
        delta = achieved - required
        status = "PASS" if achieved >= required else "FAIL"
        if achieved < required:
            failed = True
        print(f"{name:<20} {achieved:>10} {required:>10} {delta:>+10} {status:>8}")

    print(f"{'-'*60}")

    overall = min(m[1] for m in metrics)
    overall_status = "PASS" if not failed else "FAIL"
    print(f"{'OVERALL':<20} {overall:>10} {THRESHOLD:>10} {overall - THRESHOLD:>+10} {overall_status:>8}")
    print(f"{'='*60}\n")

    if failed:
        print("Quality gate FAILED — see metrics above.")
        return 1

    print("Quality gate PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
