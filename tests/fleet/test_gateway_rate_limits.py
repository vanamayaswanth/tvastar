"""Unit tests for FleetGateway rate limiting (task 3.3).

Tests _check_rate_limits() and set_agent_rate_limit() methods to verify:
- Fleet-wide rate limit enforcement
- Per-agent rate limit enforcement
- RateLimitError with correct scope and reset_after
- Independent configuration of per-agent vs fleet-wide limits
- Dynamic rate limit configuration via set_agent_rate_limit()

Validates: Requirements 4.1, 4.2, 4.3, 4.4
"""

from __future__ import annotations

import pytest

from tvastar.fleet import RateLimitError
from tvastar.fleet.gateway import FleetGateway, RateLimitConfig


class TestFleetWideRateLimit:
    """Tests for fleet-wide rate limiting."""

    def test_fleet_limit_allows_within_capacity(self):
        """Requests within capacity should pass without error."""
        gw = FleetGateway(
            registry=None,
            fleet_rate_limit=RateLimitConfig(requests_per_window=5, window_seconds=60.0),
        )
        # Should allow 5 requests
        for _ in range(5):
            gw._check_rate_limits("agent-a")

    def test_fleet_limit_rejects_over_capacity(self):
        """Requests exceeding capacity should raise RateLimitError with scope='fleet'."""
        gw = FleetGateway(
            registry=None,
            fleet_rate_limit=RateLimitConfig(requests_per_window=3, window_seconds=60.0),
        )
        # Consume all 3 tokens
        for _ in range(3):
            gw._check_rate_limits("agent-a")

        # 4th request should be rejected
        with pytest.raises(RateLimitError) as exc_info:
            gw._check_rate_limits("agent-a")

        assert exc_info.value.scope == "fleet"
        assert exc_info.value.reset_after > 0

    def test_fleet_limit_applies_across_all_agents(self):
        """Fleet-wide limit is shared across all agents."""
        gw = FleetGateway(
            registry=None,
            fleet_rate_limit=RateLimitConfig(requests_per_window=2, window_seconds=60.0),
        )
        # One request from agent-a, one from agent-b
        gw._check_rate_limits("agent-a")
        gw._check_rate_limits("agent-b")

        # Both agents should now be rejected (fleet limit exhausted)
        with pytest.raises(RateLimitError) as exc_info:
            gw._check_rate_limits("agent-c")

        assert exc_info.value.scope == "fleet"

    def test_no_fleet_limit_configured_allows_all(self):
        """When no fleet rate limit is configured, all requests pass."""
        gw = FleetGateway(registry=None, fleet_rate_limit=None)
        # Should not raise
        for _ in range(100):
            gw._check_rate_limits("agent-a")


class TestPerAgentRateLimit:
    """Tests for per-agent rate limiting."""

    def test_agent_limit_allows_within_capacity(self):
        """Requests within per-agent capacity should pass."""
        gw = FleetGateway(
            registry=None,
            agent_rate_limits={
                "agent-a": RateLimitConfig(requests_per_window=3, window_seconds=60.0)
            },
        )
        for _ in range(3):
            gw._check_rate_limits("agent-a")

    def test_agent_limit_rejects_over_capacity(self):
        """Requests exceeding per-agent capacity should raise RateLimitError."""
        gw = FleetGateway(
            registry=None,
            agent_rate_limits={
                "agent-a": RateLimitConfig(requests_per_window=2, window_seconds=60.0)
            },
        )
        gw._check_rate_limits("agent-a")
        gw._check_rate_limits("agent-a")

        with pytest.raises(RateLimitError) as exc_info:
            gw._check_rate_limits("agent-a")

        assert exc_info.value.scope == "agent:agent-a"
        assert exc_info.value.reset_after > 0

    def test_agent_limit_does_not_affect_other_agents(self):
        """Per-agent limit for agent-a should not block agent-b."""
        gw = FleetGateway(
            registry=None,
            agent_rate_limits={
                "agent-a": RateLimitConfig(requests_per_window=1, window_seconds=60.0),
                "agent-b": RateLimitConfig(requests_per_window=5, window_seconds=60.0),
            },
        )
        # Exhaust agent-a's limit
        gw._check_rate_limits("agent-a")
        with pytest.raises(RateLimitError):
            gw._check_rate_limits("agent-a")

        # agent-b should still work
        gw._check_rate_limits("agent-b")

    def test_unconfigured_agent_has_no_limit(self):
        """Agents without a configured rate limit should pass freely."""
        gw = FleetGateway(
            registry=None,
            agent_rate_limits={
                "agent-a": RateLimitConfig(requests_per_window=1, window_seconds=60.0)
            },
        )
        # agent-b has no configured limit
        for _ in range(100):
            gw._check_rate_limits("agent-b")


class TestCombinedRateLimits:
    """Tests for fleet-wide + per-agent combined enforcement."""

    def test_fleet_limit_checked_before_agent_limit(self):
        """Fleet limit is checked first; if it fails, scope is 'fleet'."""
        gw = FleetGateway(
            registry=None,
            fleet_rate_limit=RateLimitConfig(requests_per_window=1, window_seconds=60.0),
            agent_rate_limits={
                "agent-a": RateLimitConfig(requests_per_window=5, window_seconds=60.0)
            },
        )
        # First request passes both checks
        gw._check_rate_limits("agent-a")

        # Second request should fail at fleet level (only 1 token in fleet bucket)
        with pytest.raises(RateLimitError) as exc_info:
            gw._check_rate_limits("agent-a")

        assert exc_info.value.scope == "fleet"

    def test_agent_limit_triggers_when_fleet_allows(self):
        """When fleet limit allows but agent limit is exceeded, scope is 'agent:X'."""
        gw = FleetGateway(
            registry=None,
            fleet_rate_limit=RateLimitConfig(requests_per_window=10, window_seconds=60.0),
            agent_rate_limits={
                "agent-a": RateLimitConfig(requests_per_window=2, window_seconds=60.0)
            },
        )
        gw._check_rate_limits("agent-a")
        gw._check_rate_limits("agent-a")

        with pytest.raises(RateLimitError) as exc_info:
            gw._check_rate_limits("agent-a")

        assert exc_info.value.scope == "agent:agent-a"

    def test_independent_configuration(self):
        """Per-agent and fleet-wide limits are independently configurable."""
        # Fleet allows 10, agent-a allows 2, agent-b allows 5
        gw = FleetGateway(
            registry=None,
            fleet_rate_limit=RateLimitConfig(requests_per_window=10, window_seconds=60.0),
            agent_rate_limits={
                "agent-a": RateLimitConfig(requests_per_window=2, window_seconds=60.0),
                "agent-b": RateLimitConfig(requests_per_window=5, window_seconds=60.0),
            },
        )
        # Exhaust agent-a (2 requests)
        gw._check_rate_limits("agent-a")
        gw._check_rate_limits("agent-a")

        # agent-a is now limited
        with pytest.raises(RateLimitError) as exc_info:
            gw._check_rate_limits("agent-a")
        assert exc_info.value.scope == "agent:agent-a"

        # agent-b can still make requests (used 2 of fleet's 10, 0 of agent-b's 5)
        for _ in range(5):
            gw._check_rate_limits("agent-b")


class TestSetAgentRateLimit:
    """Tests for dynamic rate limit configuration."""

    def test_set_new_agent_rate_limit(self):
        """set_agent_rate_limit adds a rate limit for a new agent."""
        gw = FleetGateway(registry=None)

        # Initially no limit
        for _ in range(50):
            gw._check_rate_limits("agent-x")

        # Set a limit
        gw.set_agent_rate_limit(
            "agent-x", RateLimitConfig(requests_per_window=2, window_seconds=60.0)
        )

        # Now limited to 2
        gw._check_rate_limits("agent-x")
        gw._check_rate_limits("agent-x")
        with pytest.raises(RateLimitError) as exc_info:
            gw._check_rate_limits("agent-x")
        assert exc_info.value.scope == "agent:agent-x"

    def test_set_updates_existing_agent_rate_limit(self):
        """set_agent_rate_limit replaces an existing rate limit."""
        gw = FleetGateway(
            registry=None,
            agent_rate_limits={
                "agent-a": RateLimitConfig(requests_per_window=1, window_seconds=60.0)
            },
        )
        # Exhaust original limit
        gw._check_rate_limits("agent-a")
        with pytest.raises(RateLimitError):
            gw._check_rate_limits("agent-a")

        # Update to a more generous limit
        gw.set_agent_rate_limit(
            "agent-a", RateLimitConfig(requests_per_window=10, window_seconds=60.0)
        )

        # Now should allow more requests (new bucket starts full)
        for _ in range(10):
            gw._check_rate_limits("agent-a")


class TestRateLimitErrorDetails:
    """Tests for RateLimitError content."""

    def test_error_has_positive_reset_after(self):
        """RateLimitError.reset_after should be positive when bucket is empty."""
        gw = FleetGateway(
            registry=None,
            fleet_rate_limit=RateLimitConfig(requests_per_window=1, window_seconds=60.0),
        )
        gw._check_rate_limits("agent-a")

        with pytest.raises(RateLimitError) as exc_info:
            gw._check_rate_limits("agent-a")

        assert exc_info.value.reset_after > 0
        # Should be less than or equal to the window (one token refills in ~60s/1 = 60s)
        assert exc_info.value.reset_after <= 60.0

    def test_error_message_includes_scope(self):
        """RateLimitError message should include the scope."""
        gw = FleetGateway(
            registry=None,
            fleet_rate_limit=RateLimitConfig(requests_per_window=1, window_seconds=60.0),
        )
        gw._check_rate_limits("agent-a")

        with pytest.raises(RateLimitError) as exc_info:
            gw._check_rate_limits("agent-a")

        assert "fleet" in str(exc_info.value)
