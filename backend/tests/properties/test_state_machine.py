"""Property tests for lead stage machine."""
from hypothesis import given, strategies as st

from core.types import LeadStage
from core.lead.state_machine import lead_stage_transition, TRANSITIONS
from core.errors import Ok, Err


@given(st.sampled_from(list(LeadStage)))
def test_valid_transitions_produce_valid_stages(stage):
    """Any valid (stage, event) pair produces a LeadStage, never crashes."""
    edges = TRANSITIONS.get(stage, {})
    for event in edges:
        result = lead_stage_transition(stage, event)
        assert isinstance(result, Ok)
        assert isinstance(result.value, LeadStage)


@given(st.sampled_from(list(LeadStage)), st.text(min_size=1))
def test_invalid_events_return_err(stage, event):
    """Unknown events always return Err, never crash."""
    edges = TRANSITIONS.get(stage, {})
    if event not in edges:
        result = lead_stage_transition(stage, event)
        assert isinstance(result, Err)
