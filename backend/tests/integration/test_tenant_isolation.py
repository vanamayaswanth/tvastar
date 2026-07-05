"""Integration test placeholder for tenant data isolation."""
import pytest


@pytest.mark.skip(reason="Requires running PostgreSQL with RLS")
def test_tenant_a_cannot_see_tenant_b_data():
    """Verify RLS enforcement: queries in tenant A context return zero rows from tenant B."""
    pass
