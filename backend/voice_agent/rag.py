"""Qdrant retrieval during live calls."""
from uuid import UUID


async def retrieve_context(tenant_id: UUID, project_id: UUID, query: str) -> list[str]:
    """Retrieve relevant KB chunks for the agent's current conversation turn."""
    raise NotImplementedError
