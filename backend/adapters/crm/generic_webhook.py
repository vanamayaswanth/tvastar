"""Generic webhook CRM adapter — for CRMs that push via webhooks."""
from uuid import UUID


class GenericWebhookAdapter:
    async def transform_inbound(self, raw: dict) -> dict:
        raise NotImplementedError

    async def sync_outcome(self, lead_id: UUID, outcome: dict) -> dict:
        raise NotImplementedError

    async def sync_site_visit(self, lead_id: UUID, status: str) -> dict:
        raise NotImplementedError
