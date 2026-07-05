"""Shared test fixtures."""
import pytest


@pytest.fixture
def tenant_id():
    from uuid import UUID
    return UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def project_id():
    from uuid import UUID
    return UUID("00000000-0000-0000-0000-000000000002")
