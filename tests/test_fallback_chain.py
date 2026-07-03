"""Unit tests for fallback model chain (Requirement 17)."""

from __future__ import annotations

import pytest

from tvastar import Harness, create_agent
from tvastar.model import MockModel


class TestFallbackModelChain:
    """Tests for the fallback model chain in Session._run_loop()."""

    async def test_primary_succeeds_no_fallback_used(self):
        """When primary model succeeds, fallback models are not tried."""
        primary = MockModel(["Primary response"])
        fallback1 = MockModel(["Fallback1 response"])

        agent = create_agent(
            "test",
            model=primary,
            instructions="test",
            fallback_models=[fallback1],
        )
        h = Harness(agent)
        sess = h.session()
        result = await sess.prompt("hello")
        assert "Primary response" in result.text
        # Fallback should not have been called
        assert len(fallback1.calls) == 0

    async def test_primary_fails_first_fallback_succeeds(self):
        """When primary fails with non-overflow, first fallback is used."""
        primary = MockModel([RuntimeError("primary down")])
        fallback1 = MockModel(["Fallback1 response"])

        agent = create_agent(
            "test",
            model=primary,
            instructions="test",
            fallback_models=[fallback1],
        )
        h = Harness(agent)
        sess = h.session()
        result = await sess.prompt("hello")
        assert "Fallback1 response" in result.text
        assert len(fallback1.calls) == 1

    async def test_primary_fails_second_fallback_succeeds(self):
        """When primary and first fallback fail, second fallback is tried."""
        primary = MockModel([RuntimeError("primary down")])
        fallback1 = MockModel([RuntimeError("fallback1 down")])
        fallback2 = MockModel(["Fallback2 response"])

        agent = create_agent(
            "test",
            model=primary,
            instructions="test",
            fallback_models=[fallback1, fallback2],
        )
        h = Harness(agent)
        sess = h.session()
        result = await sess.prompt("hello")
        assert "Fallback2 response" in result.text
        assert len(fallback1.calls) == 1
        assert len(fallback2.calls) == 1

    async def test_all_fallbacks_fail_raises_primary_exception(self):
        """When all fallbacks fail, the primary exception is raised."""
        primary_error = RuntimeError("primary down")
        primary = MockModel([primary_error])
        fallback1 = MockModel([RuntimeError("fallback1 down")])
        fallback2 = MockModel([RuntimeError("fallback2 down")])

        agent = create_agent(
            "test",
            model=primary,
            instructions="test",
            fallback_models=[fallback1, fallback2],
        )
        h = Harness(agent)
        sess = h.session()
        with pytest.raises(RuntimeError, match="primary down"):
            await sess.prompt("hello")

    async def test_overflow_exception_bypasses_fallback_chain(self):
        """Context overflow exceptions bypass the fallback chain entirely."""
        # Use an overflow phrase from _OVERFLOW_PHRASES
        overflow_err = RuntimeError("maximum context length is 128000 tokens")
        primary = MockModel([overflow_err])
        fallback1 = MockModel(["Fallback response"])

        agent = create_agent(
            "test",
            model=primary,
            instructions="test",
            fallback_models=[fallback1],
        )
        h = Harness(agent)
        sess = h.session()
        with pytest.raises(RuntimeError, match="maximum context length"):
            await sess.prompt("hello")
        # Fallback should NOT have been called
        assert len(fallback1.calls) == 0

    async def test_no_fallback_models_raises_immediately(self):
        """When no fallback_models configured, non-overflow exception raises."""
        primary = MockModel([RuntimeError("primary down")])

        agent = create_agent(
            "test",
            model=primary,
            instructions="test",
        )
        h = Harness(agent)
        sess = h.session()
        with pytest.raises(RuntimeError, match="primary down"):
            await sess.prompt("hello")

    async def test_fallback_chain_order_preserved(self):
        """Fallback models are tried in the order they are configured."""
        call_order = []

        class TrackingModel(MockModel):
            def __init__(self, name_tag, script):
                super().__init__(script)
                self._tag = name_tag

            async def generate(self, messages, **kwargs):
                call_order.append(self._tag)
                return await super().generate(messages, **kwargs)

        primary = TrackingModel("primary", [RuntimeError("down")])
        fb1 = TrackingModel("fb1", [RuntimeError("also down")])
        fb2 = TrackingModel("fb2", [RuntimeError("also down")])
        fb3 = TrackingModel("fb3", ["Success from fb3"])

        agent = create_agent(
            "test",
            model=primary,
            instructions="test",
            fallback_models=[fb1, fb2, fb3],
        )
        h = Harness(agent)
        sess = h.session()
        result = await sess.prompt("hello")
        assert "Success from fb3" in result.text
        assert call_order == ["primary", "fb1", "fb2", "fb3"]
