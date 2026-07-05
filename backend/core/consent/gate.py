from ..errors import ConsentBlockedError, Err, Ok, Result
from ..types import ConsentStatus


def consent_gate(consent_status: ConsentStatus, lead_id: str) -> Result:
    """Hard-gate: only GRANTED consent allows outbound communication."""
    if consent_status == ConsentStatus.GRANTED:
        return Ok(True)
    return Err(ConsentBlockedError(lead_id=lead_id, reason=f"consent is {consent_status.value}"))
