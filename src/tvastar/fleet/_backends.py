"""Optional backend protocols and lazy import helpers for fleet module.

Defines the StateBackend and EventBackend protocols that optional persistent
backends (Redis, Kafka) must implement, plus a lazy-import utility that raises
a descriptive ImportError naming the missing extras package.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from tvastar.fleet.bus import FleetEvent

# ---------------------------------------------------------------------------
# Type aliases (kept here to avoid circular imports at runtime)
# ---------------------------------------------------------------------------

EventHandler = Callable[..., Any]
"""Callable that handles a FleetEvent. Signature: (FleetEvent) -> Any."""


# ---------------------------------------------------------------------------
# Backend Protocols
# ---------------------------------------------------------------------------


class StateBackend(Protocol):
    """Protocol for persistent shared-state backends (e.g. Redis)."""

    def get(self, fleet_name: str, key: str) -> Any | None:
        """Retrieve a value by fleet-scoped key, or None if not found."""
        ...

    def set(self, fleet_name: str, key: str, value: Any, version: int) -> None:
        """Store a value with its version number."""
        ...

    def delete(self, fleet_name: str, key: str) -> bool:
        """Delete a key. Returns True if it existed, False otherwise."""
        ...


class EventBackend(Protocol):
    """Protocol for persistent event-bus backends (e.g. Kafka)."""

    def publish(self, fleet_name: str, topic: str, event: FleetEvent) -> None:
        """Publish an event to a topic within a fleet."""
        ...

    def subscribe(self, fleet_name: str, topic: str, handler: EventHandler) -> str:
        """Subscribe a handler to a topic. Returns a subscription ID."""
        ...

    def unsubscribe(self, subscription_id: str) -> bool:
        """Remove a subscription. Returns True if it existed."""
        ...


# ---------------------------------------------------------------------------
# Lazy import helper
# ---------------------------------------------------------------------------


def require_backend(backend_type: str, extras_package: str) -> Any:
    """Attempt to import an optional backend module.

    Parameters
    ----------
    backend_type:
        Short identifier for the backend (e.g. "redis", "kafka").
    extras_package:
        The pip extras name to install (e.g. "tvastar[redis]").

    Returns
    -------
    The imported backend module.

    Raises
    ------
    ImportError
        If the backend's dependencies are not installed, with a message
        naming the missing extras package and the install command.
    """
    try:
        if backend_type == "redis":
            import redis  # type: ignore[import-untyped]

            return redis
        elif backend_type == "kafka":
            import kafka  # type: ignore[import-untyped]

            return kafka
        else:
            raise ImportError(
                f"Unknown backend type '{backend_type}'. Supported backends: 'redis', 'kafka'."
            )
    except ImportError:
        raise ImportError(
            f"{backend_type.capitalize()} backend requires the '{extras_package}' extra. "
            f"Install it with: pip install {extras_package}"
        ) from None
