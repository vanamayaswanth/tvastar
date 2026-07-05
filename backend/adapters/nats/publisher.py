"""Tenant-scoped NATS JetStream publisher."""
from uuid import UUID


async def publish(tenant_id: UUID, subject: str, payload: bytes) -> None:
    """Publish to tenant.{tenant_id}.{subject}."""
    # ponytail: Will use nats-py JetStream client, serialize with msgpack or json
    raise NotImplementedError
