"""The built-in detectors.

Each is a small, high-precision function over a :class:`RunContext`. They are
tuned to avoid false positives — a noisy detector that cries wolf is worse than
none. Thresholds are arguments so you can tune or wrap them.

Failure-mode taxonomy informed by prior art in agent observability;
implementation is original to Tvastar.
"""

from __future__ import annotations

import re

from .base import Finding, RunContext, Severity
from .jsonschema import validate

# Words a model uses when it believes it succeeded.
_SUCCESS_CLAIM = re.compile(
    r"\b(done|success(?:ful|fully)?|completed?|fixed|passing|passes|all tests pass|"
    r"works now|resolved|ready)\b",
    re.IGNORECASE,
)
# Signals in a tool result that contradict a success claim.
_FAILURE_SIGNAL = re.compile(
    r"\b(failed|failure|error|traceback|exception|assert(?:ion)?error|"
    r"\[exit [1-9]|exit code [1-9]|[1-9]\d* failed)\b",
    re.IGNORECASE,
)


def unknown_tool(ctx: RunContext) -> list[Finding]:
    """Model requested a tool that isn't registered (tool unavailability)."""
    out = []
    for call in ctx.tool_calls:
        if call.name not in ctx.tools:
            out.append(
                Finding(
                    "unknown_tool",
                    Severity.ERROR,
                    f"model called unregistered tool '{call.name}'",
                    {"tool": call.name},
                )
            )
    return out


def schema_mismatch(ctx: RunContext) -> list[Finding]:
    """Tool called with arguments that violate its declared input schema."""
    out = []
    for call in ctx.tool_calls:
        if call.name not in ctx.tools:
            continue
        schema = ctx.tools.get(call.name).input_schema
        errors = validate(call.input, schema)
        if errors:
            out.append(
                Finding(
                    "schema_mismatch",
                    Severity.ERROR,
                    f"'{call.name}' called with invalid arguments: {'; '.join(errors)}",
                    {"tool": call.name, "errors": errors, "args": call.input},
                )
            )
    return out


def thrash_loop(ctx: RunContext, *, threshold: int = 3) -> list[Finding]:
    """Same tool invoked with identical arguments >= threshold times — a sign
    the agent is stuck repeating itself instead of making progress."""
    import json
    import hashlib

    counts: dict[tuple, int] = {}
    for call in ctx.tool_calls:
        key = (call.name, hashlib.md5(json.dumps(call.input, sort_keys=True, default=str).encode()).hexdigest())
        counts[key] = counts.get(key, 0) + 1
    out = []
    for (name, _args), n in counts.items():
        if n >= threshold:
            out.append(
                Finding(
                    "thrash_loop",
                    Severity.WARNING,
                    f"tool '{name}' called {n}x with identical arguments",
                    {"tool": name, "count": n},
                )
            )
    return out


def ignored_tool_error(ctx: RunContext) -> list[Finding]:
    """The run ended normally even though the last tool call errored — the
    agent may have given up or ignored the failure."""
    last = ctx.last_tool_result
    if last is not None and last.is_error and ctx.stopped == "end_turn":
        return [
            Finding(
                "ignored_tool_error",
                Severity.WARNING,
                "run ended after a tool error without recovering",
                {"error": last.content[:200]},
            )
        ]
    return []


def unverified_completion(ctx: RunContext) -> list[Finding]:
    """The agent claims success, but the last tool output shows failure — the
    classic silent failure (e.g. 'all tests pass' over a red test run)."""
    if not _SUCCESS_CLAIM.search(ctx.final_text):
        return []
    last = ctx.last_tool_result
    if last is not None and (last.is_error or _FAILURE_SIGNAL.search(last.content)):
        return [
            Finding(
                "unverified_completion",
                Severity.ERROR,
                "final answer claims success but the last tool result shows failure",
                {"claim": ctx.final_text[:160], "evidence": last.content[:200]},
            )
        ]
    return []


def empty_answer(ctx: RunContext, *, min_len: int = 1) -> list[Finding]:
    """The run ended its turn with no real final answer."""
    if ctx.stopped == "end_turn" and len(ctx.final_text.strip()) < min_len:
        return [
            Finding(
                "empty_answer",
                Severity.WARNING,
                "run ended with an empty final answer",
            )
        ]
    return []


def prompt_injection(ctx: RunContext) -> list[Finding]:
    """A tool result contains content that *looks like* a prompt-injection
    attempt (e.g. "ignore previous instructions", a fake system turn, or a
    request to exfiltrate secrets).

    This is *detection, not prevention*: it surfaces suspicious tool output so a
    human can look. To reduce the model acting on it, fence untrusted content
    with :func:`tvastar.boundary.wrap_untrusted` when you feed it in.
    """
    from ..boundary import scan_for_injection

    out = []
    for ev in ctx.events:
        if ev.result is None:
            continue
        hits = scan_for_injection(ev.result.content)
        if hits:
            out.append(
                Finding(
                    "prompt_injection",
                    Severity.WARNING,
                    f"tool '{ev.call.name}' returned content matching injection "
                    f"patterns: {', '.join(hits)}",
                    {"tool": ev.call.name, "patterns": hits, "excerpt": ev.result.content[:200]},
                )
            )
    return out


def step_limit(ctx: RunContext) -> list[Finding]:
    """The agent hit its step ceiling without finishing — likely incomplete."""
    if ctx.stopped == "max_steps":
        return [
            Finding(
                "step_limit",
                Severity.WARNING,
                "run stopped at the step limit without reaching end_turn",
            )
        ]
    return []


def default_detectors() -> list:
    """The recommended high-precision detector suite."""
    return [
        unknown_tool,
        schema_mismatch,
        thrash_loop,
        ignored_tool_error,
        unverified_completion,
        prompt_injection,
        empty_answer,
        step_limit,
    ]
