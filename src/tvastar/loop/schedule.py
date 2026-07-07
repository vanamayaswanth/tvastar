"""Minimal cron-expression evaluator + adaptive scheduling — zero dependencies.

Supports:
  @yearly @annually @monthly @weekly @daily @midnight @hourly
  */N * * * *   — every N minutes/hours/etc.
  0 9 * * 1-5  — 9am weekdays
  Full 5-field cron: MIN HOUR DOM MON DOW

All times are UTC. Warn if system clock appears non-UTC.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

MIN_ADAPTIVE_SECONDS = 60
MAX_ADAPTIVE_SECONDS = 86400

_ALIASES: dict[str, str] = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}


def _parse_field(field: str, lo: int, hi: int) -> list[int]:
    """Parse one cron field into a sorted list of valid int values."""
    values: set[int] = set()
    for part in field.split(","):
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
            if step <= 0:
                raise ValueError(f"Cron step must be positive, got {step!r}")
            if base == "*":
                start, end = lo, hi
            elif "-" in base:
                a, b = base.split("-", 1)
                start, end = int(a), int(b)
            else:
                start, end = int(base), hi
            values.update(range(start, end + 1, step))
        elif "-" in part:
            a, b = part.split("-", 1)
            values.update(range(int(a), int(b) + 1))
        elif part == "*":
            values.update(range(lo, hi + 1))
        else:
            values.add(int(part))
    return sorted(v for v in values if lo <= v <= hi)


def next_run_time(expr: str, after: datetime) -> datetime:
    """Return the next UTC datetime when *expr* fires strictly after *after*.

    Args:
        expr: cron expression or alias (@daily etc.)
        after: reference datetime (timezone-aware or naive UTC)

    Returns:
        timezone-aware UTC datetime of the next scheduled fire.

    Raises:
        ValueError: if expr is @manual, malformed, or produces no run in 1 year.
    """
    if expr == "@manual":
        raise ValueError(
            "@manual schedule never auto-fires. Call loop.trigger() explicitly instead."
        )

    expr = _ALIASES.get(expr, expr)
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(
            f"Invalid cron expression {expr!r}: expected 5 space-separated fields "
            f"(MIN HOUR DOM MON DOW). Got {len(parts)} fields."
        )

    mins = _parse_field(parts[0], 0, 59)
    hours = _parse_field(parts[1], 0, 23)
    doms = _parse_field(parts[2], 1, 31)
    months = _parse_field(parts[3], 1, 12)
    # cron DOW: 0=Sun, 6=Sat. Python weekday: 0=Mon, 6=Sun. Convert.
    raw_dows = _parse_field(parts[4], 0, 7)  # allow 7 as alias for 0 (Sun)
    py_dows = {(d - 1) % 7 for d in raw_dows} if raw_dows else set()

    all_dom = parts[2] == "*"
    all_dow = parts[4] == "*"

    if not mins:
        raise ValueError(f"Cron field 0 (minutes) parsed to empty set in {expr!r}")
    if not hours:
        raise ValueError(f"Cron field 1 (hours) parsed to empty set in {expr!r}")

    # Ensure timezone-aware
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)

    # Start from next minute
    t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = t + timedelta(days=366)

    while t < limit:
        # --- month ---
        if t.month not in months:
            m = t.month % 12 + 1
            y = t.year + (1 if t.month == 12 else 0)
            t = t.replace(year=y, month=m, day=1, hour=0, minute=0)
            continue

        # --- day ---
        dom_ok = all_dom or t.day in doms
        dow_ok = all_dow or t.weekday() in py_dows

        if all_dom and all_dow:
            day_ok = True
        elif all_dom:
            day_ok = dow_ok
        elif all_dow:
            day_ok = dom_ok
        else:
            day_ok = dom_ok or dow_ok  # standard cron OR semantics

        if not day_ok:
            t = t.replace(hour=0, minute=0) + timedelta(days=1)
            continue

        # --- hour ---
        if t.hour not in hours:
            nxt_h = next((h for h in hours if h > t.hour), None)
            if nxt_h is None:
                t = t.replace(hour=0, minute=0) + timedelta(days=1)
            else:
                t = t.replace(hour=nxt_h, minute=mins[0])
            continue

        # --- minute ---
        if t.minute not in mins:
            nxt_m = next((m for m in mins if m > t.minute), None)
            if nxt_m is None:
                nxt_h = next((h for h in hours if h > t.hour), None)
                if nxt_h is None:
                    t = t.replace(hour=0, minute=0) + timedelta(days=1)
                else:
                    t = t.replace(hour=nxt_h, minute=mins[0])
            else:
                t = t.replace(minute=nxt_m)
            continue

        return t

    raise ValueError(f"No scheduled run found within 1 year for expression: {expr!r}")


def resolve_next_run(
    agent_response: dict | None,
    cron_expr: str,
    after: datetime,
    *,
    adaptive_enabled: bool = False,
) -> datetime:
    """Determine next run time, considering adaptive hints.

    If adaptive_enabled and response contains valid next_run_in,
    returns after + clamped(next_run_in). Otherwise uses cron.
    One-shot: the override applies only once per hint — subsequent
    calls without a new hint revert to cron.
    """
    if adaptive_enabled and agent_response:
        hint = agent_response.get("next_run_in")
        if _is_valid_hint(hint):
            clamped = _clamp(int(hint))
            if clamped != int(hint):
                logger.warning(
                    "Adaptive schedule clamped: requested=%s, applied=%s",
                    hint,
                    clamped,
                )
            return after + timedelta(seconds=clamped)
        elif hint is not None:
            logger.warning("Invalid adaptive hint ignored: %r", hint)
    return next_run_time(cron_expr, after)


def _is_valid_hint(value: Any) -> bool:
    """True iff value is a positive integer (not bool)."""
    if not isinstance(value, int) or isinstance(value, bool):
        return False
    return value > 0


def _clamp(seconds: int) -> int:
    """Clamp to [MIN_ADAPTIVE_SECONDS, MAX_ADAPTIVE_SECONDS]."""
    return max(MIN_ADAPTIVE_SECONDS, min(MAX_ADAPTIVE_SECONDS, seconds))


__all__ = ["next_run_time", "resolve_next_run", "MIN_ADAPTIVE_SECONDS", "MAX_ADAPTIVE_SECONDS"]
