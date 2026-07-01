"""Tvastar failure-detection layer — catch silent agent failures in-process.

Detectors inspect a finished run's transcript and emit :class:`Finding`s
(attached to ``RunResult.findings``). Zero dependencies, never breaks a run.

    from tvastar.detect import default_detectors, Severity

Failure-mode taxonomy informed by prior art in agent observability; all code
here is original to Tvastar.
"""

from .base import (
    Detector,
    Finding,
    RunContext,
    Severity,
    ToolEvent,
    detect_from_messages,
    run_detectors,
)
from .detectors import (
    default_detectors,
    empty_answer,
    ignored_tool_error,
    prompt_injection,
    schema_mismatch,
    step_limit,
    thrash_loop,
    unknown_tool,
    unverified_completion,
)
from .jsonschema import validate

__all__ = [
    "Detector",
    "Finding",
    "RunContext",
    "Severity",
    "ToolEvent",
    "detect_from_messages",
    "run_detectors",
    "default_detectors",
    "validate",
    "unknown_tool",
    "schema_mismatch",
    "thrash_loop",
    "ignored_tool_error",
    "unverified_completion",
    "prompt_injection",
    "empty_answer",
    "step_limit",
]
