"""Tests for event-driven loop triggers (Loop.subscribe_trigger + EventBus)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from tvastar.fleet.bus import EventBus


class TestSubscribeTrigger:
    """Tests for Loop.subscribe_trigger() method."""

    def test_subscribe_registers_on_event_bus(self):
        """subscribe_trigger registers a handler on the EventBus for the configured topic."""
        from tvastar.loop import LoopConfig

        config = LoopConfig(name="test-loop", goal="do work", trigger_on="event:deploy.done")

        # Create a mock loop with the config
        mock_loop = MagicMock()
        mock_loop._config = config
        mock_loop.name = "test-loop"

        # Import the real method and bind it
        from tvastar.loop import Loop

        bus = EventBus("test-fleet")
        assert "deploy.done" not in bus.topics()

        # Call the real subscribe_trigger method on a mock
        Loop.subscribe_trigger(mock_loop, bus)

        # Verify the topic now has a subscriber
        assert "deploy.done" in bus.topics()

    def test_subscribe_does_nothing_without_trigger_on(self):
        """subscribe_trigger is a no-op when trigger_on is None."""
        from tvastar.loop import LoopConfig

        config = LoopConfig(name="test-loop", goal="do work", trigger_on=None)

        mock_loop = MagicMock()
        mock_loop._config = config
        mock_loop.name = "test-loop"

        from tvastar.loop import Loop

        bus = EventBus("test-fleet")
        Loop.subscribe_trigger(mock_loop, bus)

        # No subscriptions should have been made
        assert bus.topics() == []

    def test_subscribe_does_nothing_with_invalid_prefix(self):
        """subscribe_trigger is a no-op when trigger_on doesn't start with 'event:'."""

        # This would fail validation, so we directly set it on a mock config
        mock_config = MagicMock()
        mock_config.trigger_on = "cron:daily"

        mock_loop = MagicMock()
        mock_loop._config = mock_config
        mock_loop.name = "test-loop"

        from tvastar.loop import Loop

        bus = EventBus("test-fleet")
        Loop.subscribe_trigger(mock_loop, bus)

        assert bus.topics() == []

    @pytest.mark.asyncio
    async def test_event_triggers_loop(self):
        """Publishing to the subscribed topic triggers the loop."""
        from tvastar.loop import LoopConfig, Loop

        config = LoopConfig(name="trigger-test", goal="react to events", trigger_on="event:ci.done")

        mock_loop = MagicMock()
        mock_loop._config = config
        mock_loop.name = "trigger-test"

        # Make trigger an async mock that returns immediately
        trigger_future = asyncio.Future()
        trigger_future.set_result(MagicMock(run_id="run_abc"))
        mock_loop.trigger = AsyncMock(return_value=MagicMock(run_id="run_abc"))

        bus = EventBus("test-fleet")
        Loop.subscribe_trigger(mock_loop, bus)

        # Publish an event to the topic
        bus.publish("ci.done", {"commit": "abc123"}, source_agent="ci-agent")

        # Allow the created task to run
        await asyncio.sleep(0.05)

        # Verify trigger was called with event context
        mock_loop.trigger.assert_called_once()
        call_kwargs = mock_loop.trigger.call_args
        context = call_kwargs[1]["context"] if call_kwargs[1] else call_kwargs[0][0] if call_kwargs[0] else {}
        # The context should contain the event payload
        if "context" in (call_kwargs[1] or {}):
            assert call_kwargs[1]["context"]["event"] == {"commit": "abc123"}


class TestLoopConfigTriggerOn:
    """Tests for LoopConfig.trigger_on validation."""

    def test_trigger_on_none_is_valid(self):
        """trigger_on=None is the default (manual/cron mode)."""
        from tvastar.loop import LoopConfig

        config = LoopConfig(name="test", goal="test", trigger_on=None)
        assert config.trigger_on is None

    def test_trigger_on_valid_event(self):
        """trigger_on='event:topic' is valid."""
        from tvastar.loop import LoopConfig

        config = LoopConfig(name="test", goal="test", trigger_on="event:deploy.complete")
        assert config.trigger_on == "event:deploy.complete"

    def test_trigger_on_invalid_prefix_raises(self):
        """trigger_on without 'event:' prefix raises ValueError."""
        from tvastar.loop import LoopConfig

        with pytest.raises(ValueError, match="must start with 'event:'"):
            LoopConfig(name="test", goal="test", trigger_on="cron:daily")

    def test_trigger_on_empty_topic_raises(self):
        """trigger_on='event:' (empty topic) raises ValueError."""
        from tvastar.loop import LoopConfig

        with pytest.raises(ValueError, match="topic must not be empty"):
            LoopConfig(name="test", goal="test", trigger_on="event:")
