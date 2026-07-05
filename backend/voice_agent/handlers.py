"""Call event handlers — react to LiveKit room events."""


async def on_call_connected(event: dict) -> None:
    """Start AI conversation when call connects."""
    raise NotImplementedError


async def on_call_ended(event: dict) -> None:
    """Generate summary, publish call.completed event."""
    raise NotImplementedError


async def on_transfer_requested(event: dict) -> None:
    """Initiate warm transfer to salesperson."""
    raise NotImplementedError
