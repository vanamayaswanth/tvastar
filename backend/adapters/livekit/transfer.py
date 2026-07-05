"""Warm transfer logic — bridges AI call to human salesperson."""
from uuid import UUID


async def warm_transfer(call_id: UUID, salesperson_phone: str, context_summary: str) -> dict:
    """Transfer active call with context handoff."""
    # ponytail: Will use LiveKit room participant transfer + SIP dial to salesperson
    raise NotImplementedError
