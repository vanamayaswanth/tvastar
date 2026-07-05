def exponential_backoff(attempt: int, base_delay_s: float = 2.0, max_delay_s: float = 3600.0) -> float:
    """Compute delay for attempt n: base * 2^(n-1), capped at max_delay_s."""
    delay = base_delay_s * (2 ** (attempt - 1))
    return min(delay, max_delay_s)
