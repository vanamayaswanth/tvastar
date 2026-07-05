from ..errors import Err, InvalidTransitionError, Ok, Result
from ..types import LeadStage

# ponytail: Transition table is the single source of truth. Add edges here as stages are finalized.
TRANSITIONS: dict[LeadStage, dict[str, LeadStage]] = {
    LeadStage.RECEIVED: {"schedule_call": LeadStage.CALLING},
    LeadStage.CALLING: {
        "qualify": LeadStage.QUALIFIED,
        "mark_unreachable": LeadStage.UNREACHABLE,
        "manual_review": LeadStage.REQUIRING_MANUAL_REVIEW,
    },
    LeadStage.QUALIFIED: {"assign": LeadStage.ASSIGNED},
    LeadStage.ASSIGNED: {"track_callback": LeadStage.CALLBACK_TRACKING},
    LeadStage.CALLBACK_TRACKING: {"book_site_visit": LeadStage.SITE_VISIT_BOOKED},
}


def lead_stage_transition(current: LeadStage, event: str) -> Result:
    edges = TRANSITIONS.get(current, {})
    next_stage = edges.get(event)
    if next_stage is None:
        return Err(InvalidTransitionError(from_stage=current.value, event=event))
    return Ok(next_stage)
