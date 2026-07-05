"""StoragePort implementation using S3/MinIO with tenant-prefixed paths."""
from uuid import UUID


class S3StorageAdapter:
    async def upload(self, tenant_id: UUID, path: str, data: bytes, content_type: str) -> str:
        """Upload to /{tenant_id}/{path}."""
        raise NotImplementedError

    async def download(self, tenant_id: UUID, path: str) -> bytes:
        raise NotImplementedError

    async def delete(self, tenant_id: UUID, path: str) -> None:
        raise NotImplementedError
