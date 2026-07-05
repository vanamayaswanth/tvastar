"""Per-tenant API rate limiting middleware."""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # ponytail: Check Valkey sliding window, return 429 with Retry-After if exceeded
        response = await call_next(request)
        return response
