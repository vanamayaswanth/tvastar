"""Failure-detection core: findings, the run context detectors inspect, and
the runner that fans a transcript out across detectors.

This is Tvastar's lightweight answer to *silent failures* — agents that look like
they succeeded but didn't. Detectors are pure functions over a completed run's
transcript: cheap, in-process, dependency-free, and never able to break a run
(a detector that raises is isolated and reported, not propagated).

Originally written for Tvastar; the *taxonomy* of failure modes is informed by
prior art in agent observability, but no code is shared.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable, Optional

from ..types import Message, TextBlock, ToolResultBlock, ToolUseBlock

if TYPE_CHECKING:  # pragma: no cover
    from ..tools.base import ToolRegistry


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class Finding:
    """One detected (potential) silent failure."""

    detector: str
    severity: Severity
    message: str
    evidence: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.severity.value}] {self.detector}: {self.message}"


@dataclass
class ToolEvent:
    """A matched tool call + its result, in transcript order."""

    call: ToolUseBlock
    result: Optional[ToolResultBlock]
    step: int


@dataclass
class RunContext:
    """A read-only view of a finished run that detectors inspect."""

    messages: list[Message]
    tools: "ToolRegistry"
    stopped: str
    final_text: str

    @property
    def tool_calls(self) -> list[ToolUseBlock]:
        return [b for m in self.messages for b in m.blocks if isinstance(b, ToolUseBlock)]

    @property
    def tool_results(self) -> list[ToolResultBlock]:
        return [b for m in self.messages for b in m.blocks if isinstance(b, ToolResultBlock)]

    @property
    def events(self) -> list[ToolEvent]:
        """Pair each tool call with its result by tool_use_id, in order."""
        results = {r.tool_use_id: r for r in self.tool_results}
        out: list[ToolEvent] = []
        step = 0
        for m in self.messages:
            for b in m.blocks:
                if isinstance(b, ToolUseBlock):
                    step += 1
                    out.append(ToolEvent(call=b, result=results.get(b.id), step=step))
        return out

    @property
    def last_tool_result(self) -> Optional[ToolResultBlock]:
        results = self.tool_results
        return results[-1] if results else None


#: A detector takes a RunContext and returns any findings it sees.
Detector = Callable[[RunContext], list[Finding]]


def run_detectors(ctx: RunContext, detectors: list[Detector]) -> list[Finding]:
    """Run every detector, isolating failures so one bad detector can't break
    the run or hide the others' findings."""
    findings: list[Finding] = []
    for det in detectors:
        try:
            findings.extend(det(ctx) or [])
        except Exception as e:  # a detector must never break a run
            findings.append(
                Finding(
                    detector=getattr(det, "__name__", "detector"),
                    severity=Severity.INFO,
                    message=f"detector raised {type(e).__name__}: {e}",
                )
            )
    return findings


def _final_text(messages: list[Message]) -> str:
    for m in reversed(messages):
        if m.role == "assistant":
            text = "".join(b.text for b in m.blocks if isinstance(b, TextBlock))
            if text:
                return text
    return ""
