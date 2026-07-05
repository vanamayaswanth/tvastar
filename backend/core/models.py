from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from .types import CallDisposition, ConsentStatus, LeadClassification, LeadStage, Role


@dataclass(frozen=True)
class TenantContext:
    tenant_id: UUID
    user_id: UUID
    role: Role
    project_ids: list[UUID]


@dataclass(frozen=True)
class Lead:
    id: UUID
    tenant_id: UUID
    project_id: UUID
    prospect_name: str
    phone_number: str
    stage: LeadStage
    consent_status: ConsentStatus
    engagement_locked: bool
    lead_score: int | None = None
    classification: LeadClassification | None = None


@dataclass(frozen=True)
class CallRecord:
    id: UUID
    tenant_id: UUID
    lead_id: UUID
    project_id: UUID
    disposition: CallDisposition
    duration_seconds: int
    started_at: datetime
    ended_at: datetime | None = None


@dataclass(frozen=True)
class CallSummary:
    call_id: UUID
    qualification_outcome: str
    topics_discussed: list[str]
    next_action: str


@dataclass(frozen=True)
class LeadScore:
    lead_id: UUID
    score: int
    classification: LeadClassification
    signal_breakdown: dict[str, float]


@dataclass(frozen=True)
class SiteVisit:
    id: UUID
    tenant_id: UUID
    lead_id: UUID
    project_id: UUID
    status: str
    preferred_date: str | None = None
