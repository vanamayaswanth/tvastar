from dataclasses import dataclass
from datetime import datetime, timedelta

COOLING_OFF_HOURS = 4


@dataclass(frozen=True)
class CoolingOffClear:
    pass


@dataclass(frozen=True)
class CoolingOffActive:
    next_allowed_at: datetime


CoolingOffResult = CoolingOffClear | CoolingOffActive


def check_cooling_off(last_called_at: datetime | None, now: datetime) -> CoolingOffResult:
    """Return CoolingOffActive if phone was called within last 4 hours."""
    if last_called_at is None:
        return CoolingOffClear()
    boundary = last_called_at + timedelta(hours=COOLING_OFF_HOURS)
    if now < boundary:
        return CoolingOffActive(next_allowed_at=boundary)
    return CoolingOffClear()
