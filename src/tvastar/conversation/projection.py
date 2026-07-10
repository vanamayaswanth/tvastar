"""Projection — derive RunResult fields from the event log.

Pure functions: no side effects, no I/O beyond reading the already-persisted log.
Returns dicts (not RunResult directly) to avoid circular imports with session.py.
"""

from __future__ import annotations

import json
from typing import Any

from ..types import Message
from .reducer import reduce


def project_run_result(
    log: list[dict[str, Any]],
    *,
    messages: list[Message] | None = None,
) -> dict[str, Any]:
    """Pure function: derive RunResult fields from an event log.

    Returns dict with keys: text, messages, usage, steps, stopped, findings, cost.
    For partial projection (mid-run), missing fields are None.

    If messages is provided (in-process optimization), uses it directly
    instead of replaying via reducer. This avoids the extra read since
    Session already holds messages in memory.
    """
    # Derive messages: use provided or reduce from log
    if messages is not None:
        msgs = messages
    else:
        msgs = reduce(log)

    # Extract text from last assistant message
    text = _last_assistant_text(msgs)

    # Scan for run_end and step_complete records
    run_end_data: dict[str, Any] | None = None
    steps = 0

    for record in reversed(log):
        if not isinstance(record, dict):
            continue
        rtype = record.get("type")
        if rtype == "run_end" and run_end_data is None:
            run_end_data = record.get("data", {})
        elif rtype == "step_complete":
            steps = max(steps, record.get("data", {}).get("step", 0))

    # Partial projection: no run_end yet (mid-run for stop_predicate)
    if run_end_data is None:
        return {
            "text": text,
            "messages": msgs,
            "usage": None,
            "steps": steps or None,
            "stopped": None,
            "findings": None,
            "cost": None,
        }

    # Full projection from run_end
    usage_data = run_end_data.get("usage", {})
    usage = {
        "input_tokens": usage_data.get("input_tokens", 0),
        "output_tokens": usage_data.get("output_tokens", 0),
    }
    stopped = run_end_data.get("stopped", "end_turn")
    findings = run_end_data.get("findings", [])
    cost = run_end_data.get("cost")

    return {
        "text": text,
        "messages": msgs,
        "usage": usage,
        "steps": steps,
        "stopped": stopped,
        "findings": findings,
        "cost": cost,
    }


def project_fields_from_log(log: str | list[dict[str, Any]]) -> dict[str, Any]:
    """Extract only LoopRun-relevant fields from a persisted event log.

    Scans from the end for the last run_end record.
    Returns: {text, steps, stopped, findings} or {} if no run_end found.

    Args:
        log: Either a JSON string (from Store.get) or pre-parsed list of records.
    """
    if isinstance(log, str):
        try:
            log = json.loads(log)
        except (json.JSONDecodeError, TypeError):
            return {}

    if not log:
        return {}

    run_end_data: dict[str, Any] | None = None
    steps = 0

    for record in reversed(log):
        if not isinstance(record, dict):
            continue
        rtype = record.get("type")
        if rtype == "run_end" and run_end_data is None:
            run_end_data = record.get("data", {})
        elif rtype == "step_complete" and run_end_data is not None and steps == 0:
            # First step_complete before run_end (scanning backwards) = last step
            steps = record.get("data", {}).get("step", 0)

    if run_end_data is None:
        return {}

    msgs = reduce(log)
    return {
        "text": _last_assistant_text(msgs),
        "steps": steps,
        "stopped": run_end_data.get("stopped", "end_turn"),
        "findings": run_end_data.get("findings", []),
    }


def _last_assistant_text(messages: list[Message]) -> str:
    """Extract text from the last assistant message."""
    for msg in reversed(messages):
        if msg.role == "assistant":
            return msg.text
    return ""
