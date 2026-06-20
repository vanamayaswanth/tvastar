"""Wrap a LangGraph graph with Tvastar Loop Quality detection.

The LangGraph adapter converts LangGraph's state dict output (including
LangChain ``AIMessage``, ``HumanMessage``, ``ToolMessage`` objects) into
Tvastar's message format so the full silent-failure detector suite can run.

Usage::

    from langgraph.graph import StateGraph
    from tvastar.adapters.langgraph import LangGraphWrapper

    # Compile your graph as normal
    graph = build_my_graph().compile()

    # Wrap it
    wrapped = LangGraphWrapper(graph)

    # Use it like graph.ainvoke — same state dict input
    result = await wrapped.ainvoke(
        {"messages": [HumanMessage(content="Fix the failing tests.")]}
    )
    print(result.quality.score)   # 0–100
    print(result.quality.grade)   # "PASS" | "WARN" | "FAIL"
    print(result.text)            # final answer text
    print(result.raw)             # original state dict from LangGraph

    # Sync variant
    result = wrapped.invoke({"messages": [HumanMessage(content="...")]})

Custom text/message extraction::

    def my_extract_text(state: dict) -> str:
        return state["custom_output_key"]

    wrapped = LangGraphWrapper(graph, extract_text=my_extract_text)
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from ..detect import RunContext, default_detectors, run_detectors
from ..quality import score_run
from ..tools.base import ToolRegistry
from ..types import Message, TextBlock, ToolResultBlock, ToolUseBlock
from ..wrap import WrappedResult, _ScoringProxy

__all__ = ["LangGraphWrapper"]


class LangGraphWrapper:
    """Wrap a compiled LangGraph graph to add Loop Quality detection.

    Converts LangGraph/LangChain message objects in the output state to
    Tvastar's internal format and runs the full detector suite on exit.

    Args:
        graph: A compiled LangGraph ``CompiledGraph`` (returned by
            ``StateGraph(...).compile()``).
        detectors: Detector callables. Defaults to
            :func:`~tvastar.detect.default_detectors`.
        extract_text: ``(state_dict) -> str`` — how to pull the final answer
            text from the output state. Defaults to checking common keys
            (``"output"``, ``"result"``, ``"answer"``) and the last
            ``AIMessage`` in ``state["messages"]``.
        extract_messages: ``(state_dict) -> list[Message]`` — how to convert
            the output state's message history to Tvastar Messages. Defaults
            to reading ``state["messages"]`` and converting LangChain
            message objects automatically.
    """

    def __init__(
        self,
        graph,
        *,
        detectors=None,
        extract_text: Optional[Callable[[Dict], str]] = None,
        extract_messages: Optional[Callable[[Dict], List[Message]]] = None,
    ):
        self._graph = graph
        self._detectors = detectors if detectors is not None else default_detectors()
        self._extract_text = extract_text or _default_extract_text
        self._extract_messages = extract_messages or _default_extract_messages

    async def ainvoke(self, state: Dict, **kwargs) -> WrappedResult:
        """Run the graph async and return a quality-scored WrappedResult."""
        t0 = time.monotonic()
        try:
            raw = await self._graph.ainvoke(state, **kwargs)
            stopped = "end_turn"
        except Exception as exc:
            raw = {}
            stopped = "error"
            return _score(f"[error] {exc}", stopped, time.monotonic() - t0, self._detectors, raw={})
        return self._score_state(raw, time.monotonic() - t0)

    def invoke(self, state: Dict, **kwargs) -> WrappedResult:
        """Run the graph synchronously and return a quality-scored WrappedResult."""
        t0 = time.monotonic()
        try:
            raw = self._graph.invoke(state, **kwargs)
        except Exception as exc:
            return _score(f"[error] {exc}", "error", time.monotonic() - t0, self._detectors, raw={})
        return self._score_state(raw, time.monotonic() - t0)

    def _score_state(self, raw: Dict, duration: float) -> WrappedResult:
        messages = self._extract_messages(raw)
        final_text = self._extract_text(raw)
        if not final_text and messages:
            # Fall back to last assistant message text
            for m in reversed(messages):
                if m.role == "assistant" and m.text:
                    final_text = m.text
                    break
        return _score(final_text, "end_turn", duration, self._detectors, messages=messages, raw=raw)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _score(
    text: str,
    stopped: str,
    duration: float,
    detectors: list,
    *,
    messages: Optional[List[Message]] = None,
    raw: Any = None,
) -> WrappedResult:
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


def _default_extract_text(state: Dict) -> str:
    """Try common LangGraph state keys for the final answer."""
    for key in ("output", "result", "answer", "response", "final_answer", "final_output"):
        val = state.get(key)
        if val:
            return str(val)
    # Try last message in messages list
    msgs = state.get("messages") or []
    for m in reversed(msgs):
        content = _get_content(m)
        role_name = type(m).__name__.lower()
        is_ai = (
            "ai" in role_name
            or getattr(m, "role", "") == "assistant"
            or (isinstance(m, dict) and m.get("role") == "assistant")
        )
        if is_ai and content:
            return content
    return ""


def _default_extract_messages(state: Dict) -> List[Message]:
    """Convert LangGraph/LangChain messages in state to Tvastar Messages."""
    raw_msgs = state.get("messages") or []
    out: List[Message] = []
    for m in raw_msgs:
        msg_type = type(m).__name__.lower()
        is_dict = isinstance(m, dict)

        if is_dict:
            role = m.get("role", "user")
            content = str(m.get("content") or "")
            if content:
                out.append(Message(role, [TextBlock(text=content)]))
            continue

        content = _get_content(m)
        tool_calls = getattr(m, "tool_calls", None) or []

        # HumanMessage / user — check before "ai" since "chain" in LangChain class
        # names contains the substring "ai", which would misclassify human messages.
        if "human" in msg_type or getattr(m, "role", "") == "user":
            if content:
                out.append(Message("user", [TextBlock(text=content)]))
            continue

        # ToolMessage → ToolResultBlock
        if "tool" in msg_type:
            tool_call_id = str(getattr(m, "tool_call_id", "") or "")
            is_error = bool(getattr(m, "is_error", False) or getattr(m, "status", "") == "error")
            out.append(
                Message(
                    "user",
                    [ToolResultBlock(tool_use_id=tool_call_id, content=content, is_error=is_error)],
                )
            )
            continue

        # AIMessage / assistant
        if "ai" in msg_type or getattr(m, "role", "") == "assistant":
            blocks = []
            if content:
                blocks.append(TextBlock(text=content))
            for tc in tool_calls:
                if isinstance(tc, dict):
                    name = tc.get("name", "unknown")
                    inp = tc.get("args") or tc.get("arguments") or {}
                    tid = str(tc.get("id") or "")
                else:
                    name = str(getattr(tc, "name", "unknown"))
                    inp = getattr(tc, "args", {}) or {}
                    tid = str(getattr(tc, "id", "") or "")
                blocks.append(ToolUseBlock(name=name, input=inp, id=tid))
            if blocks:
                out.append(Message("assistant", blocks))
            continue

        # Unknown role — best-effort user message
        if content:
            out.append(Message("user", [TextBlock(text=content)]))

    return out


def _get_content(m: Any) -> str:
    """Extract text content from a LangChain message object."""
    content = getattr(m, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    # content can be a list of dicts (multimodal)
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return " ".join(parts)
    return str(content)
