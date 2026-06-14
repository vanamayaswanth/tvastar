"""Tests for tvastar.ui — trace viewer server (0.7.0)."""

import json
import time
import uuid
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient


def _span(name, parent_id=None, duration_ms=100, status="ok", attrs=None, events=None, start=None):
    return {
        "name": name,
        "span_id": uuid.uuid4().hex[:16],
        "parent_id": parent_id,
        "duration_ms": duration_ms,
        "status": status,
        "attributes": attrs or {},
        "events": events or [],
        "start": start or time.time(),
    }


def _write_trace(path: Path, spans: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(s) for s in spans), encoding="utf-8")


@pytest.fixture()
def trace_file(tmp_path):
    p = tmp_path / "trace.jsonl"
    now = time.time()
    prompt = _span(
        "session.prompt",
        start=now,
        duration_ms=5000,
        attrs={"session": "s1", "agent": "test-agent"},
    )
    gen1 = _span(
        "model.generate",
        parent_id=prompt["span_id"],
        start=now + 0.1,
        duration_ms=1000,
        attrs={
            "gen_ai.usage.input_tokens": 500,
            "gen_ai.usage.output_tokens": 200,
            "gen_ai.response.finish_reasons": ["tool_use"],
        },
    )
    tool = _span(
        "tool.invoke",
        parent_id=prompt["span_id"],
        start=now + 1.2,
        duration_ms=50,
        attrs={"tool": "bash"},
        events=[{"name": "tool.result", "attributes": {"result": "hello"}}],
    )
    gen2 = _span(
        "model.generate",
        parent_id=prompt["span_id"],
        start=now + 1.3,
        duration_ms=800,
        attrs={
            "gen_ai.usage.input_tokens": 700,
            "gen_ai.usage.output_tokens": 150,
            "gen_ai.response.finish_reasons": ["end_turn"],
        },
    )
    _write_trace(p, [prompt, gen1, tool, gen2])
    return p


@pytest.fixture()
def client(trace_file):
    from tvastar.ui import create_ui_app

    app = create_ui_app(str(trace_file))
    return TestClient(app)


# ── /api/runs ────────────────────────────────────────────────────────────────


def test_list_runs_structure(client):
    r = client.get("/api/runs")
    assert r.status_code == 200
    data = r.json()
    assert "runs" in data
    assert len(data["runs"]) == 1
    run = data["runs"][0]
    assert run["agent"] == "test-agent"
    assert run["session"] == "s1"
    assert run["step_count"] == 3  # 2 model + 1 tool
    assert run["tool_count"] == 1
    assert run["total_input_tokens"] == 1200
    assert run["total_output_tokens"] == 350
    assert "steps" not in run  # list endpoint strips steps


def test_get_run_by_id(client, trace_file):
    runs = client.get("/api/runs").json()["runs"]
    run_id = runs[0]["id"]
    r = client.get(f"/api/runs/{run_id}")
    assert r.status_code == 200
    run = r.json()
    assert run["id"] == run_id
    assert "steps" in run
    assert len(run["steps"]) == 3


def test_get_run_not_found(client):
    r = client.get("/api/runs/doesnotexist")
    assert r.status_code == 404


# ── /api/stats ────────────────────────────────────────────────────────────────


def test_stats(client):
    r = client.get("/api/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["total_runs"] == 1
    assert data["total_input_tokens"] == 1200
    assert data["total_output_tokens"] == 350
    assert data["warnings"] == 0


def test_stats_empty_trace(tmp_path):
    from tvastar.ui import create_ui_app

    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    app = create_ui_app(str(empty))
    cl = TestClient(app)
    data = cl.get("/api/stats").json()
    assert data["total_runs"] == 0


# ── / (HTML) ──────────────────────────────────────────────────────────────────


def test_serves_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Tvastar UI" in r.text


# ── findings / warning flag ───────────────────────────────────────────────────


def test_warning_flag_set_for_finding(tmp_path):
    from tvastar.ui import create_ui_app

    p = tmp_path / "t.jsonl"
    now = time.time()
    prompt = _span("session.prompt", start=now, attrs={"session": "s", "agent": "a"})
    finding = _span(
        "event.finding",
        parent_id=prompt["span_id"],
        start=now + 0.1,
        attrs={"detector": "ignored_tool_error", "severity": "WARNING", "message": "bad"},
    )
    _write_trace(p, [prompt, finding])
    app = create_ui_app(str(p))
    cl = TestClient(app)
    runs = cl.get("/api/runs").json()["runs"]
    assert runs[0]["has_warning"] is True
    assert len(runs[0]["findings"]) == 1


# ── missing trace file ────────────────────────────────────────────────────────


def test_missing_trace_returns_empty(tmp_path):
    from tvastar.ui import create_ui_app

    app = create_ui_app(str(tmp_path / "nonexistent.jsonl"))
    cl = TestClient(app)
    data = cl.get("/api/runs").json()
    assert data["runs"] == []


# ── top-level import ──────────────────────────────────────────────────────────


def test_top_level_exports():
    from tvastar import create_ui_app, run_ui  # noqa: F401

    assert callable(create_ui_app)
    assert callable(run_ui)
