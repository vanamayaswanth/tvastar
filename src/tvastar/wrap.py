"""tvastar.wrap — add Loop Quality detection to ANY callable agent loop.

This is the entry point for using Tvastar as a quality layer on top of
whatever agent infrastructure you already run — AgentCore, LangGraph, raw
OpenAI loops, or anything else that returns a string or dict.

Usage::

    import tvastar

    # Decorator — wraps any async function
    @tvastar.wrap
    async def my_loop(prompt: str) -> str:
        return await some_external_agent(prompt)

    result = await my_loop("fix the tests")
    print(result.quality.score)    # 0–100
    print(result.quality.grade)    # "PASS" | "WARN" | "FAIL"
    print(result.text)             # final answer text

    # One-shot — wrap a callable inline
    result = await tvastar.wrap(some_agent)("fix tests")

    # With custom detectors
    @tvastar.wrap(detectors=[my_detector, *default_detectors()])
    async def my_loop(prompt: str) -> str: ...
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from .detect import Finding, RunContext, Severity, default_detectors, run_detectors
from .quality import LoopQualityReport, score_run
from .tools.base import ToolRegistry
from .types import Message, TextBlock

__all__ = ["wrap", "WrappedResult"]


@dataclass
class _ScoringProxy:
    """Minimal duck-type that satisfies score_run()'s interface."""

    findings: List[Finding]
    stopped: str


@dataclass
class WrappedResult:
    """Result from a wrapped external agent loop.

    Drop-in companion to Tvastar's own ``RunResult``: exposes the same
    ``.ok``, ``.findings``, and ``.quality`` attributes so you can write
    the same quality-gate code regardless of which agent framework ran.
    """

    text: str
    quality: LoopQualityReport
    findings: List[Finding]
    duration: float
    raw: Any = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        """True when grade is PASS and there are no ERROR-severity findings."""
        return self.quality.grade == "PASS"

    @property
    def warnings(self) -> List[Finding]:
        """Findings at WARNING or ERROR severity."""
        return [f for f in self.findings if f.severity in (Severity.WARNING, Severity.ERROR)]

    @property
    def errors(self) -> List[Finding]:
        """Findings at ERROR severity only."""
        return [f for f in self.findings if f.severity == Severity.ERROR]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def wrap(fn=None, *, detectors=None, extract_text=None):
    """Wrap any async (or sync) callable to add Loop Quality detection.

    Can be used as a plain decorator, a decorator factory, or a one-shot
    wrapper::

        # 1. Plain decorator
        @tvastar.wrap
        async def my_loop(prompt): ...

        # 2. Decorator factory (custom detectors)
        @tvastar.wrap(detectors=[my_detector, *default_detectors()])
        async def my_loop(prompt): ...

        # 3. One-shot
        result = await tvastar.wrap(some_fn)("prompt")

    Args:
        fn: The callable to wrap (omit when using as a factory).
        detectors: List of detector callables. Defaults to
            :func:`~tvastar.detect.default_detectors`.
        extract_text: ``(raw_return_value) -> str`` — how to pull the final
            answer text out of whatever the wrapped function returns.
            Defaults to inspecting common attribute/key names.

    Returns:
        A coroutine function (always async) that returns a
        :class:`WrappedResult`.
    """
    if fn is None:
        # Called as @wrap(...) factory
        def decorator(f: Callable) -> Callable:
            return _make_wrapper(f, detectors=detectors, extract_text=extract_text)

        return decorator

    # Called as @wrap (no parens) or wrap(fn)
    return _make_wrapper(fn, detectors=detectors, extract_text=extract_text)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_wrapper(fn: Callable, *, detectors, extract_text) -> Callable:
    _detectors = detectors if detectors is not None else default_detectors()
    _extract = extract_text or _default_extract_text

    async def _wrapped(*args, **kwargs):
        t0 = time.monotonic()
        try:
            if asyncio.iscoroutinefunction(fn):
                raw = await fn(*args, **kwargs)
            else:
                raw = await asyncio.to_thread(fn, *args, **kwargs)
            stopped = "end_turn"
        except Exception as exc:
            raw = None
            stopped = "error"
            _exc = exc
        else:
            _exc = None

        duration = time.monotonic() - t0
        text = _extract(raw) if raw is not None else (f"[error] {_exc}" if _exc else "")
        return _build_result(text, stopped, duration, _detectors, raw=raw)

    _wrapped.__name__ = getattr(fn, "__name__", "wrapped_loop")
    _wrapped.__wrapped__ = fn
    return _wrapped


def _build_result(
    text: str,
    stopped: str,
    duration: float,
    detectors: list,
    *,
    messages: Optional[List[Message]] = None,
    raw: Any = None,
) -> WrappedResult:
    """Score a completed run and build a WrappedResult."""
    if messages is None:
        messages = [Message("assistant", [TextBlock(text=text)])] if text else []

    ctx = RunContext(
        messages=messages,
        tools=ToolRegistry(),
        stopped=stopped,
        final_text=text,
    )
    findings = run_detectors(ctx, detectors)
    quality = score_run(_ScoringProxy(findings=findings, stopped=stopped))
    return WrappedResult(
        text=text,
        quality=quality,
        findings=findings,
        duration=duration,
        raw=raw,
    )


def _default_extract_text(raw: Any) -> str:
    """Pull final-answer text from common return types."""
    if isinstance(raw, str):
        return raw
    # Object with .text / .content / .output
    for attr in ("text", "content", "output", "result", "answer", "response"):
        val = getattr(raw, attr, None)
        if val is not None:
            return str(val)
    # Dict with common keys
    if isinstance(raw, dict):
        for key in ("text", "output", "result", "content", "answer", "response", "message"):
            if key in raw and raw[key]:
                return str(raw[key])
    return str(raw) if raw is not None else ""
