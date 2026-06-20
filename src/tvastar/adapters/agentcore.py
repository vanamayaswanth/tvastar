"""Wrap an AWS AgentCore (Bedrock Agents) session with Tvastar Loop Quality detection.

AWS AgentCore runs your agent loop in the cloud. This adapter wraps an
``invoke_agent`` call, collects the streaming response, and scores the
interaction with Tvastar's full detector suite.

Usage::

    import boto3
    from tvastar.adapters.agentcore import AgentCoreWrapper

    client = boto3.client("bedrock-agent-runtime", region_name="us-east-1")
    wrapper = AgentCoreWrapper(client)

    result = wrapper.invoke(
        agent_id="ABCDEF1234",
        agent_alias_id="TSTALIASID",
        session_id="session-42",
        input_text="Fix the failing tests and summarise what you changed.",
    )

    print(result.quality.score)   # 0–100
    print(result.quality.grade)   # "PASS" | "WARN" | "FAIL"
    print(result.text)            # collected assistant response text

Post-hoc scoring (if you already have the response)::

    from tvastar.adapters.agentcore import score_agentcore_response

    response = client.invoke_agent(...)
    result = score_agentcore_response(response)
    print(result.quality.grade)
"""

from __future__ import annotations

import time
from typing import Any, List, Optional

from ..detect import RunContext, default_detectors, run_detectors
from ..quality import score_run
from ..tools.base import ToolRegistry
from ..types import Message, TextBlock, ToolResultBlock, ToolUseBlock
from ..wrap import WrappedResult, _ScoringProxy

__all__ = ["AgentCoreWrapper", "score_agentcore_response"]


class AgentCoreWrapper:
    """Wrap an AWS AgentCore (Bedrock Agents) client to add Loop Quality detection.

    Calls ``bedrock-agent-runtime``'s ``invoke_agent``, collects the event
    stream into a text string, extracts any trace events (tool calls /
    results) present in the ``trace`` field, and scores the whole
    interaction.

    Args:
        client: A ``boto3`` ``bedrock-agent-runtime`` client.
        detectors: Detector callables. Defaults to
            :func:`~tvastar.detect.default_detectors`.
    """

    def __init__(self, client, *, detectors=None):
        self._client = client
        self._detectors = detectors if detectors is not None else default_detectors()

    def invoke(
        self,
        *,
        agent_id: str,
        agent_alias_id: str,
        session_id: str,
        input_text: str,
        **kwargs,
    ) -> WrappedResult:
        """Invoke the AgentCore agent and return a quality-scored WrappedResult.

        Args:
            agent_id: The Bedrock agent ID (e.g. ``"ABCDEF1234"``).
            agent_alias_id: The alias ID (e.g. ``"TSTALIASID"``).
            session_id: A stable session identifier for multi-turn use.
            input_text: The user message / prompt.
            **kwargs: Any additional keyword arguments passed through to
                ``boto3``'s ``invoke_agent`` call.

        Returns:
            :class:`~tvastar.wrap.WrappedResult`
        """
        t0 = time.monotonic()
        try:
            response = self._client.invoke_agent(
                agentId=agent_id,
                agentAliasId=agent_alias_id,
                sessionId=session_id,
                inputText=input_text,
                **kwargs,
            )
            text, messages, stopped = _parse_response(response, input_text=input_text)
        except Exception as exc:
            text = f"[error] {exc}"
            messages = []
            stopped = "error"
        duration = time.monotonic() - t0
        return _score(
            text,
            stopped,
            duration,
            self._detectors,
            messages=messages,
            raw={
                "agentId": agent_id,
                "sessionId": session_id,
                "inputText": input_text,
            },
        )


def score_agentcore_response(
    response: Any, *, detectors=None, input_text: str = ""
) -> WrappedResult:
    """Score an already-collected AgentCore ``invoke_agent`` response.

    Args:
        response: The raw response dict from ``bedrock-agent-runtime``'s
            ``invoke_agent`` call (the one containing a ``"completion"``
            event stream).
        detectors: Detector callables. Defaults to
            :func:`~tvastar.detect.default_detectors`.
        input_text: The original user input — used to populate the message
            history for detectors that inspect user turns.

    Returns:
        :class:`~tvastar.wrap.WrappedResult`
    """
    _detectors = detectors if detectors is not None else default_detectors()
    text, messages, stopped = _parse_response(response, input_text=input_text)
    return _score(text, stopped, 0.0, _detectors, messages=messages, raw=response)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _parse_response(response: Any, *, input_text: str) -> tuple[str, List[Message], str]:
    """Collect text and tool-trace events from a Bedrock agent event stream."""
    chunks: List[str] = []
    messages: List[Message] = []
    stopped = "end_turn"

    if input_text:
        messages.append(Message("user", [TextBlock(text=input_text)]))

    try:
        event_stream = (
            response.get("completion", response) if isinstance(response, dict) else response
        )
        for event in event_stream:
            if not isinstance(event, dict):
                continue

            # Final completion chunk
            if "chunk" in event:
                chunk = event["chunk"]
                raw_bytes = chunk.get("bytes")
                if raw_bytes is not None:
                    if isinstance(raw_bytes, (bytes, bytearray)):
                        chunks.append(raw_bytes.decode("utf-8", errors="replace"))
                    else:
                        chunks.append(str(raw_bytes))

            # Trace events carry tool-call and tool-result info
            elif "trace" in event:
                _extract_trace(event["trace"], messages)

    except Exception:
        stopped = "error"

    text = "".join(chunks)
    if text:
        messages.append(Message("assistant", [TextBlock(text=text)]))
    return text, messages, stopped


def _extract_trace(trace: dict, messages: List[Message]) -> None:
    """Pull tool invocations and results out of a Bedrock trace event."""
    orchestration = trace.get("orchestrationTrace") or {}

    # Tool invocation
    invocation = orchestration.get("invocationInput") or {}
    action = invocation.get("actionGroupInvocationInput") or {}
    if action:
        name = action.get("function") or action.get("actionGroupName") or "unknown_action"
        import json as _json

        raw_params = action.get("parameters") or action.get("requestBody") or {}
        try:
            inp = _json.loads(raw_params) if isinstance(raw_params, str) else raw_params
        except Exception:
            inp = {"_raw": str(raw_params)}
        messages.append(Message("assistant", [ToolUseBlock(name=str(name), input=inp, id="")]))

    # Tool result
    observation = orchestration.get("observation") or {}
    action_result = observation.get("actionGroupInvocationOutput") or {}
    if action_result:
        out_text = str(action_result.get("text") or "")
        is_error = "error" in out_text.lower()[:50]
        messages.append(
            Message("user", [ToolResultBlock(tool_use_id="", content=out_text, is_error=is_error)])
        )


def _score(
    text: str,
    stopped: str,
    duration: float,
    detectors: list,
    *,
    messages: Optional[List[Message]] = None,
    raw: Any = None,
) -> WrappedResult:
    if not messages:
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
