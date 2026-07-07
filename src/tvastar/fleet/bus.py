"""In-memory publish-subscribe event bus for intra-fleet coordination.

Provides FleetEvent, EventHandler type alias, and EventBus class that supports
scoped pub/sub within a fleet. Events to topics with no subscribers are silently
discarded. Handler exceptions are swallowed (best-effort delivery).

Optionally delegates to a persistent EventBackend (e.g. Kafka) if provided.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from tvastar.fleet._backends import EventBackend


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class FleetEvent:
    """An event published to the fleet EventBus."""

    topic: str
    payload: Any
    source_agent: str
    timestamp: float = field(default_factory=time.time)
    correlation_id: str | None = None


# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

EventHandler = Callable[[FleetEvent], Any]
"""Callable that handles a FleetEvent. Signature: (FleetEvent) -> Any."""


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------


class EventBus:
    """In-memory publish-subscribe bus scoped to a single fleet.

    Parameters
    ----------
    fleet_name:
        Identifier for the fleet this bus belongs to.
    backend:
        Optional persistent backend implementing the EventBackend protocol.
        When provided, publish/subscribe/unsubscribe are delegated to it.
        When None, an in-memory subscriptions dict is used.
    """

    def __init__(
        self,
        fleet_name: str,
        *,
        backend: EventBackend | None = None,
    ) -> None:
        self._fleet_name = fleet_name
        self._backend = backend
        # In-memory state: topic -> {subscription_id: handler}
        self._subscriptions: dict[str, dict[str, EventHandler]] = {}
        # Reverse lookup: subscription_id -> topic
        self._sub_to_topic: dict[str, str] = {}

    @property
    def fleet_name(self) -> str:
        """The fleet this bus is scoped to."""
        return self._fleet_name

    def publish(
        self,
        topic: str,
        payload: Any,
        *,
        source_agent: str,
        correlation_id: str | None = None,
    ) -> None:
        """Publish an event to all subscribers of the given topic.

        Creates a FleetEvent with the current timestamp and delivers it to
        every handler registered for the topic. If no subscribers exist for
        the topic, the event is silently discarded (no error).

        Handler exceptions are caught and swallowed — delivery is best-effort.
        Exceptions never propagate back to the publisher.

        If a backend is configured, the event is also forwarded to the backend.
        """
        event = FleetEvent(
            topic=topic,
            payload=payload,
            source_agent=source_agent,
            timestamp=time.time(),
            correlation_id=correlation_id,
        )

        # Delegate to backend if present
        if self._backend is not None:
            try:
                self._backend.publish(self._fleet_name, topic, event)
            except Exception:
                pass  # best-effort

        # In-memory delivery
        handlers = self._subscriptions.get(topic)
        if not handlers:
            # No subscribers — silently discard
            return

        for handler in list(handlers.values()):
            try:
                handler(event)
            except Exception:
                # Best-effort delivery: swallow handler exceptions
                pass

    def subscribe(
        self,
        topic: str,
        handler: EventHandler,
        *,
        agent: str | None = None,
    ) -> str:
        """Subscribe a handler to events on the given topic.

        Parameters
        ----------
        topic:
            The event topic to subscribe to.
        handler:
            Callable invoked with a FleetEvent when an event is published.
        agent:
            Optional name of the subscribing agent (informational).

        Returns
        -------
        A unique subscription_id (uuid4) that can be used to unsubscribe.
        """
        subscription_id = str(uuid.uuid4())

        # Delegate to backend if present
        if self._backend is not None:
            try:
                backend_id = self._backend.subscribe(self._fleet_name, topic, handler)
                # Use backend's subscription ID if available
                if backend_id:
                    subscription_id = backend_id
            except Exception:
                pass  # fall through to in-memory

        # In-memory registration
        if topic not in self._subscriptions:
            self._subscriptions[topic] = {}

        self._subscriptions[topic][subscription_id] = handler
        self._sub_to_topic[subscription_id] = topic

        return subscription_id

    def unsubscribe(self, subscription_id: str) -> bool:
        """Remove a subscription by its ID.

        Parameters
        ----------
        subscription_id:
            The ID returned by subscribe().

        Returns
        -------
        True if the subscription existed and was removed, False otherwise.
        """
        topic = self._sub_to_topic.pop(subscription_id, None)
        if topic is None:
            return False

        handlers = self._subscriptions.get(topic)
        if handlers is None:
            return False

        removed = handlers.pop(subscription_id, None) is not None

        # Clean up empty topic entries
        if not handlers:
            del self._subscriptions[topic]

        # Delegate to backend if present
        if self._backend is not None:
            try:
                self._backend.unsubscribe(subscription_id)
            except Exception:
                pass  # best-effort

        return removed

    def topics(self) -> list[str]:
        """Return a list of topics that currently have at least one subscriber."""
        return list(self._subscriptions.keys())
