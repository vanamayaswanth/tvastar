import math
from decimal import Decimal


def compute_cost(duration_seconds: int, rate_per_minute: Decimal) -> Decimal:
    """Cost = ceil(duration/60) * rate. Rounds up partial minutes."""
    minutes = math.ceil(duration_seconds / 60)
    return Decimal(minutes) * rate_per_minute
