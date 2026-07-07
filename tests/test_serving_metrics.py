"""Tests for the /metrics Prometheus endpoint."""

import pytest

from tvastar.loop.metrics import MetricsCollector
from tvastar.serving.metrics import create_metrics_router


@pytest.fixture
def collector():
    return MetricsCollector()


@pytest.fixture
def client(collector):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(create_metrics_router(collector))
    return TestClient(app)


def test_metrics_returns_correct_content_type(client):
    """GET /metrics returns text/plain with Prometheus version parameter."""
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert "version=0.0.4" in resp.headers["content-type"]


def test_metrics_returns_collector_render_output(client, collector):
    """GET /metrics returns whatever the collector's render() produces."""
    from tvastar.loop import LoopEvent, LoopState

    # Feed some events to the collector so render() produces output
    collector(LoopEvent(loop_name="test-loop", run_id="run_1", state=LoopState.RUNNING, at=100.0))
    collector(LoopEvent(loop_name="test-loop", run_id="run_1", state=LoopState.PASS, at=105.0))

    resp = client.get("/metrics")
    assert resp.status_code == 200

    body = resp.text
    expected = collector.render()
    assert body == expected
    assert "tvastar_loop_runs_total" in body
    assert "tvastar_loop_passes_total" in body


def test_metrics_empty_collector(client):
    """GET /metrics with no events returns empty body (valid Prometheus response)."""
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.text == ""
