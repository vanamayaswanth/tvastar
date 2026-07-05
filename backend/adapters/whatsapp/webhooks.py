"""WhatsApp webhook verification and payload parsing."""


async def verify_webhook(token: str, challenge: str) -> str:
    """Verify webhook subscription from Meta."""
    raise NotImplementedError


async def parse_webhook(payload: dict) -> dict:
    """Parse inbound message/status webhook payload."""
    raise NotImplementedError
