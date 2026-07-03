"""NATS-backed event backend for Fleet EventBus.

Requires: pip install tvastar[nats]
Uses nats-py async client. Provides persistent pub/sub via NATS JetStream.
"""
from __future__ import annotations

import json
from typing import Any, Callable


class NATSEventBackend:
    """Implements the EventBackend protocol using NATS.

    Topics are namespaced as: fleet.{fleet_name}.{topic}
    Events are JSON-serialized FleetEvent payloads.

    NOTE: This backend requires an external NATS server.
    For in-process use, the default in-memory EventBus is sufficient.
    """

    def __init__(self, url: str = "nats://localhost:4222") -> None:
        try:
            import nats  # noqa: F401
        except ImportError:
            raise ImportError(
                "NATS backend requires the 'tvastar[nats]' extra. "
                "Install it with: pip install tvastar[nats]"
            ) from None
        self._url = url
        self._nc: Any = None  # Lazy connection
        self._subscriptions: dict[str, Any] = {}

    async def _ensure_connected(self) -> Any:
        if self._nc is None:
            import nats

            self._nc = await nats.connect(self._url)
        return self._nc

    def _subject(self, fleet_name: str, topic: str) -> str:
        return f"fleet.{fleet_name}.{topic}"

    def publish(self, fleet_name: str, topic: str, event: Any) -> None:
        """Publish synchronously by encoding the event as JSON.

        Note: For full async support, use publish_async().
        This sync version is provided for protocol compatibility.
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._publish_async(fleet_name, topic, event))
        except RuntimeError:
            asyncio.run(self._publish_async(fleet_name, topic, event))

    async def _publish_async(self, fleet_name: str, topic: str, event: Any) -> None:
        nc = await self._ensure_connected()
        subject = self._subject(fleet_name, topic)
        payload = json.dumps(
            {
                "topic": event.topic,
                "payload": (
                    event.payload if not callable(event.payload) else str(event.payload)
                ),
                "source_agent": event.source_agent,
                "timestamp": event.timestamp,
                "correlation_id": event.correlation_id,
            }
        ).encode()
        await nc.publish(subject, payload)

    def subscribe(self, fleet_name: str, topic: str, handler: Callable) -> str:
        """Subscribe to a NATS subject. Returns subscription ID."""
        import asyncio
        import uuid

        sub_id = str(uuid.uuid4())

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self._subscribe_async(fleet_name, topic, handler, sub_id)
            )
        except RuntimeError:
            asyncio.run(self._subscribe_async(fleet_name, topic, handler, sub_id))

        return sub_id

    async def _subscribe_async(
        self, fleet_name: str, topic: str, handler: Callable, sub_id: str
    ) -> None:
        nc = await self._ensure_connected()
        subject = self._subject(fleet_name, topic)

        async def _msg_handler(msg: Any) -> None:
            try:
                data = json.loads(msg.data.decode())
                # Reconstruct a minimal event-like object for the handler
                from tvastar.fleet.bus import FleetEvent

                event = FleetEvent(
                    topic=data["topic"],
                    payload=data["payload"],
                    source_agent=data["source_agent"],
                    timestamp=data.get("timestamp", 0),
                    correlation_id=data.get("correlation_id"),
                )
                handler(event)
            except Exception:
                pass  # best-effort delivery

        sub = await nc.subscribe(subject, cb=_msg_handler)
        self._subscriptions[sub_id] = sub

    def unsubscribe(self, subscription_id: str) -> bool:
        sub = self._subscriptions.pop(subscription_id, None)
        if sub is None:
            return False
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(sub.unsubscribe())
        except RuntimeError:
            asyncio.run(sub.unsubscribe())
        return True

    async def close(self) -> None:
        if self._nc is not None:
            await self._nc.close()
            self._nc = None
