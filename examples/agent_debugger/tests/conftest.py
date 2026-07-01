"""Pytest configuration for agent_debugger tests."""


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "property: mark test as a property-based test (runs with Hypothesis)",
    )
