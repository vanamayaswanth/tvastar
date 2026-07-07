"""Tvastar Event Exporter — emits spec-conformant events (spec/events.schema.json).

This exporter translates internal Span objects into the standardized Tvastar
event format (v0.1). Any tool, dashboard, or analyzer that reads this format
is interoperable with any Tvastar implementation regardless of language.

Usage::

    from tvastar import Tracer
    from tvastar.event_exporter import TvastarEventExporter

    exporter = TvastarEventExporter("events.jsonl")
    tracer = Tracer(exporters=[exporter])

The output is JSONL — one spec-conformant JSON object per line.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from .observability import Span

_SPEC_VERSION = "tvastar/0.1"

# Map internal span names to event_type
_EVENT_TYPE_MAP = {
    "tool.execute": "tool_call",
    "tool.result": "tool_result",
    "detector.": "detection",
    "event.compaction": "compaction",
    "event.approval": "approval",
    "session.task": "task_start",
    "graph.task": "task_start",
    "event.loop_state": "loop_state_change",
}


def _classify_event(span_name: str) -> Optional[str]:
    """Map an internal span name to a spec event_type."""
    for prefix, event_type in _EVENT_TYPE_MAP.items():
        if span_name.startswith(prefix):
            return event_type
    return None


def _iso_timestamp(unix_ts: float) -> str:
    """Convert unix timestamp to ISO 8601."""
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat()


class TvastarEventExporter:
    """Emit spec-conformant Tvastar events to a JSONL file.

    Only spans that map to a recognized event_type are emitted.
    Unrecognized spans (internal tracing) are silently skipped.
    """

    def __init__(self, path: str = "tvastar-events.jsonl"):
        self.path = path

    def export(self, span: Span) -> None:
        event_type = _classify_event(span.name)
        if event_type is None:
            return  # not a spec-level event, skip

        event: dict[str, Any] = {
            "spec_version": _SPEC_VERSION,
            "event_type": event_type,
            "run_id": span.attributes.get("session", span.attributes.get("run_id", "")),
            "step": span.attributes.get("step", 0),
            "timestamp": _iso_timestamp(span.start),
        }

        # Optional common fields
        if "loop_id" in span.attributes:
            event["loop_id"] = span.attributes["loop_id"]
        if "agent" in span.attributes:
            event["agent"] = span.attributes["agent"]
        if "model" in span.attributes:
            event["model"] = span.attributes["model"]
        if span.parent_id:
            event["parent_span_id"] = span.parent_id

        # Tool events
        if event_type in ("tool_call", "tool_result"):
            tool_data: dict[str, Any] = {"name": span.attributes.get("tool", span.name)}
            if "input" in span.attributes:
                tool_data["input"] = span.attributes["input"]
            if "output" in span.attributes:
                tool_data["output"] = span.attributes["output"]
            if "is_error" in span.attributes:
                tool_data["is_error"] = span.attributes["is_error"]
            if span.duration_ms is not None:
                tool_data["duration_ms"] = span.duration_ms
            event["tool"] = tool_data

        # Detection events
        elif event_type == "detection":
            verification: dict[str, Any] = {
                "result": span.attributes.get("result", "UNVERIFIABLE"),
                "detector": span.attributes.get("detector.name", span.name),
                "severity": span.attributes.get("severity", "warning"),
            }
            if "message" in span.attributes:
                verification["message"] = span.attributes["message"]
            if "correction" in span.attributes:
                verification["correction"] = span.attributes["correction"]
            event["verification"] = verification

        # Approval events
        elif event_type == "approval":
            event["approval"] = {
                "tool": span.attributes.get("tool", ""),
                "approved": span.attributes.get("approved", False),
                "approver": span.attributes.get("approver", "human"),
                "reason": span.attributes.get("reason"),
            }

        # Compaction events
        elif event_type == "compaction":
            event["compaction"] = {
                "messages_before": span.attributes.get("messages_before", 0),
                "messages_after": span.attributes.get("messages_after", 0),
                "trigger": span.attributes.get("trigger", "threshold"),
            }

        # State change events
        elif event_type == "loop_state_change":
            event["state_change"] = {
                "from": span.attributes.get("from_state", ""),
                "to": span.attributes.get("to_state", ""),
            }

        # Usage and cost (from any event)
        if "input_tokens" in span.attributes or "output_tokens" in span.attributes:
            event["usage"] = {
                "input_tokens": span.attributes.get("input_tokens", 0),
                "output_tokens": span.attributes.get("output_tokens", 0),
            }
        if "cost_usd" in span.attributes:
            event["cost_usd"] = span.attributes["cost_usd"]

        # Write as JSONL
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, default=str) + "\n")
        except Exception:
            pass  # exporter failures must never break a run
