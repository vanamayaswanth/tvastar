"""Tenant context extraction middleware."""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # ponytail: Extract tenant_id from JWT claims, set on request.state
        response = await call_next(request)
        return response
