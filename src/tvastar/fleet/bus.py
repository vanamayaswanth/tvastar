"""In-memory publish-subscribe event bus for intra-fleet coordination.

Provides FleetEvent, EventHandler type alias, and EventBus class that supports
scoped pub/sub within a fleet. Events to topics with no subscribers are silently
discarded. Handler exceptions route events to the dead-letter queue.

Optionally delegates to a persistent EventBackend (e.g. Kafka) if provided.

Kafka-like features:
- Durable event log: every published event is appended to a per-topic log with
  monotonically increasing offsets. Events are retained up to a configurable
  max (retention). Enables replay and post-mortem debugging.
- Dead-letter queue (DLQ): handler exceptions no longer silently vanish. Failed
  events land in a queryable DLQ with the exception attached.
- Replay: consumers can re-read any topic from any offset, enabling crash
  recovery, new-subscriber catch-up, and integration testing.
"""

from __future__ import annotations

import time
import uuid
from collections import deque
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
    offset: int = -1  # assigned by the bus on publish


# ---------------------------------------------------------------------------
# Dead-letter entry
# ---------------------------------------------------------------------------


@dataclass
class DeadLetter:
    """A failed event delivery record."""

    event: FleetEvent
    handler_id: str
    error: str
    failed_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

EventHandler = Callable[[FleetEvent], Any]
"""Callable that handles a FleetEvent. Signature: (FleetEvent) -> Any."""


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------

# ponytail: default retention is 10k events per topic. Upgrade path: make it
# time-based or move to a real broker (NATS JetStream, Kafka) for larger fleets.
_DEFAULT_RETENTION = 10_000
_DEFAULT_DLQ_MAX = 1_000


class EventBus:
    """In-memory publish-subscribe bus scoped to a single fleet.

    Now with Kafka-like durability semantics:
    - Every event is appended to a per-topic log (capped deque).
    - Failed handler deliveries route to a dead-letter queue.
    - replay() re-delivers events from any offset forward.

    Parameters
    ----------
    fleet_name:
        Identifier for the fleet this bus belongs to.
    backend:
        Optional persistent backend implementing the EventBackend protocol.
        When provided, publish/subscribe/unsubscribe are delegated to it.
        When None, an in-memory subscriptions dict is used.
    retention:
        Max events retained per topic in the in-memory log. Oldest events
        are evicted when this limit is reached. Defaults to 10,000.
    dlq_max:
        Max dead-letter entries retained. Defaults to 1,000.
    """

    def __init__(
        self,
        fleet_name: str,
        *,
        backend: EventBackend | None = None,
        retention: int = _DEFAULT_RETENTION,
        dlq_max: int = _DEFAULT_DLQ_MAX,
    ) -> None:
        self._fleet_name = fleet_name
        self._backend = backend
        self._retention = retention
        # In-memory state: topic -> {subscription_id: handler}
        self._subscriptions: dict[str, dict[str, EventHandler]] = {}
        # Reverse lookup: subscription_id -> topic
        self._sub_to_topic: dict[str, str] = {}

        # --- Kafka-like additions ---
        # Durable log: topic -> deque of (offset, event)
        self._log: dict[str, deque[FleetEvent]] = {}
        # Per-topic offset counter (monotonically increasing)
        self._next_offset: dict[str, int] = {}
        # Dead-letter queue
        self._dead_letters: deque[DeadLetter] = deque(maxlen=dlq_max)

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
    ) -> int:
        """Publish an event to all subscribers of the given topic.

        Creates a FleetEvent, appends it to the durable topic log, then
        delivers it to every registered handler. If no subscribers exist,
        the event is still logged (available for replay).

        Handler exceptions route the event to the dead-letter queue rather
        than being silently swallowed. Exceptions never propagate to publisher.

        If a backend is configured, the event is also forwarded to the backend.

        Returns
        -------
        The offset assigned to the event in its topic log.
        """
        # Assign offset
        offset = self._next_offset.get(topic, 0)
        self._next_offset[topic] = offset + 1

        event = FleetEvent(
            topic=topic,
            payload=payload,
            source_agent=source_agent,
            timestamp=time.time(),
            correlation_id=correlation_id,
            offset=offset,
        )

        # Append to durable log
        if topic not in self._log:
            self._log[topic] = deque(maxlen=self._retention)
        self._log[topic].append(event)

        # Delegate to backend if present
        if self._backend is not None:
            try:
                self._backend.publish(self._fleet_name, topic, event)
            except Exception:
                pass  # best-effort

        # In-memory delivery
        handlers = self._subscriptions.get(topic)
        if not handlers:
            return offset

        for sub_id, handler in list(handlers.items()):
            try:
                handler(event)
            except Exception as exc:
                # Route to dead-letter queue instead of silent discard
                self._dead_letters.append(
                    DeadLetter(
                        event=event,
                        handler_id=sub_id,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )

        return offset

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

    # ------------------------------------------------------------------
    # Kafka-like: Durable log, Replay, DLQ
    # ------------------------------------------------------------------

    def replay(
        self,
        topic: str,
        handler: EventHandler,
        *,
        from_offset: int = 0,
        to_offset: int | None = None,
    ) -> int:
        """Replay past events from the topic log to a handler.

        Delivers events with offset >= from_offset (and < to_offset if set)
        to the given handler. Useful for:
        - Crash recovery: new handler catches up on missed events.
        - Debugging: re-process a specific offset range.
        - Testing: deterministic replay of recorded event streams.

        Handler exceptions during replay route to the DLQ (same as live).

        Parameters
        ----------
        topic:
            The topic to replay from.
        handler:
            The handler to deliver replayed events to.
        from_offset:
            Start offset (inclusive). Defaults to 0 (beginning of log).
        to_offset:
            End offset (exclusive). None means replay to the end.

        Returns
        -------
        Number of events replayed.
        """
        log = self._log.get(topic)
        if not log:
            return 0

        count = 0
        for event in log:
            if event.offset < from_offset:
                continue
            if to_offset is not None and event.offset >= to_offset:
                break
            try:
                handler(event)
            except Exception as exc:
                self._dead_letters.append(
                    DeadLetter(
                        event=event,
                        handler_id="replay",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
            count += 1

        return count

    def latest_offset(self, topic: str) -> int:
        """Return the next offset that will be assigned for a topic.

        If the topic has never been published to, returns 0.
        The last written event has offset = latest_offset(topic) - 1.
        """
        return self._next_offset.get(topic, 0)

    def read_log(
        self,
        topic: str,
        *,
        from_offset: int = 0,
        limit: int | None = None,
    ) -> list[FleetEvent]:
        """Read events from the topic log without invoking any handler.

        Useful for inspection, debugging, and building materialized views.

        Parameters
        ----------
        topic:
            The topic to read from.
        from_offset:
            Start offset (inclusive).
        limit:
            Max number of events to return. None = all from from_offset.

        Returns
        -------
        List of FleetEvent objects in offset order.
        """
        log = self._log.get(topic)
        if not log:
            return []

        events = [e for e in log if e.offset >= from_offset]
        if limit is not None:
            events = events[:limit]
        return events

    def dead_letters(self, *, limit: int = 100) -> list[DeadLetter]:
        """Return the most recent dead-letter entries.

        Parameters
        ----------
        limit:
            Max entries to return (most recent first). Defaults to 100.

        Returns
        -------
        List of DeadLetter entries, newest first.
        """
        entries = list(self._dead_letters)
        return list(reversed(entries[-limit:]))

    def retry_dead_letters(
        self,
        *,
        topic: str | None = None,
        limit: int = 50,
    ) -> tuple[int, int]:
        """Retry delivering dead-lettered events.

        Re-delivers events that previously failed. Successes are removed
        from the DLQ. New failures remain.

        Parameters
        ----------
        topic:
            If set, only retry dead letters for this topic. None = all.
        limit:
            Max number of dead letters to attempt. Defaults to 50.

        Returns
        -------
        (succeeded, failed) — counts of retried events.
        """
        to_retry: list[DeadLetter] = []
        remaining: list[DeadLetter] = []

        for dl in self._dead_letters:
            if topic is not None and dl.event.topic != topic:
                remaining.append(dl)
            elif len(to_retry) < limit:
                to_retry.append(dl)
            else:
                remaining.append(dl)

        succeeded = 0
        failed = 0

        for dl in to_retry:
            handlers = self._subscriptions.get(dl.event.topic, {})
            handler = handlers.get(dl.handler_id)
            if handler is None:
                # Handler gone — drop the dead letter (can't retry)
                succeeded += 1
                continue
            try:
                handler(dl.event)
                succeeded += 1
            except Exception as exc:
                # Still failing — put back with updated error
                remaining.append(
                    DeadLetter(
                        event=dl.event,
                        handler_id=dl.handler_id,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                failed += 1

        self._dead_letters = deque(remaining, maxlen=self._dead_letters.maxlen)
        return succeeded, failed
