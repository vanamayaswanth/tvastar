"""Property tests for consent gate."""
from hypothesis import given, strategies as st

from core.types import ConsentStatus
from core.consent.gate import consent_gate
from core.errors import Ok, Err


@given(st.sampled_from(list(ConsentStatus)))
def test_only_granted_passes(status):
    """Only GRANTED consent produces Ok; everything else is Err."""
    result = consent_gate(status, "test-lead")
    if status == ConsentStatus.GRANTED:
        assert isinstance(result, Ok)
    else:
        assert isinstance(result, Err)
