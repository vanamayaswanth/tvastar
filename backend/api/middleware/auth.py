"""JWT authentication middleware."""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # ponytail: Validate JWT, extract user/role, 401 on failure. Skip /health.
        response = await call_next(request)
        return response
