"""Generate the Tvastar Event JSON Schema v0.1.

Run this to produce spec/events.schema.json:
    python spec/generate_schema.py
"""

import json
from pathlib import Path

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://tvastar.dev/spec/events/v0.1",
    "title": "Tvastar Event",
    "description": (
        "A single event emitted by a Tvastar-compliant agent harness. "
        "Any implementation emitting this format is interoperable with "
        "Tvastar observability tooling."
    ),
    "type": "object",
    "required": ["spec_version", "event_type", "run_id", "step", "timestamp"],
    "properties": {
        "spec_version": {
            "type": "string",
            "const": "tvastar/0.1",
            "description": "Schema version. Consumers MUST check this before parsing.",
        },
        "event_type": {
            "type": "string",
            "enum": [
                "tool_call",
                "tool_result",
                "detection",
                "compaction",
                "approval",
                "task_start",
                "task_end",
                "loop_state_change",
            ],
            "description": "The kind of event being recorded.",
        },
        "loop_id": {
            "type": "string",
            "description": "Stable identifier for the loop instance (persists across runs).",
        },
        "run_id": {
            "type": "string",
            "description": "Unique identifier for this specific run/session.",
        },
        "step": {
            "type": "integer",
            "minimum": 0,
            "description": "Zero-indexed step number within the run.",
        },
        "timestamp": {
            "type": "string",
            "format": "date-time",
            "description": "ISO 8601 timestamp when the event occurred.",
        },
        "agent": {
            "type": "string",
            "description": "Name of the agent spec that generated this event.",
        },
        "model": {
            "type": "string",
            "description": "Model identifier used for this step.",
        },
        "tool": {
            "type": "object",
            "description": "Present on tool_call and tool_result events.",
            "properties": {
                "name": {"type": "string"},
                "input": {"type": "object"},
                "output": {"type": "string"},
                "is_error": {"type": "boolean"},
                "duration_ms": {"type": "number"},
            },
            "required": ["name"],
        },
        "verification": {
            "type": "object",
            "description": "Present on detection events. Result of post-execution verification.",
            "properties": {
                "result": {
                    "type": "string",
                    "enum": ["PASS", "FAIL", "UNVERIFIABLE"],
                },
                "detector": {
                    "type": "string",
                    "description": "Name of the detector that produced this finding.",
                },
                "severity": {
                    "type": "string",
                    "enum": ["info", "warning", "error"],
                },
                "message": {
                    "type": "string",
                    "description": "Human-readable description of the finding.",
                },
                "correction": {
                    "type": ["string", "null"],
                    "description": "Imperative correction injected into the agent, or null.",
                },
            },
            "required": ["result", "detector", "severity"],
        },
        "approval": {
            "type": "object",
            "description": "Present on approval events.",
            "properties": {
                "tool": {"type": "string"},
                "approved": {"type": "boolean"},
                "approver": {
                    "type": "string",
                    "description": "Who approved: 'human', 'model:<name>', or 'policy'.",
                },
                "reason": {"type": ["string", "null"]},
            },
            "required": ["tool", "approved", "approver"],
        },
        "compaction": {
            "type": "object",
            "description": "Present on compaction events.",
            "properties": {
                "messages_before": {"type": "integer"},
                "messages_after": {"type": "integer"},
                "trigger": {
                    "type": "string",
                    "enum": ["threshold", "overflow", "manual"],
                },
            },
            "required": ["messages_before", "messages_after", "trigger"],
        },
        "state_change": {
            "type": "object",
            "description": "Present on loop_state_change events.",
            "properties": {
                "from": {"type": "string"},
                "to": {"type": "string"},
            },
            "required": ["from", "to"],
        },
        "usage": {
            "type": "object",
            "description": "Token usage for this step.",
            "properties": {
                "input_tokens": {"type": "integer"},
                "output_tokens": {"type": "integer"},
            },
        },
        "cost_usd": {
            "type": "number",
            "minimum": 0,
            "description": "Estimated cost in USD for this step.",
        },
        "parent_span_id": {
            "type": ["string", "null"],
            "description": "For nested events (subtasks), the parent span ID.",
        },
    },
    "additionalProperties": True,
}

if __name__ == "__main__":
    out = Path(__file__).parent / "events.schema.json"
    out.write_text(json.dumps(SCHEMA, indent=2) + "\n", encoding="utf-8")
    print(f"Written: {out}")
