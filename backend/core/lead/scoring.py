from ..models import CallSummary, LeadScore
from ..types import LeadClassification


def score_lead(summary: CallSummary, thresholds: dict[str, int] | None = None) -> LeadScore:
    """Score a lead based on call summary signals. Stub — returns cold/0."""
    # ponytail: Scoring logic TBD. Will use signal_breakdown from conversation.
    return LeadScore(
        lead_id=summary.call_id,
        score=0,
        classification=LeadClassification.COLD,
        signal_breakdown={},
    )
