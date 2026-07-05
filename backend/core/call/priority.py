from datetime import datetime

from ..types import LeadClassification

# ponytail: Classification ordering — Hot < Warm < Cold for sort key (lower = higher priority)
_PRIORITY = {LeadClassification.HOT: 0, LeadClassification.WARM: 1, LeadClassification.COLD: 2}


def priority_sort(items: list[dict]) -> list[dict]:
    """Sort call queue items by classification (hot first) then by created_at (oldest first)."""
    return sorted(items, key=lambda x: (_PRIORITY.get(x["classification"], 99), x["created_at"]))
