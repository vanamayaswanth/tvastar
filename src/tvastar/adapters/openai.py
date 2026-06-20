"""Wrap a raw OpenAI function-calling loop with Tvastar Loop Quality detection.

The OpenAI adapter gives you two entry points:

1. **Context-manager** (``OpenAILoopWrapper``) — you own the loop, Tvastar
   wraps it. Pass ``loop.messages`` as the messages list to your OpenAI calls;
   quality is scored when the ``with`` block exits.

2. **Post-hoc scorer** (``score_openai_messages``) — you've already run the
   loop and have the messages list. Pass it in and get a ``WrappedResult``.

Usage::

    from openai import OpenAI
    from tvastar.adapters.openai import OpenAILoopWrapper

    client = OpenAI()

    with OpenAILoopWrapper() as loop:
        loop.messages.append({"role": "user", "content": "Fix the failing tests."})
        while True:
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=loop.messages,
                tools=my_tools,
            )
            msg = resp.choices[0].message
            loop.messages.append(msg.model_dump())
            if resp.choices[0].finish_reason == "stop":
                break
            # handle tool calls ...

    result = loop.result
    print(result.quality.score)   # 0–100
    print(result.quality.grade)   # "PASS" | "WARN" | "FAIL"
    print(result.text)            # final assistant text

Post-hoc scoring::

    from tvastar.adapters.openai import score_openai_messages

    # messages is the list you passed to your OpenAI calls
    result = score_openai_messages(messages)
    print(result.quality.grade)
"""

from __future__ import annotations

import json
import time
from typing import Any, List, Optional

from ..detect import default_detectors, run_detectors
from ..quality import score_run
from ..tools.base import ToolRegistry
from ..types import Message, TextBlock, ToolResultBlock, ToolUseBlock
from ..wrap import WrappedResult, _ScoringProxy

__all__ = ["OpenAILoopWrapper", "score_openai_messages"]


class OpenAILoopWrapper:
    """Context-manager that records an OpenAI function-calling loop and scores it.

    Works as both a synchronous ``with`` and an async ``async with`` block.
    The ``messages`` list is yours to use directly — append to it as normal.
    Tvastar scores quality when the block exits.

    Args:
        detectors: Detector callables. Defaults to
            :func:`~tvastar.detect.default_detectors`.
        tool_names: Optional list of tool names the model had access to.
            Used to populate the ToolRegistry so ``unknown_tool`` and
            ``schema_mismatch`` can fire accurately.
    """

    def __init__(self, *, detectors=None, tool_names: Optional[List[str]] = None):
        self.messages: List[dict] = []
        self._detectors = detectors if detectors is not None else default_detectors()
        self._tool_names = tool_names or []
        self.result: Optional[WrappedResult] = None
        self._t0: float = 0.0

    # ---- sync context manager ----

    def __enter__(self) -> "OpenAILoopWrapper":
        self._t0 = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        stopped = "error" if exc_type else "end_turn"
        self.result = _score_messages(
            self.messages,
            stopped=stopped,
            duration=time.monotonic() - self._t0,
            detectors=self._detectors,
        )
        return False  # never suppress exceptions

    # ---- async context manager ----

    async def __aenter__(self) -> "OpenAILoopWrapper":
        return self.__enter__()

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return self.__exit__(exc_type, exc, tb)


def score_openai_messages(
    messages: List[dict],
    *,
    stopped: str = "end_turn",
    detectors=None,
) -> WrappedResult:
    """Score an already-completed OpenAI messages list.

    Pass the full ``messages`` list after your OpenAI loop finishes.
    Tvastar converts it to its internal format and runs the full detector
    suite (``unverified_completion``, ``thrash_loop``, ``ignored_tool_error``, …).

    Args:
        messages: The complete messages list from your OpenAI run.
        stopped: How the run ended: ``"end_turn"`` (normal), ``"max_steps"``,
            ``"budget"``, or ``"error"``.
        detectors: Detector callables. Defaults to
            :func:`~tvastar.detect.default_detectors`.

    Returns:
        :class:`~tvastar.wrap.WrappedResult` with ``.quality``, ``.findings``,
        ``.text``, and ``.ok``.
    """
    return _score_messages(
        messages,
        stopped=stopped,
        duration=0.0,
        detectors=detectors if detectors is not None else default_detectors(),
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _score_messages(
    messages: List[dict],
    *,
    stopped: str,
    duration: float,
    detectors: list,
) -> WrappedResult:
    tvastar_msgs = _convert_messages(messages)
    final_text = _extract_final_text(tvastar_msgs)

    ctx_messages = tvastar_msgs
    from ..detect import RunContext

    ctx = RunContext(
        messages=ctx_messages,
        tools=ToolRegistry(),
        stopped=stopped,
        final_text=final_text,
    )
    findings = run_detectors(ctx, detectors)
    quality = score_run(_ScoringProxy(findings=findings, stopped=stopped))
    return WrappedResult(
        text=final_text,
        quality=quality,
        findings=findings,
        duration=duration,
        raw=messages,
    )


def _convert_messages(messages: List[dict]) -> List[Message]:
    """Convert OpenAI message dicts (or message objects) to Tvastar Messages."""
    out: List[Message] = []
    for m in messages:
        # Accept both dicts and objects (e.g. openai.types.chat.ChatCompletionMessage)
        if not isinstance(m, dict):
            m = _obj_to_dict(m)

        role = m.get("role", "user")
        content = m.get("content") or ""
        tool_calls = m.get("tool_calls") or []

        if role == "assistant":
            blocks = []
            if content:
                blocks.append(TextBlock(text=str(content)))
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    tc = _obj_to_dict(tc)
                fn = tc.get("function") or {}
                if not isinstance(fn, dict):
                    fn = _obj_to_dict(fn)
                try:
                    inp = json.loads(fn.get("arguments") or "{}")
                except (json.JSONDecodeError, TypeError):
                    inp = {"_raw": str(fn.get("arguments", ""))}
                blocks.append(
                    ToolUseBlock(
                        name=str(fn.get("name") or "unknown"),
                        input=inp,
                        id=str(tc.get("id") or ""),
                    )
                )
            if blocks:
                out.append(Message("assistant", blocks))

        elif role == "tool":
            out.append(
                Message(
                    "user",
                    [
                        ToolResultBlock(
                            tool_use_id=str(m.get("tool_call_id") or ""),
                            content=str(content),
                            is_error=bool(m.get("is_error", False)),
                        )
                    ],
                )
            )

        elif role == "user" and content:
            out.append(Message("user", [TextBlock(text=str(content))]))

        # system messages are not part of the agent turn loop — skip

    return out


def _extract_final_text(messages: List[Message]) -> str:
    for m in reversed(messages):
        if m.role == "assistant" and m.text:
            return m.text
    return ""


def _obj_to_dict(obj: Any) -> dict:
    """Convert an openai SDK object to a plain dict via model_dump or __dict__."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return vars(obj) if hasattr(obj, "__dict__") else {}
