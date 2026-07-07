"""Tests for tvastar.loop.channels.pagerduty — PagerDuty handoff channel."""

from __future__ import annotations

import time
from unittest.mock import patch

import httpx
import pytest

from tvastar.loop import FailureKind, LoopRun, LoopState
from tvastar.loop.channels.pagerduty import PagerDutyHandoff


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(
    state: LoopState = LoopState.HANDOFF,
    failure_kind: FailureKind | None = FailureKind.TIMEOUT,
    error: str | None = "timed out after 30s",
    run_id: str = "run-123",
    loop_name: str = "my-loop",
) -> LoopRun:
    return LoopRun(
        run_id=run_id,
        loop_name=loop_name,
        state=state,
        iteration=1,
        started_at=time.time(),
        failure_kind=failure_kind,
        error=error,
    )


def _history(n_failures: int, prefix_pass: bool = True) -> list[LoopRun]:
    """Build a history list with optional passing run followed by n_failures failed runs at tail."""
    runs: list[LoopRun] = []
    if prefix_pass:
        runs.append(_run(state=LoopState.PASS, failure_kind=None, error=None))
    for i in range(n_failures):
        runs.append(_run(state=LoopState.FAIL, run_id=f"fail-{i}"))
    return runs


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_routing_key_from_constructor(self):
        h = PagerDutyHandoff(routing_key="test-key")
        assert h.routing_key == "test-key"

    def test_routing_key_from_env(self, monkeypatch):
        monkeypatch.setenv("PAGERDUTY_ROUTING_KEY", "env-key")
        h = PagerDutyHandoff()
        assert h.routing_key == "env-key"

    def test_import_error_without_httpx(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def block_httpx(name, *args, **kwargs):
            if name == "httpx":
                raise ImportError("no httpx")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", block_httpx)
        with pytest.raises(ImportError, match="Install tvastar\\[pagerduty\\]"):
            PagerDutyHandoff(routing_key="k")


# ---------------------------------------------------------------------------
# Severity derivation
# ---------------------------------------------------------------------------


class TestSeverity:
    @pytest.mark.asyncio
    async def test_warning_for_1_failure(self, monkeypatch):
        """1 consecutive failure → warning severity."""
        monkeypatch.setenv("PAGERDUTY_ROUTING_KEY", "rk")
        h = PagerDutyHandoff()
        run = _run()
        history = _history(1)

        posted = {}

        async def mock_post(self, url, **kwargs):
            posted.update(kwargs.get("json", {}))
            resp = httpx.Response(202, request=httpx.Request("POST", url))
            return resp

        with patch.object(httpx.AsyncClient, "post", mock_post):
            await h.escalate(run, history)

        assert posted["payload"]["severity"] == "warning"

    @pytest.mark.asyncio
    async def test_warning_for_2_failures(self, monkeypatch):
        """2 consecutive failures → warning severity."""
        monkeypatch.setenv("PAGERDUTY_ROUTING_KEY", "rk")
        h = PagerDutyHandoff()
        run = _run()
        history = _history(2)

        posted = {}

        async def mock_post(self, url, **kwargs):
            posted.update(kwargs.get("json", {}))
            return httpx.Response(202, request=httpx.Request("POST", url))

        with patch.object(httpx.AsyncClient, "post", mock_post):
            await h.escalate(run, history)

        assert posted["payload"]["severity"] == "warning"

    @pytest.mark.asyncio
    async def test_critical_for_3_failures(self, monkeypatch):
        """3 consecutive failures → critical severity."""
        monkeypatch.setenv("PAGERDUTY_ROUTING_KEY", "rk")
        h = PagerDutyHandoff()
        run = _run()
        history = _history(3)

        posted = {}

        async def mock_post(self, url, **kwargs):
            posted.update(kwargs.get("json", {}))
            return httpx.Response(202, request=httpx.Request("POST", url))

        with patch.object(httpx.AsyncClient, "post", mock_post):
            await h.escalate(run, history)

        assert posted["payload"]["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_critical_for_5_failures(self, monkeypatch):
        """5 consecutive failures → critical severity."""
        monkeypatch.setenv("PAGERDUTY_ROUTING_KEY", "rk")
        h = PagerDutyHandoff()
        run = _run()
        history = _history(5)

        posted = {}

        async def mock_post(self, url, **kwargs):
            posted.update(kwargs.get("json", {}))
            return httpx.Response(202, request=httpx.Request("POST", url))

        with patch.object(httpx.AsyncClient, "post", mock_post):
            await h.escalate(run, history)

        assert posted["payload"]["severity"] == "critical"


# ---------------------------------------------------------------------------
# Payload structure
# ---------------------------------------------------------------------------


class TestPayload:
    @pytest.mark.asyncio
    async def test_payload_fields(self, monkeypatch):
        monkeypatch.setenv("PAGERDUTY_ROUTING_KEY", "my-routing-key")
        h = PagerDutyHandoff()
        run = _run(failure_kind=FailureKind.MODEL_ERROR, error="rate limited")
        history = _history(1)

        posted = {}

        async def mock_post(self, url, **kwargs):
            posted.update(kwargs.get("json", {}))
            return httpx.Response(202, request=httpx.Request("POST", url))

        with patch.object(httpx.AsyncClient, "post", mock_post):
            await h.escalate(run, history)

        assert posted["routing_key"] == "my-routing-key"
        assert posted["event_action"] == "trigger"
        assert posted["dedup_key"] == "run-123"
        assert posted["payload"]["summary"] == "model_error: rate limited"
        assert posted["payload"]["source"] == "my-loop"

    @pytest.mark.asyncio
    async def test_summary_truncated_to_1024(self, monkeypatch):
        monkeypatch.setenv("PAGERDUTY_ROUTING_KEY", "rk")
        h = PagerDutyHandoff()
        long_error = "x" * 2000
        run = _run(error=long_error)
        history = _history(1)

        posted = {}

        async def mock_post(self, url, **kwargs):
            posted.update(kwargs.get("json", {}))
            return httpx.Response(202, request=httpx.Request("POST", url))

        with patch.object(httpx.AsyncClient, "post", mock_post):
            await h.escalate(run, history)

        assert len(posted["payload"]["summary"]) <= 1024

    @pytest.mark.asyncio
    async def test_summary_with_no_failure_kind(self, monkeypatch):
        monkeypatch.setenv("PAGERDUTY_ROUTING_KEY", "rk")
        h = PagerDutyHandoff()
        run = _run(failure_kind=None, error="something broke")
        history = _history(1)

        posted = {}

        async def mock_post(self, url, **kwargs):
            posted.update(kwargs.get("json", {}))
            return httpx.Response(202, request=httpx.Request("POST", url))

        with patch.object(httpx.AsyncClient, "post", mock_post):
            await h.escalate(run, history)

        assert posted["payload"]["summary"] == "unknown: something broke"


# ---------------------------------------------------------------------------
# API error handling
# ---------------------------------------------------------------------------


class TestAPIErrors:
    @pytest.mark.asyncio
    async def test_raises_on_4xx(self, monkeypatch):
        monkeypatch.setenv("PAGERDUTY_ROUTING_KEY", "rk")
        h = PagerDutyHandoff()
        run = _run()
        history = _history(1)

        async def mock_post(self, url, **kwargs):
            return httpx.Response(400, request=httpx.Request("POST", url))

        with patch.object(httpx.AsyncClient, "post", mock_post):
            with pytest.raises(RuntimeError, match="HTTP 400"):
                await h.escalate(run, history)

    @pytest.mark.asyncio
    async def test_raises_on_5xx(self, monkeypatch):
        monkeypatch.setenv("PAGERDUTY_ROUTING_KEY", "rk")
        h = PagerDutyHandoff()
        run = _run()
        history = _history(1)

        async def mock_post(self, url, **kwargs):
            return httpx.Response(500, request=httpx.Request("POST", url))

        with patch.object(httpx.AsyncClient, "post", mock_post):
            with pytest.raises(RuntimeError, match="HTTP 500"):
                await h.escalate(run, history)
