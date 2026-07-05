from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from .types import CallDisposition, LeadClassification, LeadStage


@dataclass(frozen=True)
class LeadCreatedEvent:
    tenant_id: UUID
    lead_id: UUID
    project_id: UUID
    timestamp: datetime


@dataclass(frozen=True)
class LeadStageChangedEvent:
    tenant_id: UUID
    lead_id: UUID
    from_stage: LeadStage
    to_stage: LeadStage
    timestamp: datetime


@dataclass(frozen=True)
class CallScheduledEvent:
    tenant_id: UUID
    lead_id: UUID
    project_id: UUID
    scheduled_at: datetime


@dataclass(frozen=True)
class CallCompletedEvent:
    tenant_id: UUID
    lead_id: UUID
    call_id: UUID
    disposition: CallDisposition
    duration_seconds: int
    timestamp: datetime


@dataclass(frozen=True)
class CallRNREvent:
    tenant_id: UUID
    lead_id: UUID
    call_id: UUID
    retry_number: int
    timestamp: datetime


@dataclass(frozen=True)
class AssignmentCreatedEvent:
    tenant_id: UUID
    lead_id: UUID
    salesperson_id: UUID
    timestamp: datetime


@dataclass(frozen=True)
class HotLeadNotificationEvent:
    tenant_id: UUID
    lead_id: UUID
    salesperson_id: UUID
    score: int
    classification: LeadClassification
    timestamp: datetime


@dataclass(frozen=True)
class ConsentChangedEvent:
    tenant_id: UUID
    lead_id: UUID
    new_status: str
    timestamp: datetime


@dataclass(frozen=True)
class SiteVisitCapturedEvent:
    tenant_id: UUID
    lead_id: UUID
    project_id: UUID
    preferred_date: str | None
    timestamp: datetime
