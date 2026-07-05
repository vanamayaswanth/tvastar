"""NATS JetStream consumer stubs for event processing."""


async def lead_event_consumer() -> None:
    """Subscribe to tenant.*.lead.* subjects."""
    raise NotImplementedError


async def call_event_consumer() -> None:
    """Subscribe to tenant.*.call.* subjects."""
    raise NotImplementedError
