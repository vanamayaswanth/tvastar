from uuid import UUID

from ..types import AvailabilityStatus


def round_robin(
    project_id: UUID,
    salespersons: list[dict],
    pointer: int,
) -> tuple[UUID | None, int]:
    """Return next available salesperson ID and updated pointer. Skips unavailable."""
    available = [
        sp for sp in salespersons
        if sp["availability"] not in (AvailabilityStatus.OFFLINE, AvailabilityStatus.BUSY, AvailabilityStatus.AT_CAPACITY)
    ]
    if not available:
        return None, pointer
    idx = pointer % len(available)
    return available[idx]["id"], pointer + 1
