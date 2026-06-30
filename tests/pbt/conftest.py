"""Shared Hypothesis configuration for Tvastar PBT suite.

This conftest configures the Hypothesis settings profile (max 30 examples,
deadline=None for async tests) and is auto-loaded by pytest for all tests
under tests/pbt/.

Strategies are defined in tests/pbt/strategies.py and imported by test modules
directly.

Validates: Requirements REQ-LOOP-001, REQ-DETECT-001, CON-006
"""

from hypothesis import settings, HealthCheck

# ---------------------------------------------------------------------------
# Hypothesis settings profile — applied to all PBT tests in this directory
# ---------------------------------------------------------------------------

settings.register_profile(
    "pbt",
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("pbt")
