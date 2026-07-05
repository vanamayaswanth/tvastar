from typing import Protocol
from uuid import UUID


class KnowledgePort(Protocol):
    async def index_document(self, tenant_id: UUID, project_id: UUID, content: bytes, metadata: dict) -> str: ...
    async def search(self, tenant_id: UUID, project_id: UUID, query: str, top_k: int = 5) -> list[dict]: ...
