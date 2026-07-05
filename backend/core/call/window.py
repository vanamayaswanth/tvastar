from datetime import datetime, time


def is_within_call_window(
    ts: datetime,
    start_hour: int = 10,
    end_hour: int = 20,
    tz_name: str = "Asia/Kolkata",
) -> bool:
    """Check if timestamp falls within [start_hour, end_hour) in given timezone."""
    from zoneinfo import ZoneInfo

    local = ts.astimezone(ZoneInfo(tz_name))
    return time(start_hour) <= local.time() < time(end_hour)
