"""Integration tests for the HTTP serving layer (serving/http.py).

Verifies:
- POST /sessions/{id}/prompt returns expected response shape (Req 24.2)
- WS /sessions/{id}/stream for bidirectional streaming (Req 24.3)
- GET /sessions/{id}/stream for SSE-based streaming (Req 24.4)
- GET / returns agent health and info (Req 24.5)
- ImportError with install instructions when serve extra not installed (Req 24.6)
"""

from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest

# Skip entire module if fastapi/httpx not installed
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from tvastar.agent import AgentSpec
from tvastar.model.mock import MockModel
from tvastar.serving.http import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_spec() -> AgentSpec:
    """A minimal AgentSpec with a scripted MockModel for testing."""
    model = MockModel(script=["Hello from the agent!"])
    return AgentSpec(name="test-agent", model=model)


@pytest.fixture()
def client(mock_spec: AgentSpec) -> TestClient:
    """A FastAPI TestClient backed by a mock agent."""
    app = create_app(mock_spec)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Test: GET / returns agent health and info (Req 24.5)
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """Verify GET / returns agent framework info and health."""

    def test_root_returns_200(self, client: TestClient):
        r = client.get("/")
        assert r.status_code == 200

    def test_root_contains_framework_field(self, client: TestClient):
        data = client.get("/").json()
        assert data["framework"] == "tvastar"

    def test_root_contains_agent_name(self, client: TestClient):
        data = client.get("/").json()
        assert data["agent"] == "test-agent"

    def test_root_contains_model_name(self, client: TestClient):
        data = client.get("/").json()
        assert data["model"] == "mock"

    def test_root_contains_tools_list(self, client: TestClient):
        data = client.get("/").json()
        assert "tools" in data
        assert isinstance(data["tools"], list)

    def test_root_contains_skills_list(self, client: TestClient):
        data = client.get("/").json()
        assert "skills" in data
        assert isinstance(data["skills"], list)


# ---------------------------------------------------------------------------
# Test: POST /sessions/{id}/prompt returns expected response shape (Req 24.2)
# ---------------------------------------------------------------------------


class TestPromptEndpoint:
    """Verify POST /sessions/{id}/prompt response shape."""

    def test_prompt_returns_200(self, client: TestClient):
        r = client.post("/sessions/sess-1/prompt", json={"text": "Hello"})
        assert r.status_code == 200

    def test_prompt_response_contains_session_id(self, client: TestClient):
        data = client.post("/sessions/sess-1/prompt", json={"text": "Hi"}).json()
        assert data["session_id"] == "sess-1"

    def test_prompt_response_contains_text(self, client: TestClient):
        data = client.post("/sessions/sess-1/prompt", json={"text": "Hi"}).json()
        assert "text" in data
        assert isinstance(data["text"], str)
        assert len(data["text"]) > 0

    def test_prompt_response_contains_steps(self, client: TestClient):
        data = client.post("/sessions/sess-1/prompt", json={"text": "Hi"}).json()
        assert "steps" in data
        assert isinstance(data["steps"], int)
        assert data["steps"] >= 1

    def test_prompt_response_contains_stopped(self, client: TestClient):
        data = client.post("/sessions/sess-1/prompt", json={"text": "Hi"}).json()
        assert "stopped" in data
        assert data["stopped"] in ("end_turn", "max_steps", "budget", "error")

    def test_prompt_response_contains_usage(self, client: TestClient):
        data = client.post("/sessions/sess-1/prompt", json={"text": "Hi"}).json()
        assert "usage" in data
        usage = data["usage"]
        assert "input_tokens" in usage
        assert "output_tokens" in usage
        assert isinstance(usage["input_tokens"], int)
        assert isinstance(usage["output_tokens"], int)

    def test_prompt_with_scripted_response(self):
        """Verify the mock model's scripted response is returned."""
        model = MockModel(script=["Specific answer"])
        spec = AgentSpec(name="scripted", model=model)
        app = create_app(spec)
        cl = TestClient(app)
        data = cl.post("/sessions/s1/prompt", json={"text": "question"}).json()
        assert data["text"] == "Specific answer"

    def test_prompt_missing_text_returns_422(self, client: TestClient):
        """Missing required 'text' field returns validation error."""
        r = client.post("/sessions/sess-1/prompt", json={})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Test: WS /sessions/{id}/stream bidirectional streaming (Req 24.3)
# ---------------------------------------------------------------------------


class TestWebSocketStream:
    """Verify WS /sessions/{id}/stream bidirectional streaming."""

    def test_ws_connect_and_receive_done(self, client: TestClient):
        """WebSocket should accept connection and end with a done message."""
        with client.websocket_connect("/sessions/ws-1/stream") as ws:
            ws.send_json({"text": "Hello stream"})
            # Collect messages until we see "done"
            messages = []
            while True:
                msg = ws.receive_json()
                messages.append(msg)
                if msg.get("type") == "done":
                    break
            assert messages[-1]["type"] == "done"

    def test_ws_stream_events_have_type_and_data(self, client: TestClient):
        """Each streamed event should have 'type' and 'data' fields."""
        with client.websocket_connect("/sessions/ws-2/stream") as ws:
            ws.send_json({"text": "Test"})
            messages = []
            while True:
                msg = ws.receive_json()
                messages.append(msg)
                if msg.get("type") == "done":
                    break
            for msg in messages:
                assert "type" in msg
                assert "data" in msg

    def test_ws_stream_includes_text_content(self):
        """WebSocket stream should include text delta or turn_end with content."""
        model = MockModel(script=["Streamed response"])
        spec = AgentSpec(name="ws-test", model=model)
        app = create_app(spec)
        cl = TestClient(app)
        with cl.websocket_connect("/sessions/ws-3/stream") as ws:
            ws.send_json({"text": "Give me text"})
            messages = []
            while True:
                msg = ws.receive_json()
                messages.append(msg)
                if msg.get("type") == "done":
                    break
            # Should have at least one content event before done
            content_events = [
                m for m in messages if m["type"] in ("text_delta", "turn_end")
            ]
            assert len(content_events) > 0


# ---------------------------------------------------------------------------
# Test: GET /sessions/{id}/stream for SSE-based streaming (Req 24.4)
# ---------------------------------------------------------------------------


class TestSSEStream:
    """Verify GET /sessions/{id}/stream?text=... SSE endpoint."""

    def test_sse_returns_event_stream_content_type(self, client: TestClient):
        """SSE endpoint should return text/event-stream content type."""
        r = client.get("/sessions/sse-1/stream", params={"text": "Hello"})
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]

    def test_sse_response_contains_data_lines(self, client: TestClient):
        """SSE response should contain data: prefixed lines."""
        r = client.get("/sessions/sse-1/stream", params={"text": "Hello"})
        body = r.text
        data_lines = [line for line in body.splitlines() if line.startswith("data:")]
        assert len(data_lines) > 0

    def test_sse_ends_with_done(self, client: TestClient):
        """SSE stream should end with data: [DONE]."""
        r = client.get("/sessions/sse-2/stream", params={"text": "Test"})
        body = r.text
        data_lines = [line for line in body.splitlines() if line.startswith("data:")]
        assert data_lines[-1].strip() == "data: [DONE]"

    def test_sse_events_are_valid_json(self, client: TestClient):
        """All SSE events except [DONE] should be valid JSON with type and data."""
        r = client.get("/sessions/sse-3/stream", params={"text": "Parse me"})
        body = r.text
        data_lines = [line for line in body.splitlines() if line.startswith("data:")]
        for line in data_lines:
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                continue
            event = json.loads(payload)
            assert "type" in event
            assert "data" in event

    def test_sse_stream_with_scripted_model(self):
        """SSE stream should emit text content from scripted model."""
        model = MockModel(script=["SSE response text"])
        spec = AgentSpec(name="sse-test", model=model)
        app = create_app(spec)
        cl = TestClient(app)
        r = cl.get("/sessions/sse-4/stream", params={"text": "question"})
        body = r.text
        data_lines = [line for line in body.splitlines() if line.startswith("data:")]
        # Find text content in the events
        found_text = False
        for line in data_lines:
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                continue
            event = json.loads(payload)
            if event["type"] == "text_delta" and "text" in event.get("data", {}):
                found_text = True
                break
        assert found_text, "Should emit text_delta event with text content"


# ---------------------------------------------------------------------------
# Test: Session management endpoints
# ---------------------------------------------------------------------------


class TestSessionManagement:
    """Verify session creation and listing endpoints."""

    def test_create_session(self, client: TestClient):
        r = client.post("/sessions")
        assert r.status_code == 200
        data = r.json()
        assert "session_id" in data
        assert isinstance(data["session_id"], str)

    def test_list_sessions(self, client: TestClient):
        r = client.get("/sessions")
        assert r.status_code == 200
        data = r.json()
        assert "sessions" in data
        assert isinstance(data["sessions"], list)


# ---------------------------------------------------------------------------
# Test: ImportError with install instructions (Req 24.6)
# ---------------------------------------------------------------------------


class TestImportErrorPath:
    """Verify ImportError with install instructions when serve extra not installed."""

    def test_create_app_raises_when_fastapi_missing(self):
        """create_app should raise RuntimeError with install instructions."""
        with patch.dict(sys.modules, {"fastapi": None}):
            # Clear cached serving modules to force re-import
            mods_to_remove = [k for k in sys.modules if k.startswith("tvastar.serving")]
            saved = {k: sys.modules.pop(k) for k in mods_to_remove}
            try:
                from tvastar.serving.http import create_app as fresh_create_app
                from tvastar.model.mock import MockModel as MM
                from tvastar.agent import AgentSpec as AS

                spec = AS(name="test", model=MM())
                with pytest.raises((ImportError, RuntimeError)) as exc_info:
                    fresh_create_app(spec)
                error_msg = str(exc_info.value).lower()
                assert any(
                    kw in error_msg
                    for kw in ["serve", "fastapi", "pip install", "uv pip install"]
                ), f"Error should mention install instructions: {exc_info.value}"
            except ImportError as e:
                # Module-level import failure — also valid
                error_msg = str(e).lower()
                assert any(kw in error_msg for kw in ["serve", "fastapi"])
            finally:
                sys.modules.update(saved)

    def test_error_message_contains_package_name(self):
        """Error message should mention tvastar[serve] for user guidance."""
        with patch.dict(sys.modules, {"fastapi": None}):
            mods_to_remove = [k for k in sys.modules if k.startswith("tvastar.serving")]
            saved = {k: sys.modules.pop(k) for k in mods_to_remove}
            try:
                from tvastar.serving.http import create_app as fresh_create_app
                from tvastar.model.mock import MockModel as MM
                from tvastar.agent import AgentSpec as AS

                spec = AS(name="test", model=MM())
                with pytest.raises((ImportError, RuntimeError)) as exc_info:
                    fresh_create_app(spec)
                error_msg = str(exc_info.value)
                assert "tvastar[serve]" in error_msg or "serve" in error_msg.lower()
            except ImportError as e:
                assert "serve" in str(e).lower() or "fastapi" in str(e).lower()
            finally:
                sys.modules.update(saved)
