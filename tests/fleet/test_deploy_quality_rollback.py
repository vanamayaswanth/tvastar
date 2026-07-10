"""Tests for quality regression detection triggering automatic rollback (task 12.2).

Validates: Requirements 6.5, 6.6
- REQ-6 AC5: After successful rollback, agent entry reports previous version and accepts new tasks
- REQ-6 AC6: Quality regression (score < threshold, default 70) triggers automatic rollback
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tvastar.fleet.deploy import DeployManager
from tvastar.fleet.registry import FleetRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry():
    """A FleetRegistry with one registered agent at version '1.0.0'."""
    reg = FleetRegistry()
    mock_loop = MagicMock()
    mock_loop.name = "test-agent"
    mock_loop.config = MagicMock()
    mock_loop.config.name = "test-agent"
    reg.register(mock_loop, name="test-agent", version="1.0.0", owner="team")
    return reg


@pytest.fixture
def deploy_manager(registry):
    """DeployManager wired to the registry with a mock event bus."""
    event_bus = MagicMock()
    event_bus.publish = MagicMock()
    return DeployManager(registry, event_bus=event_bus)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestQualityRegressionRollback:
    """Quality regression (score < 70) triggers automatic rollback."""

    def test_default_threshold_is_70(self, deploy_manager, registry):
        """CanaryDeployment default min_quality_threshold is 70."""
        deploy_manager.start_canary("test-agent", "2.0.0", 0.1, {"model": "new"})
        deployment = deploy_manager.get_canary("test-agent")
        assert deployment is not None
        assert deployment.min_quality_threshold == 70.0

    def test_should_rollback_when_quality_below_threshold(self, deploy_manager):
        """should_rollback_canary() returns True when avg quality < 70."""
        deploy_manager.start_canary("test-agent", "2.0.0", 0.1, {})
        # Record scores below threshold
        deploy_manager.record_canary_quality("test-agent", is_canary=True, score=60.0)
        deploy_manager.record_canary_quality("test-agent", is_canary=True, score=65.0)

        assert deploy_manager.should_rollback_canary("test-agent") is True

    def test_should_not_rollback_when_quality_above_threshold(self, deploy_manager):
        """should_rollback_canary() returns False when avg quality >= 70."""
        deploy_manager.start_canary("test-agent", "2.0.0", 0.1, {})
        deploy_manager.record_canary_quality("test-agent", is_canary=True, score=80.0)
        deploy_manager.record_canary_quality("test-agent", is_canary=True, score=75.0)

        assert deploy_manager.should_rollback_canary("test-agent") is False

    @pytest.mark.asyncio
    async def test_evaluate_and_rollback_triggers_on_quality_regression(
        self, deploy_manager, registry
    ):
        """evaluate_and_rollback() rolls back when quality < 70."""
        deploy_manager.start_canary("test-agent", "2.0.0", 0.1, {"model": "new"})
        deploy_manager.record_canary_quality("test-agent", is_canary=True, score=50.0)

        result = await deploy_manager.evaluate_and_rollback("test-agent")

        assert result is True
        # Canary removed
        assert deploy_manager.has_canary("test-agent") is False

    @pytest.mark.asyncio
    async def test_registry_reports_stable_version_after_rollback(self, deploy_manager, registry):
        """After rollback, registry entry still reports the previous stable version."""
        deploy_manager.start_canary("test-agent", "2.0.0", 0.1, {"model": "new"})
        deploy_manager.record_canary_quality("test-agent", is_canary=True, score=40.0)

        await deploy_manager.evaluate_and_rollback("test-agent")

        entry = registry.get("test-agent")
        assert entry is not None
        assert entry.version == "1.0.0"  # stable version preserved

    @pytest.mark.asyncio
    async def test_agent_accepts_new_canary_after_rollback(self, deploy_manager, registry):
        """After successful rollback, agent is NOT halted — can start new canaries."""
        deploy_manager.start_canary("test-agent", "2.0.0", 0.1, {})
        deploy_manager.record_canary_quality("test-agent", is_canary=True, score=30.0)

        await deploy_manager.evaluate_and_rollback("test-agent")

        # Agent should accept a new canary (not halted)
        new_deployment = deploy_manager.start_canary("test-agent", "3.0.0", 0.2, {"model": "v3"})
        assert new_deployment.canary_version == "3.0.0"
        assert new_deployment.stable_version == "1.0.0"

    def test_start_canary_min_quality_parameter_default_70(self, deploy_manager):
        """start_canary() min_quality parameter defaults to 70."""
        deployment = deploy_manager.start_canary("test-agent", "2.0.0", 0.1, {})
        assert deployment.min_quality_threshold == 70.0

    def test_custom_threshold_respected(self, deploy_manager):
        """Custom min_quality overrides the default 70."""
        deployment = deploy_manager.start_canary("test-agent", "2.0.0", 0.1, {}, min_quality=80.0)
        assert deployment.min_quality_threshold == 80.0
        deploy_manager.record_canary_quality("test-agent", is_canary=True, score=75.0)
        # 75 < 80, so should rollback
        assert deploy_manager.should_rollback_canary("test-agent") is True
