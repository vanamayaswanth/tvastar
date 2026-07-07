"""Redis-backed state backend for Fleet SharedStateStore.

Requires: pip install tvastar[redis]
"""

from __future__ import annotations

import json
from typing import Any


class RedisStateBackend:
    """Implements the StateBackend protocol using Redis.

    Keys are namespaced as: fleet:{fleet_name}:state:{key}
    Values are JSON-serialized with version metadata.
    """

    def __init__(self, url: str = "redis://localhost:6379", **kwargs: Any) -> None:
        try:
            import redis
        except ImportError:
            raise ImportError(
                "Redis backend requires the 'tvastar[redis]' extra. "
                "Install it with: pip install tvastar[redis]"
            ) from None
        self._client = redis.from_url(url, decode_responses=True, **kwargs)

    def _key(self, fleet_name: str, key: str) -> str:
        return f"fleet:{fleet_name}:state:{key}"

    def get(self, fleet_name: str, key: str) -> Any | None:
        raw = self._client.get(self._key(fleet_name, key))
        if raw is None:
            return None
        data = json.loads(raw)
        return data["value"]

    def set(self, fleet_name: str, key: str, value: Any, version: int) -> None:
        payload = json.dumps({"value": value, "version": version})
        self._client.set(self._key(fleet_name, key), payload)

    def delete(self, fleet_name: str, key: str) -> bool:
        return bool(self._client.delete(self._key(fleet_name, key)))

    def close(self) -> None:
        self._client.close()
