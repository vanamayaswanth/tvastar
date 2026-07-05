"""Temporal activity stubs — side-effectful operations called by workflows."""
from uuid import UUID


async def check_consent_activity(lead_id: UUID) -> str:
    raise NotImplementedError


async def schedule_call_activity(lead_id: UUID, priority: int) -> dict:
    raise NotImplementedError


async def send_warmup_message_activity(lead_id: UUID) -> dict:
    raise NotImplementedError


async def assign_lead_activity(lead_id: UUID) -> dict:
    raise NotImplementedError


async def sync_crm_activity(payload: dict) -> dict:
    raise NotImplementedError
