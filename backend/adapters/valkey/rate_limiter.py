"""Sliding window rate limiter using Valkey sorted sets."""


async def check_rate_limit(key: str, limit: int, window_seconds: int) -> bool:
    """Return True if request is within rate limit, False if exceeded."""
    # ponytail: sorted set with timestamp scores, ZREMRANGEBYSCORE + ZCARD
    raise NotImplementedError
