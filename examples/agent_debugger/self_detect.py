"""Silent-failure self-detection for the Agent Debugger.

After each sub-agent run, the debugger runs Tvastar's detector suite against
the sub-agent's own trajectory. If an `unverified_completion` finding triggers,
the pipeline halts and the report is marked with a self-diagnosis warning.

Requirements: 10.1, 10.2, 10.3
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tvastar.detect import detect_from_messages
from tvastar.types import Message

from .schemas import FailureMode
from .detectors import _finding_to_failure_mode


@dataclass
class SelfDetectionResult:
    """Result of running self-detection against a sub-agent's trajectory.

    Attributes:
        findings: All failure modes detected in the sub-agent's own trajectory.
        has_unverified_completion: True if an `unverified_completion` detector
            triggered, indicating the sub-agent may have falsely claimed success.
        should_halt: True if the pipeline should halt due to a critical
            self-detection finding (currently: unverified_completion).
    """

    findings: list[FailureMode] = field(default_factory=list)
    has_unverified_completion: bool = False
    should_halt: bool = False


def run_self_detection(messages: list[Message]) -> SelfDetectionResult:
    """Run Tvastar detectors against a sub-agent's own trajectory.

    This is the core self-detection mechanism: after each sub-agent completes,
    we analyze its trajectory for silent failures. If the sub-agent's own run
    exhibits an `unverified_completion` pattern, the pipeline should halt to
    avoid propagating a false-positive result.

    Args:
        messages: The sub-agent's conversation trajectory (list of Message
            objects from the sub-agent's session).

    Returns:
        A SelfDetectionResult containing all findings, whether an
        unverified_completion was detected, and whether the pipeline
        should halt.
    """
    if not messages:
        return SelfDetectionResult()

    # Use the core detect_from_messages and convert to FailureMode
    raw_findings = detect_from_messages(messages)
    findings = [_finding_to_failure_mode(f, messages) for f in raw_findings]

    # Check if unverified_completion triggered
    has_unverified = any(f.detector == "unverified_completion" for f in findings)

    return SelfDetectionResult(
        findings=findings,
        has_unverified_completion=has_unverified,
        should_halt=has_unverified,
    )


def check_self_detection(result: SelfDetectionResult) -> bool:
    """Check whether the pipeline should halt based on self-detection results.

    Returns True if the pipeline should halt. Currently this triggers when
    `unverified_completion` is found in the sub-agent's own trajectory,
    indicating the sub-agent may have claimed success without verification.

    Args:
        result: The SelfDetectionResult from run_self_detection.

    Returns:
        True if the pipeline should halt (unverified_completion found),
        False otherwise.
    """
    return result.should_halt
