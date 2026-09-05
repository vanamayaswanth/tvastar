"""Integration tests for the HTTP serving layer (serving/http.py).

Verifies:
- POST /sessions/{id}/prompt returns expected response shape (Req 24.2)
- WS /sessions/{id}/stream for bidirectional streaming (Req 24.3)
- GET /sessions/{id}/stream for SSE-based streaming (Req 24.4)
- GET / returns agent health and info (Req 24.5)
- ImportError with install instructions when serve extra not installed (Req 24.6)
"""

from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import patch

import pytest

# Skip entire module if fastapi/httpx not installed
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from tvastar.agent import AgentSpec
from tvastar.memory.store import InMemoryStore
from tvastar.model.mock import MockModel
from tvastar.serving.http import Principal, create_app


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
            content_events = [m for m in messages if m["type"] in ("text_delta", "turn_end")]
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
            payload = line[len("data:") :].strip()
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
            payload = line[len("data:") :].strip()
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
# Test: optional serving hardening
# ---------------------------------------------------------------------------


def _test_authenticator(connection):
    """Read a deliberately simple test identity from an inbound header."""
    identity = connection.headers.get("x-test-principal")
    if not identity:
        return None
    tenant_id, subject_id = identity.split(":", 1)
    return Principal(tenant_id=tenant_id, subject_id=subject_id)


class TestServingHardening:
    """Verify additive authentication, ownership, limits, and serialization controls."""

    @staticmethod
    def _headers(tenant_id: str, subject_id: str = "user") -> dict[str, str]:
        return {"x-test-principal": f"{tenant_id}:{subject_id}"}

    def test_authentication_is_optional_but_required_when_configured(self, mock_spec: AgentSpec):
        app = create_app(mock_spec, authenticator=_test_authenticator)
        client = TestClient(app)

        assert client.get("/sessions").status_code == 401
        assert client.post("/sessions", headers=self._headers("tenant-a")).status_code == 200

    def test_authenticated_sessions_are_tenant_scoped_and_unknown_ids_are_denied(
        self, mock_spec: AgentSpec
    ):
        app = create_app(mock_spec, store=InMemoryStore(), authenticator=_test_authenticator)
        client = TestClient(app)
        tenant_a = self._headers("tenant-a", "alice")
        tenant_b = self._headers("tenant-b", "bob")

        session_id = client.post("/sessions", headers=tenant_a).json()["session_id"]
        assert (
            client.post(
                f"/sessions/{session_id}/prompt", headers=tenant_a, json={"text": "Hi"}
            ).status_code
            == 200
        )

        tenant_a_sessions = client.get("/sessions", headers=tenant_a).json()["sessions"]
        assert [session["id"] for session in tenant_a_sessions] == [session_id]
        assert client.get("/sessions", headers=tenant_b).json()["sessions"] == []
        assert (
            client.post(
                f"/sessions/{session_id}/prompt", headers=tenant_b, json={"text": "Hi"}
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/sessions/unknown-session/prompt", headers=tenant_a, json={"text": "Hi"}
            ).status_code
            == 404
        )

    def test_session_owner_survives_app_recreation(self, mock_spec: AgentSpec):
        store = InMemoryStore()
        tenant_a = self._headers("tenant-a", "alice")
        first = TestClient(create_app(mock_spec, store=store, authenticator=_test_authenticator))
        session_id = first.post("/sessions", headers=tenant_a).json()["session_id"]
        assert (
            first.post(
                f"/sessions/{session_id}/prompt", headers=tenant_a, json={"text": "Persist me"}
            ).status_code
            == 200
        )

        second = TestClient(create_app(mock_spec, store=store, authenticator=_test_authenticator))
        assert (
            second.post(
                f"/sessions/{session_id}/prompt", headers=tenant_a, json={"text": "Resume me"}
            ).status_code
            == 200
        )
        assert (
            second.post(
                f"/sessions/{session_id}/prompt",
                headers=self._headers("tenant-b", "bob"),
                json={"text": "No access"},
            ).status_code
            == 404
        )

    def test_prompt_limit_applies_to_http_and_sse(self, mock_spec: AgentSpec):
        client = TestClient(create_app(mock_spec, max_prompt_size=3))

        assert client.post("/sessions/limited/prompt", json={"text": "four"}).status_code == 413
        assert client.get("/sessions/limited/stream", params={"text": "four"}).status_code == 413
        assert client.post("/sessions/limited/prompt", json={"text": "fit"}).status_code == 200

    def test_completed_run_releases_the_active_run_permit(self, mock_spec: AgentSpec):
        client = TestClient(create_app(mock_spec, max_active_runs=1))

        assert client.post("/sessions/one/prompt", json={"text": "First"}).status_code == 200
        assert client.post("/sessions/two/prompt", json={"text": "Second"}).status_code == 200

    async def test_active_run_limit_rejects_parallel_work_and_releases(self):
        from httpx import ASGITransport, AsyncClient

        started = asyncio.Event()
        release = asyncio.Event()

        class BlockingModel(MockModel):
            async def generate(self, messages, **kwargs):
                started.set()
                await release.wait()
                return await super().generate(messages, **kwargs)

        app = create_app(AgentSpec(name="blocked", model=BlockingModel()), max_active_runs=1)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            active = asyncio.create_task(
                client.post("/sessions/active/prompt", json={"text": "Hold the permit"})
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            assert (
                await client.post("/sessions/rejected/prompt", json={"text": "Reject me"})
            ).status_code == 429
            release.set()
            assert (await active).status_code == 200
            assert (
                await client.post("/sessions/reused/prompt", json={"text": "Run again"})
            ).status_code == 200

    async def test_same_session_runs_are_serialized(self):
        from httpx import ASGITransport, AsyncClient

        first_started = asyncio.Event()
        release = asyncio.Event()

        class SerialModel(MockModel):
            def __init__(self):
                super().__init__()
                self.starts = 0

            async def generate(self, messages, **kwargs):
                self.starts += 1
                if self.starts == 1:
                    first_started.set()
                    await release.wait()
                return await super().generate(messages, **kwargs)

        model = SerialModel()
        app = create_app(AgentSpec(name="serial", model=model))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = asyncio.create_task(
                client.post("/sessions/serial/prompt", json={"text": "First"})
            )
            await asyncio.wait_for(first_started.wait(), timeout=1)
            second = asyncio.create_task(
                client.post("/sessions/serial/prompt", json={"text": "Second"})
            )
            await asyncio.sleep(0.01)
            assert model.starts == 1
            release.set()
            assert (await first).status_code == 200
            assert (await second).status_code == 200
            assert model.starts == 2

    def test_sse_and_websocket_release_the_active_run_permit(self, mock_spec: AgentSpec):
        client = TestClient(create_app(mock_spec, max_active_runs=1))

        assert client.get("/sessions/sse/stream", params={"text": "SSE"}).status_code == 200
        with client.websocket_connect("/sessions/ws/stream") as ws:
            ws.send_json({"text": "WebSocket"})
            while ws.receive_json()["type"] != "done":
                pass
        assert (
            client.post("/sessions/after/prompt", json={"text": "After streams"}).status_code == 200
        )

    def test_invalid_serving_limits_are_rejected(self, mock_spec: AgentSpec):
        with pytest.raises(ValueError, match="max_prompt_size"):
            create_app(mock_spec, max_prompt_size=-1)
        with pytest.raises(ValueError, match="max_active_runs"):
            create_app(mock_spec, max_active_runs=0)


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
                    kw in error_msg for kw in ["serve", "fastapi", "pip install", "uv pip install"]
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


class TestAuthenticatedSessionSubjectOwnership:
    """A tenant principal may access only its own subject's serving sessions."""

    def test_subject_scoping_applies_to_http_sse_websocket_and_list(self, mock_spec: AgentSpec):
        from starlette.websockets import WebSocketDisconnect

        client = TestClient(
            create_app(mock_spec, store=InMemoryStore(), authenticator=_test_authenticator)
        )
        alice = {"x-test-principal": "tenant-a:alice"}
        mallory = {"x-test-principal": "tenant-a:mallory"}
        session_id = client.post("/sessions", headers=alice).json()["session_id"]

        assert [
            item["id"] for item in client.get("/sessions", headers=alice).json()["sessions"]
        ] == [session_id]
        assert client.get("/sessions", headers=mallory).json()["sessions"] == []
        assert (
            client.post(
                f"/sessions/{session_id}/prompt", headers=mallory, json={"text": "no"}
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/sessions/{session_id}/stream", headers=mallory, params={"text": "no"}
            ).status_code
            == 404
        )
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/sessions/{session_id}/stream", headers=mallory):
                pass

    def test_created_session_routes_from_another_app_before_first_run(self, mock_spec: AgentSpec):
        store = InMemoryStore()
        headers = {"x-test-principal": "tenant-a:alice"}
        first = TestClient(create_app(mock_spec, store=store, authenticator=_test_authenticator))
        session_id = first.post("/sessions", headers=headers).json()["session_id"]

        second = TestClient(create_app(mock_spec, store=store, authenticator=_test_authenticator))
        assert (
            second.post(
                f"/sessions/{session_id}/prompt", headers=headers, json={"text": "resume"}
            ).status_code
            == 200
        )
