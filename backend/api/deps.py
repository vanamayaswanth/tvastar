"""FastAPI dependency injection functions."""
from fastapi import Request

from core.models import TenantContext


async def get_tenant_context(request: Request) -> TenantContext:
    """Extract tenant context from request state (set by middleware)."""
    return request.state.tenant_context
