"""Tests for serving layer wiring — create_app() with registry includes loop endpoints.

Validates: Requirements 8.6, 11.4, 14.5
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient


def _mock_spec():
    """Minimal AgentSpec mock for create_app."""
    spec = MagicMock()
    spec.name = "test-agent"
    spec.model.name = "mock"
    spec.tools.names.return_value = []
    spec.skills.names.return_value = []
    return spec


def _mock_registry(loops=None):
    """Create a mock LoopRegistry."""
    registry = MagicMock()
    registry.all.return_value = loops or {}
    registry.get.return_value = None
    return registry


@patch("tvastar.serving.http.Harness")
@patch("tvastar.serving.http.FileStore")
def test_create_app_with_registry_has_health_route(mock_filestore, mock_harness):
    """When registry is provided, /health endpoint is registered."""
    from tvastar.serving.http import create_app

    spec = _mock_spec()
    registry = _mock_registry()

    app = create_app(spec, registry=registry)
    client = TestClient(app)

    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "healthy"
    assert data["loops"] == {}


@patch("tvastar.serving.http.Harness")
@patch("tvastar.serving.http.FileStore")
def test_create_app_with_registry_has_webhook_route(mock_filestore, mock_harness):
    """When registry is provided, /webhooks/{loop_name} endpoint is registered."""
    from tvastar.serving.http import create_app

    spec = _mock_spec()
    registry = _mock_registry()

    app = create_app(spec, registry=registry)
    client = TestClient(app)

    r = client.post("/webhooks/nonexistent", json={"x": 1})
    assert r.status_code == 404


@patch("tvastar.serving.http.Harness")
@patch("tvastar.serving.http.FileStore")
def test_create_app_with_metrics_collector_has_metrics_route(mock_filestore, mock_harness):
    """When registry and metrics_collector are provided, /metrics endpoint is registered."""
    from tvastar.loop.metrics import MetricsCollector
    from tvastar.serving.http import create_app

    spec = _mock_spec()
    registry = _mock_registry()
    collector = MetricsCollector()

    app = create_app(spec, registry=registry, metrics_collector=collector)
    client = TestClient(app)

    r = client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]


@patch("tvastar.serving.http.Harness")
@patch("tvastar.serving.http.FileStore")
def test_create_app_without_registry_no_health_route(mock_filestore, mock_harness):
    """When no registry is provided, /health endpoint is NOT registered."""
    from tvastar.serving.http import create_app

    spec = _mock_spec()

    app = create_app(spec)
    client = TestClient(app)

    r = client.get("/health")
    # Should get 404 (no such route) or similar — not 200
    assert r.status_code == 404


@patch("tvastar.serving.http.Harness")
@patch("tvastar.serving.http.FileStore")
def test_create_app_existing_routes_still_work_with_registry(mock_filestore, mock_harness):
    """Adding registry doesn't break existing routes."""
    from tvastar.serving.http import create_app

    spec = _mock_spec()
    registry = _mock_registry()

    app = create_app(spec, registry=registry)
    client = TestClient(app)

    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["agent"] == "test-agent"
