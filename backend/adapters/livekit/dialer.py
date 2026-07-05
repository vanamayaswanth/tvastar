"""TelephonyPort implementation using LiveKit SIP."""
from uuid import UUID


class LiveKitDialer:
    """Manages outbound SIP calls via LiveKit."""

    async def initiate_call(self, lead_id: UUID, phone: str, caller_id: str) -> dict:
        raise NotImplementedError

    async def transfer_call(self, call_id: UUID, target_phone: str, context: str) -> dict:
        raise NotImplementedError

    async def end_call(self, call_id: UUID) -> None:
        raise NotImplementedError
