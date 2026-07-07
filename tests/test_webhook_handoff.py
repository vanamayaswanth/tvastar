"""Tests for tvastar.loop.channels.webhook — WebhookHandoff."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tvastar.loop import FailureKind, LoopRun, LoopState
from tvastar.loop.channels.webhook import WebhookHandoff


_SENTINEL = object()


def _run(
    error: str | None = "something broke",
    failure_kind: FailureKind | None = FailureKind.LOGIC_ERROR,
    ended_at: float | object = _SENTINEL,
) -> LoopRun:
    started = 1700000000.0
    return LoopRun(
        run_id="run_abc123",
        loop_name="ci-sweeper",
        state=LoopState.HANDOFF,
        iteration=3,
        started_at=started,
        ended_at=started + 42.5 if ended_at is _SENTINEL else ended_at,
        failure_kind=failure_kind,
        error=error,
    )


@pytest.mark.asyncio
async def test_escalate_posts_correct_payload():
    """Verify the JSON payload contains all required fields."""
    hook = WebhookHandoff(url="https://example.com/hook")
    run = _run()

    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch.object(hook._httpx, "AsyncClient", return_value=mock_client):
        await hook.escalate(run, [])

    mock_client.post.assert_called_once()
    _, kwargs = mock_client.post.call_args
    payload = kwargs["json"]

    assert payload["loop_name"] == "ci-sweeper"
    assert payload["run_id"] == "run_abc123"
    assert payload["state"] == "handoff"
    assert payload["failure_kind"] == "logic_error"
    assert payload["error"] == "something broke"
    assert payload["duration_seconds"] == 42.5
    assert payload["iteration"] == 3
    assert "T" in payload["timestamp_utc"]  # ISO 8601


@pytest.mark.asyncio
async def test_escalate_truncates_error_to_2000():
    """Error field is capped at 2000 characters."""
    long_error = "x" * 5000
    hook = WebhookHandoff(url="https://example.com/hook")
    run = _run(error=long_error)

    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch.object(hook._httpx, "AsyncClient", return_value=mock_client):
        await hook.escalate(run, [])

    payload = mock_client.post.call_args[1]["json"]
    assert len(payload["error"]) == 2000


@pytest.mark.asyncio
async def test_escalate_null_fields_when_absent():
    """failure_kind and error are null when not set on run."""
    hook = WebhookHandoff(url="https://example.com/hook")
    run = _run(error=None, failure_kind=None, ended_at=None)

    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch.object(hook._httpx, "AsyncClient", return_value=mock_client):
        await hook.escalate(run, [])

    payload = mock_client.post.call_args[1]["json"]
    assert payload["failure_kind"] is None
    assert payload["error"] is None
    assert payload["duration_seconds"] is None


@pytest.mark.asyncio
async def test_escalate_custom_headers_merged():
    """Custom headers are included in the POST request."""
    hook = WebhookHandoff(
        url="https://example.com/hook",
        headers={"X-Custom": "value", "Authorization": "Bearer tok"},
    )
    run = _run()

    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch.object(hook._httpx, "AsyncClient", return_value=mock_client):
        await hook.escalate(run, [])

    headers = mock_client.post.call_args[1]["headers"]
    assert headers["Content-Type"] == "application/json"
    assert headers["X-Custom"] == "value"
    assert headers["Authorization"] == "Bearer tok"


@pytest.mark.asyncio
async def test_escalate_non_2xx_raises():
    """Non-2xx response raises RuntimeError with status code and body snippet."""
    hook = WebhookHandoff(url="https://example.com/hook")
    run = _run()

    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.text = "Service Unavailable" + "x" * 600

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch.object(hook._httpx, "AsyncClient", return_value=mock_client):
        with pytest.raises(RuntimeError, match="503"):
            await hook.escalate(run, [])


@pytest.mark.asyncio
async def test_escalate_timeout_raises():
    """30s timeout raises RuntimeError indicating timeout."""
    import httpx

    hook = WebhookHandoff(url="https://example.com/hook")
    run = _run()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch.object(hook._httpx, "AsyncClient", return_value=mock_client):
        with pytest.raises(RuntimeError, match="timed out after 30s"):
            await hook.escalate(run, [])


def test_import_error_without_httpx():
    """ImportError raised at construction when httpx is missing."""
    with patch.dict("sys.modules", {"httpx": None}):
        with patch("builtins.__import__", side_effect=ImportError("no httpx")):
            with pytest.raises(ImportError, match="Install tvastar\\[http\\]"):
                WebhookHandoff(url="https://example.com/hook")
