"""Unit tests for __post_init__ validation on critical dataclasses (REQ-14)."""

import pytest

from tvastar.fleet import FleetBudgetConfig
from tvastar.loop import LoopConfig
from tvastar.sandbox.base import SecurityPolicy


# ---------------------------------------------------------------------------
# FleetBudgetConfig
# ---------------------------------------------------------------------------


class TestFleetBudgetConfigValidation:
    def test_valid_config(self):
        c = FleetBudgetConfig(max_fleet_usd=100.0, warn_threshold=0.7, throttle_threshold=0.9)
        assert c.max_fleet_usd == 100.0

    def test_max_fleet_usd_zero(self):
        with pytest.raises(ValueError, match="max_fleet_usd.*> 0.*got 0"):
            FleetBudgetConfig(max_fleet_usd=0)

    def test_max_fleet_usd_negative(self):
        with pytest.raises(ValueError, match="max_fleet_usd.*> 0.*got -10"):
            FleetBudgetConfig(max_fleet_usd=-10)

    def test_warn_threshold_above_one(self):
        with pytest.raises(ValueError, match="warn_threshold.*\\[0.0, 1.0\\].*got 1.5"):
            FleetBudgetConfig(max_fleet_usd=100, warn_threshold=1.5, throttle_threshold=0.9)

    def test_warn_threshold_negative(self):
        with pytest.raises(ValueError, match="warn_threshold.*\\[0.0, 1.0\\].*got -0.1"):
            FleetBudgetConfig(max_fleet_usd=100, warn_threshold=-0.1, throttle_threshold=0.9)

    def test_throttle_threshold_above_one(self):
        with pytest.raises(ValueError, match="throttle_threshold.*\\[0.0, 1.0\\].*got 2"):
            FleetBudgetConfig(max_fleet_usd=100, warn_threshold=0.5, throttle_threshold=2.0)

    def test_throttle_threshold_negative(self):
        with pytest.raises(ValueError, match="throttle_threshold.*\\[0.0, 1.0\\].*got -0.5"):
            FleetBudgetConfig(max_fleet_usd=100, warn_threshold=0.0, throttle_threshold=-0.5)

    def test_warn_equals_throttle(self):
        with pytest.raises(ValueError, match="warn_threshold.*strictly less.*throttle_threshold"):
            FleetBudgetConfig(max_fleet_usd=100, warn_threshold=0.9, throttle_threshold=0.9)

    def test_warn_greater_than_throttle(self):
        with pytest.raises(ValueError, match="warn_threshold.*strictly less.*throttle_threshold"):
            FleetBudgetConfig(max_fleet_usd=100, warn_threshold=0.95, throttle_threshold=0.8)

    def test_boundary_valid(self):
        """warn=0.0, throttle=1.0 is the widest valid range."""
        c = FleetBudgetConfig(max_fleet_usd=0.01, warn_threshold=0.0, throttle_threshold=1.0)
        assert c.warn_threshold == 0.0
        assert c.throttle_threshold == 1.0


# ---------------------------------------------------------------------------
# SecurityPolicy
# ---------------------------------------------------------------------------


class TestSecurityPolicyValidation:
    def test_default_valid(self):
        p = SecurityPolicy()
        assert p.timeout_seconds == 60.0

    def test_empty_denied_substring(self):
        with pytest.raises(ValueError, match="denied_substrings.*non-empty.*got ''"):
            SecurityPolicy(denied_substrings={"valid", ""})

    def test_timeout_zero(self):
        with pytest.raises(ValueError, match="timeout_seconds.*> 0.*got 0"):
            SecurityPolicy(timeout_seconds=0)

    def test_timeout_negative(self):
        with pytest.raises(ValueError, match="timeout_seconds.*> 0.*got -5"):
            SecurityPolicy(timeout_seconds=-5)

    def test_valid_custom(self):
        p = SecurityPolicy(timeout_seconds=120, denied_substrings={"rm -rf /", "dangerous"})
        assert p.timeout_seconds == 120


# ---------------------------------------------------------------------------
# LoopConfig
# ---------------------------------------------------------------------------


class TestLoopConfigValidation:
    def test_valid_default(self):
        c = LoopConfig(name="test", goal="do stuff")
        assert c.max_iterations == 3

    def test_max_iterations_zero(self):
        with pytest.raises(ValueError, match="max_iterations.*>= 1.*got 0"):
            LoopConfig(name="t", goal="g", max_iterations=0)

    def test_retry_backoff_base_negative(self):
        with pytest.raises(ValueError, match="retry_backoff_base.*>= 0.*got -1"):
            LoopConfig(name="t", goal="g", retry_backoff_base=-1)

    def test_retry_backoff_base_zero_valid(self):
        c = LoopConfig(name="t", goal="g", retry_backoff_base=0)
        assert c.retry_backoff_base == 0

    def test_cancel_after_zero(self):
        with pytest.raises(ValueError, match="cancel_after.*> 0.*got 0"):
            LoopConfig(name="t", goal="g", cancel_after=0)

    def test_cancel_after_negative(self):
        with pytest.raises(ValueError, match="cancel_after.*> 0.*got -5"):
            LoopConfig(name="t", goal="g", cancel_after=-5)

    def test_cancel_after_none_valid(self):
        c = LoopConfig(name="t", goal="g", cancel_after=None)
        assert c.cancel_after is None

    def test_cancel_after_positive_valid(self):
        c = LoopConfig(name="t", goal="g", cancel_after=30.0)
        assert c.cancel_after == 30.0

    def test_circuit_breaker_limit_zero(self):
        with pytest.raises(ValueError, match="circuit_breaker_limit.*>= 1.*got 0"):
            LoopConfig(name="t", goal="g", circuit_breaker_limit=0)

    def test_circuit_breaker_limit_one_valid(self):
        c = LoopConfig(name="t", goal="g", circuit_breaker_limit=1)
        assert c.circuit_breaker_limit == 1
