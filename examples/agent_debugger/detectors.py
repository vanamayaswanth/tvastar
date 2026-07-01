"""Detector runner for the Agent Debugger diagnosis phase.

Wraps Tvastar's built-in failure detectors and provides:
- run_all_detectors: runs the full default detector suite against a message list
- build_detector_dag: constructs a TaskGraph for parallel detector execution
- scan_for_injection: checks messages for prompt-injection patterns

Requirements: 2.1, 2.2, 2.6
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tvastar.detect import detect_from_messages, Finding
from tvastar.boundary import scan_messages_for_injection
from tvastar.types import Message

from .schemas import FailureMode

if TYPE_CHECKING:
    from tvastar.graph import TaskGraph
    from tvastar.harness import Harness


# ---------------------------------------------------------------------------
# Core: convert Finding -> FailureMode
# ---------------------------------------------------------------------------


def _finding_to_failure_mode(finding: Finding, messages: list[Message]) -> FailureMode:
    """Convert a Tvastar Finding into the agent debugger's FailureMode schema."""
    evidence: list[str] = []
    if finding.evidence:
        for key, val in finding.evidence.items():
            if isinstance(val, str):
                evidence.append(val[:200])
            elif isinstance(val, list):
                evidence.extend(str(v)[:200] for v in val[:3])

    line_range = (0, max(len(messages) - 1, 0))

    return FailureMode(
        detector=finding.detector,
        severity=finding.severity.value,
        message=finding.message,
        evidence=evidence,
        line_range=line_range,
    )


# ---------------------------------------------------------------------------
# Public API: run_all_detectors
# ---------------------------------------------------------------------------


def run_all_detectors(messages: list[Message]) -> list[FailureMode]:
    """Run all default Tvastar detectors against a message list.

    This is a thin wrapper around :func:`tvastar.detect.detect_from_messages`
    that converts the raw Finding objects into the agent debugger's FailureMode
    schema.

    Args:
        messages: The trajectory messages to analyze.

    Returns:
        A list of FailureMode instances for each detected issue.
    """
    if not messages:
        return []

    findings = detect_from_messages(messages)
    return [_finding_to_failure_mode(f, messages) for f in findings]


# ---------------------------------------------------------------------------
# Public API: build_detector_dag
# ---------------------------------------------------------------------------


def build_detector_dag(harness: "Harness") -> "TaskGraph":
    """Build a TaskGraph for parallel execution of independent detector groups.

    Detector groups are organized by independence:
    - Group A (tool-related): unknown_tool, schema_mismatch, ignored_tool_error
    - Group B (behavioral): thrash_loop, step_limit, empty_answer
    - Group C (security): prompt_injection
    - Group D (verification): unverified_completion

    Groups A, B, C run in parallel. Group D depends on none but is kept
    separate since it checks the final answer against tool results.
    An "aggregate" task depends on all groups to combine results.

    Args:
        harness: A Tvastar Harness instance to drive the task graph.

    Returns:
        A configured TaskGraph ready for execution.
    """
    from tvastar.graph import TaskGraph

    graph = TaskGraph(harness)

    # Independent detector groups run in parallel
    graph.task(
        "tool_detectors",
        "Run tool-related detectors: unknown_tool, schema_mismatch, ignored_tool_error. "
        "Analyze the trajectory for tool invocation issues.",
    )
    graph.task(
        "behavioral_detectors",
        "Run behavioral detectors: thrash_loop, step_limit, empty_answer. "
        "Analyze the trajectory for stuck loops, step limits, and empty outputs.",
    )
    graph.task(
        "security_detectors",
        "Run security detectors: prompt_injection. "
        "Scan the trajectory for prompt-injection patterns in tool results.",
    )
    graph.task(
        "verification_detectors",
        "Run verification detectors: unverified_completion. "
        "Check if the agent claims success despite evidence of failure.",
    )

    # Aggregation task depends on all detector groups
    graph.task(
        "aggregate",
        "Combine all detector findings into a unified diagnosis report.",
        depends_on=[
            "tool_detectors",
            "behavioral_detectors",
            "security_detectors",
            "verification_detectors",
        ],
    )

    return graph


# ---------------------------------------------------------------------------
# Public API: scan_for_injection
# ---------------------------------------------------------------------------


def scan_for_injection(messages: list[Message]) -> tuple[bool, list[str]]:
    """Scan messages for prompt-injection patterns.

    Thin wrapper around :func:`tvastar.boundary.scan_messages_for_injection`
    that returns a (is_adversarial, evidence) tuple for backward compatibility.

    Args:
        messages: The trajectory messages to scan.

    Returns:
        A tuple of (is_adversarial, evidence) where:
        - is_adversarial: True if any injection pattern was detected
        - evidence: list of strings describing each detected pattern
    """
    result = scan_messages_for_injection(messages)
    return (result.is_adversarial, result.evidence)
