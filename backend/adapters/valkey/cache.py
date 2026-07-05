"""CachePort implementation using Valkey (Redis-compatible)."""


class ValkeyCacheAdapter:
    """Implements CachePort with tenant-namespaced keys: t:{tenant_id}:*"""

    async def get(self, key: str) -> str | None:
        raise NotImplementedError

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        raise NotImplementedError

    async def delete(self, key: str) -> None:
        raise NotImplementedError

    async def increment(self, key: str) -> int:
        raise NotImplementedError
