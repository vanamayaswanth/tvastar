"""Tests for the /health endpoint."""

import time
from dataclasses import dataclass, field

import pytest

from tvastar.loop import LoopRun, LoopState
from tvastar.loop.registry import LoopRegistry
from tvastar.serving.health import create_health_router


# -- Minimal fakes for testing without full Loop machinery --


@dataclass
class FakeConfig:
    name: str = "test-loop"
    goal: str = "test"
    schedule: str = "@manual"
    then: str | None = None
    metadata: dict = field(default_factory=dict)


class FakeLoop:
    """Minimal loop stub for health check tests."""

    def __init__(self, name: str, state: LoopState, schedule: str = "@manual", runs=None):
        self.name = name
        self.state = state
        self.config = FakeConfig(name=name, schedule=schedule)
        self._runs = runs or []

    def history(self, limit=50):
        return self._runs[-limit:]

    def on_event(self, fn):
        pass


@pytest.fixture
def registry():
    return LoopRegistry()


@pytest.fixture
def client(registry):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(create_health_router(registry))
    return TestClient(app)


def test_healthy_no_loops(client):
    """No loops registered → 200 with status healthy, empty loops mapping."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["loops"] == {}


def test_healthy_loop(client, registry):
    """A manual loop in IDLE state → healthy."""
    loop = FakeLoop("my-loop", LoopState.IDLE, schedule="@manual")
    registry._loops["my-loop"] = loop

    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["loops"]["my-loop"]["status"] == "healthy"


def test_unhealthy_suspended_loop(client, registry):
    """A SUSPENDED loop → 503 with unhealthy status."""
    loop = FakeLoop("broken", LoopState.SUSPENDED, schedule="@hourly")
    registry._loops["broken"] = loop

    resp = client.get("/health")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "unhealthy"
    assert data["loops"]["broken"]["status"] == "unhealthy"
    assert data["loops"]["broken"]["reason"] == "suspended"


def test_unhealthy_stale_loop(client, registry):
    """A scheduled loop with no recent success → 503 unhealthy."""
    # @hourly → interval=3600, threshold=10800s. Last success was 12000s ago.
    old_time = time.time() - 12000
    runs = [
        LoopRun(
            run_id="run_1",
            loop_name="stale",
            state=LoopState.PASS,
            iteration=1,
            started_at=old_time,
            ended_at=old_time + 5,
        )
    ]
    loop = FakeLoop("stale", LoopState.IDLE, schedule="@hourly", runs=runs)
    registry._loops["stale"] = loop

    resp = client.get("/health")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "unhealthy"
    assert data["loops"]["stale"]["status"] == "unhealthy"
    assert "no success" in data["loops"]["stale"]["reason"]


def test_healthy_scheduled_loop_with_recent_success(client, registry):
    """A scheduled loop with a recent success → 200 healthy."""
    recent_time = time.time() - 100  # 100s ago, well within 3*3600
    runs = [
        LoopRun(
            run_id="run_1",
            loop_name="fresh",
            state=LoopState.PASS,
            iteration=1,
            started_at=recent_time,
            ended_at=recent_time + 5,
        )
    ]
    loop = FakeLoop("fresh", LoopState.IDLE, schedule="@hourly", runs=runs)
    registry._loops["fresh"] = loop

    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["loops"]["fresh"]["status"] == "healthy"


def test_manual_loop_always_healthy(client, registry):
    """A manual loop is always healthy regardless of history."""
    loop = FakeLoop("manual", LoopState.IDLE, schedule="@manual")
    registry._loops["manual"] = loop

    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["loops"]["manual"]["status"] == "healthy"


def test_mixed_healthy_and_unhealthy(client, registry):
    """One healthy + one unhealthy → 503 overall."""
    healthy = FakeLoop("ok", LoopState.IDLE, schedule="@manual")
    unhealthy = FakeLoop("bad", LoopState.SUSPENDED, schedule="@daily")
    registry._loops["ok"] = healthy
    registry._loops["bad"] = unhealthy

    resp = client.get("/health")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "unhealthy"
    assert data["loops"]["ok"]["status"] == "healthy"
    assert data["loops"]["bad"]["status"] == "unhealthy"
