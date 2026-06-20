"""
tvastar.ui.server — FastAPI backend for the local trace viewer.

Reads a JSONL trace file (produced by JSONLExporter) and exposes a REST API
that the single-page frontend queries. Also serves the frontend HTML.

Usage::

    tvastar ui                          # reads tvastar-trace.jsonl in cwd
    tvastar ui --trace my-run.jsonl
    tvastar ui --port 7878 --auto-open

Or programmatically::

    from tvastar.ui import run_ui
    run_ui("tvastar-trace.jsonl", port=7878)
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

__all__ = ["create_ui_app", "run_ui"]


# ---------------------------------------------------------------------------
# Trace parsing
# ---------------------------------------------------------------------------


def _load_spans(path: str) -> list[dict]:
    """Load all spans from a JSONL file."""
    spans = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        spans.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except FileNotFoundError:
        pass
    return spans


def _group_into_runs(spans: list[dict]) -> list[dict]:
    """
    Group flat spans into logical runs (session.prompt calls).
    Returns list of run dicts sorted by start time (newest first).
    """
    # Find all session.prompt spans — each is one run
    prompt_spans = [s for s in spans if s.get("name") == "session.prompt"]

    # Build a parent_id -> children index
    children: dict[str, list[dict]] = defaultdict(list)
    for span in spans:
        pid = span.get("parent_id")
        if pid:
            children[pid].append(span)

    runs = []
    for ps in prompt_spans:
        sid = ps.get("span_id", "")
        run_children = children.get(sid, [])

        # Collect steps: model.generate and tool.invoke children
        steps = []
        for child in sorted(run_children, key=lambda x: x.get("start", 0)):
            name = child.get("name", "")
            attrs = child.get("attributes", {})
            if name == "model.generate":
                steps.append(
                    {
                        "type": "model",
                        "start": child.get("start", 0),
                        "duration_ms": child.get("duration_ms"),
                        "status": child.get("status", "ok"),
                        "input_tokens": attrs.get("gen_ai.usage.input_tokens", 0),
                        "output_tokens": attrs.get("gen_ai.usage.output_tokens", 0),
                        "stop_reason": (
                            lambda r: (
                                r[0]
                                if isinstance(r, list) and r
                                else (r if isinstance(r, str) else None)
                            )
                        )(attrs.get("gen_ai.response.finish_reasons")),
                    }
                )
            elif name == "tool.invoke":
                result_preview = None
                for ev in child.get("events", []):
                    if ev.get("name") == "tool.result":
                        result_preview = str(ev.get("attributes", {}).get("result", ""))[:200]
                steps.append(
                    {
                        "type": "tool",
                        "tool": attrs.get("tool", "unknown"),
                        "start": child.get("start", 0),
                        "duration_ms": child.get("duration_ms"),
                        "status": child.get("status", "ok"),
                        "input": {k: v for k, v in attrs.items() if k not in ("tool",)},
                        "result_preview": result_preview,
                        "error": child.get("status", "ok").startswith("error"),
                    }
                )
            elif name.startswith("event."):
                event_kind = name[len("event.") :]
                steps.append(
                    {
                        "type": "event",
                        "kind": event_kind,
                        "start": child.get("start", 0),
                        "duration_ms": child.get("duration_ms"),
                        "data": attrs,
                    }
                )

        ps_attrs = ps.get("attributes", {})
        # Compute cost
        total_in = sum(s.get("input_tokens", 0) for s in steps if s["type"] == "model")
        total_out = sum(s.get("output_tokens", 0) for s in steps if s["type"] == "model")

        # Find findings from event.finding children
        findings = [s for s in steps if s["type"] == "event" and s["kind"] == "finding"]
        has_warning = any(f["data"].get("severity") in ("WARNING", "ERROR") for f in findings)

        runs.append(
            {
                "id": sid,
                "session": ps_attrs.get("session", "unknown"),
                "agent": ps_attrs.get("agent", "unknown"),
                "start": ps.get("start", 0),
                "duration_ms": ps.get("duration_ms"),
                "status": ps.get("status", "ok"),
                "step_count": len([s for s in steps if s["type"] in ("model", "tool")]),
                "tool_count": len([s for s in steps if s["type"] == "tool"]),
                "total_input_tokens": total_in,
                "total_output_tokens": total_out,
                "has_warning": has_warning,
                "findings": [f["data"] for f in findings],
                "steps": steps,
            }
        )

    # Sort newest first
    runs.sort(key=lambda r: r["start"], reverse=True)
    return runs


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


def create_ui_app(trace_path: str = "tvastar-trace.jsonl") -> Any:
    """Create the FastAPI UI application."""
    try:
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import HTMLResponse, JSONResponse
    except ImportError:
        raise ImportError(
            "tvastar[serve] is required for the UI. Run: pip install 'tvastar[serve]'"
        )

    app = FastAPI(title="Tvastar UI", docs_url=None, redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"])

    _trace_path = trace_path

    @app.get("/api/runs")
    def list_runs():
        spans = _load_spans(_trace_path)
        runs = _group_into_runs(spans)
        # Return summary without steps (for the list panel)
        summary = [{k: v for k, v in r.items() if k != "steps"} for r in runs]
        return JSONResponse({"runs": summary, "trace_path": _trace_path})

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        spans = _load_spans(_trace_path)
        runs = _group_into_runs(spans)
        for r in runs:
            if r["id"] == run_id:
                return JSONResponse(r)
        return JSONResponse({"error": "run not found"}, status_code=404)

    @app.get("/api/stats")
    def get_stats():
        spans = _load_spans(_trace_path)
        runs = _group_into_runs(spans)
        if not runs:
            return JSONResponse(
                {
                    "total_runs": 0,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "avg_steps": 0.0,
                    "warnings": 0,
                }
            )
        total_in = sum(r["total_input_tokens"] for r in runs)
        total_out = sum(r["total_output_tokens"] for r in runs)
        return JSONResponse(
            {
                "total_runs": len(runs),
                "total_input_tokens": total_in,
                "total_output_tokens": total_out,
                "avg_steps": sum(r["step_count"] for r in runs) / len(runs),
                "warnings": sum(1 for r in runs if r["has_warning"]),
            }
        )

    @app.get("/", response_class=HTMLResponse)
    def serve_ui():
        html_path = Path(__file__).parent / "index.html"
        return HTMLResponse(html_path.read_text(encoding="utf-8"))

    return app


def run_ui(
    trace_path: str = "tvastar-trace.jsonl",
    *,
    port: int = 7878,
    auto_open: bool = True,
) -> None:
    """Start the UI server. Blocks until interrupted."""
    try:
        import uvicorn
    except ImportError:
        raise ImportError("pip install 'tvastar[serve]'")

    # Resolve absolute path now so the server finds the file from any CWD
    abs_path = str(Path(trace_path).resolve())
    app = create_ui_app(abs_path)

    if not Path(abs_path).exists():
        print(f"\n  ⚠  Trace file not found: {abs_path}")
        print("     Run your agent with JSONLExporter first:")
        print(f"     harness = Harness(agent, tracer=Tracer([JSONLExporter('{trace_path}')]))")
        print()

    if auto_open:
        import threading
        import webbrowser

        def _open():
            time.sleep(1.2)
            webbrowser.open(f"http://localhost:{port}")

        threading.Thread(target=_open, daemon=True).start()

    print(f"\n  ⚡ Tvastar UI  →  http://localhost:{port}")
    print(f"  📄 Trace file  →  {abs_path}")
    print("  ↻  Auto-refreshes every 5s — open in browser, not as file\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
