"""Tenant-scoped SQLAlchemy session factory."""
from uuid import UUID


async def get_tenant_session(tenant_id: UUID):
    """Create a session with app.current_tenant_id set for RLS enforcement."""
    # ponytail: Will wrap async_sessionmaker + SET app.current_tenant_id per connection
    raise NotImplementedError
