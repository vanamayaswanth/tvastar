"""Handoff policies — what happens when a loop exhausts its retries.

Werner principle: handoff is not fire-and-forget. It is persisted before
firing. If it fails it is retried. It is never silently dropped.
"""

from __future__ import annotations

import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from . import LoopRun


class HandoffPolicy(ABC):
    """Called when a loop exhausts retries and escalates to a human."""

    @abstractmethod
    async def escalate(self, run: "LoopRun", history: list["LoopRun"]) -> None: ...


@dataclass
class LogHandoff(HandoffPolicy):
    """Print a structured handoff report to stderr (or a custom stream).

    Default for development and when no handoff is configured.
    """

    stream: object = None  # defaults to sys.stderr at call time

    async def escalate(self, run: "LoopRun", history: list["LoopRun"]) -> None:
        out = self.stream or sys.stderr
        sep = "=" * 64
        lines = [
            "",
            sep,
            f"  LOOP HANDOFF REQUIRED: {run.loop_name}",
            sep,
            f"  Run ID   : {run.run_id}",
            f"  Iteration: {run.iteration}",
            f"  Time     : {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(run.started_at))}",
            f"  Duration : {f'{run.duration:.1f}s' if run.duration else 'unknown'}",
        ]
        if run.failure_kind:
            lines.append(f"  Kind     : {run.failure_kind}")
        if run.error:
            lines.append(f"  Error    : {run.error}")
        if run.findings:
            lines.append("  Findings :")
            for f in run.findings[:5]:
                lines.append(f"    [{f.severity}] {f.message}")
        if run.result_text:
            preview = run.result_text[:300].replace("\n", " ")
            lines.append(f"  Agent    : {preview}...")
        lines += [
            "",
            "  ACTION REQUIRED: A human must resolve this and call loop.reset()",
            sep,
            "",
        ]
        print("\n".join(lines), file=out)


@dataclass
class CallbackHandoff(HandoffPolicy):
    """Call an async function with (run, history).

    Example::

        async def my_handler(run, history):
            await slack.post(f"Loop {run.loop_name} needs help: {run.error}")

        loop = Loop(spec, LoopConfig(..., handoff=CallbackHandoff(my_handler)))
    """

    fn: Callable[["LoopRun", list["LoopRun"]], Awaitable[None]]

    async def escalate(self, run: "LoopRun", history: list["LoopRun"]) -> None:
        await self.fn(run, history)


@dataclass
class MultiHandoff(HandoffPolicy):
    """Fire multiple handoff policies in sequence. All are attempted even if one fails."""

    policies: list[HandoffPolicy] = field(default_factory=list)

    async def escalate(self, run: "LoopRun", history: list["LoopRun"]) -> None:
        errors: list[str] = []
        for policy in self.policies:
            try:
                await policy.escalate(run, history)
            except Exception as exc:
                errors.append(f"{type(policy).__name__}: {exc}")
        if errors:
            raise RuntimeError(
                f"MultiHandoff: {len(errors)} of {len(self.policies)} policies failed: "
                + "; ".join(errors)
            )


__all__ = [
    "HandoffPolicy",
    "LogHandoff",
    "CallbackHandoff",
    "MultiHandoff",
]
