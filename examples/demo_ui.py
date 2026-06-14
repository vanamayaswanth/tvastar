"""
Quick demo: generates a sample trace then opens the UI.
Run: python run_ui_demo.py
"""

import json
import time
import uuid
import os
import sys


def mk_span(
    name, parent_id=None, duration_ms=None, status="ok", start=None, attributes=None, events=None
):
    t = start or time.time()
    return {
        "name": name,
        "span_id": uuid.uuid4().hex[:16],
        "parent_id": parent_id,
        "duration_ms": duration_ms,
        "status": status,
        "attributes": attributes or {},
        "events": events or [],
        "start": t,
    }


print("Generating demo trace...")
now = time.time()
spans = []

# Run 1 — coding agent
t = now - 300
p1 = mk_span(
    "session.prompt",
    start=t,
    duration_ms=8200,
    attributes={"session": "demo-session-1", "agent": "coding-assistant"},
)
spans += [
    p1,
    mk_span(
        "model.generate",
        parent_id=p1["span_id"],
        start=t + 0.1,
        duration_ms=1400,
        attributes={
            "gen_ai.usage.input_tokens": 842,
            "gen_ai.usage.output_tokens": 312,
            "gen_ai.response.finish_reasons": ["tool_use"],
        },
    ),
    mk_span(
        "tool.invoke",
        parent_id=p1["span_id"],
        start=t + 1.5,
        duration_ms=18,
        attributes={"tool": "read_file"},
        events=[
            {
                "name": "tool.result",
                "attributes": {"result": "def auth(u,p): return u==p  # TODO: bcrypt"},
            }
        ],
    ),
    mk_span(
        "model.generate",
        parent_id=p1["span_id"],
        start=t + 1.6,
        duration_ms=2100,
        attributes={
            "gen_ai.usage.input_tokens": 1240,
            "gen_ai.usage.output_tokens": 580,
            "gen_ai.response.finish_reasons": ["tool_use"],
        },
    ),
    mk_span(
        "tool.invoke",
        parent_id=p1["span_id"],
        start=t + 3.8,
        duration_ms=12,
        attributes={"tool": "write_file", "path": "auth.py"},
        events=[{"name": "tool.result", "attributes": {"result": "written 847 bytes"}}],
    ),
    mk_span(
        "tool.invoke",
        parent_id=p1["span_id"],
        start=t + 3.9,
        duration_ms=340,
        attributes={"tool": "bash", "command": "pytest tests/ -q"},
        events=[{"name": "tool.result", "attributes": {"result": "3 passed in 0.12s"}}],
    ),
    mk_span(
        "model.generate",
        parent_id=p1["span_id"],
        start=t + 4.3,
        duration_ms=890,
        attributes={
            "gen_ai.usage.input_tokens": 1680,
            "gen_ai.usage.output_tokens": 210,
            "gen_ai.response.finish_reasons": ["end_turn"],
        },
    ),
]

# Run 2 — tool error + warning
t = now - 180
p2 = mk_span(
    "session.prompt",
    start=t,
    duration_ms=4500,
    attributes={"session": "demo-session-2", "agent": "devops-agent"},
)
spans += [
    p2,
    mk_span(
        "model.generate",
        parent_id=p2["span_id"],
        start=t + 0.1,
        duration_ms=1200,
        attributes={
            "gen_ai.usage.input_tokens": 520,
            "gen_ai.usage.output_tokens": 180,
            "gen_ai.response.finish_reasons": ["tool_use"],
        },
    ),
    mk_span(
        "tool.invoke",
        parent_id=p2["span_id"],
        start=t + 1.4,
        duration_ms=2800,
        status="error: ToolError",
        attributes={"tool": "bash", "command": "docker build ."},
        events=[
            {"name": "tool.result", "attributes": {"result": "ERROR: failed to read Dockerfile"}}
        ],
    ),
    mk_span(
        "model.generate",
        parent_id=p2["span_id"],
        start=t + 4.3,
        duration_ms=890,
        attributes={
            "gen_ai.usage.input_tokens": 720,
            "gen_ai.usage.output_tokens": 160,
            "gen_ai.response.finish_reasons": ["end_turn"],
        },
    ),
    mk_span(
        "event.finding",
        parent_id=p2["span_id"],
        start=t + 4.2,
        duration_ms=1,
        attributes={
            "detector": "ignored_tool_error",
            "severity": "WARNING",
            "message": "Tool returned error but model did not retry",
        },
    ),
]

# Run 3 — long run with compaction
t = now - 60
p3 = mk_span(
    "session.prompt",
    start=t,
    duration_ms=22400,
    attributes={"session": "demo-session-3", "agent": "research-agent"},
)
spans.append(p3)
tools = ["bash", "read_file", "grep", "write_file", "bash"]
for i in range(5):
    spans.append(
        mk_span(
            "model.generate",
            parent_id=p3["span_id"],
            start=t + i * 4 + 0.1,
            duration_ms=1800 + i * 200,
            attributes={
                "gen_ai.usage.input_tokens": 2000 + i * 800,
                "gen_ai.usage.output_tokens": 400 + i * 80,
                "gen_ai.response.finish_reasons": ["tool_use" if i < 4 else "end_turn"],
            },
        )
    )
    if i < 4:
        spans.append(
            mk_span(
                "tool.invoke",
                parent_id=p3["span_id"],
                start=t + i * 4 + 1.9,
                duration_ms=50 + i * 20,
                attributes={"tool": tools[i]},
                events=[
                    {"name": "tool.result", "attributes": {"result": f"output from step {i + 1}"}}
                ],
            )
        )
spans.append(
    mk_span(
        "event.context_compacted",
        parent_id=p3["span_id"],
        start=t + 12,
        duration_ms=2,
        attributes={"messages_after": 12},
    )
)

trace_path = "tvastar-trace.jsonl"
with open(trace_path, "w") as f:
    for s in spans:
        f.write(json.dumps(s) + "\n")
print(f"✓ Demo trace written → {trace_path} ({len(spans)} spans, 3 runs)")

print("\nStarting Tvastar UI...")
sys.path.insert(0, os.path.dirname(__file__))
from tvastar.ui import run_ui  # noqa: E402

run_ui(trace_path, port=7878, auto_open=True)
