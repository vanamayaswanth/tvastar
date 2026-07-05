from enum import Enum


class LeadStage(str, Enum):
    RECEIVED = "received"
    CALLING = "calling"
    QUALIFIED = "qualified"
    ASSIGNED = "assigned"
    CALLBACK_TRACKING = "callback_tracking"
    SITE_VISIT_BOOKED = "site_visit_booked"
    UNREACHABLE = "unreachable"
    REQUIRING_MANUAL_REVIEW = "requiring_manual_review"


class ConsentStatus(str, Enum):
    PENDING = "pending"
    GRANTED = "granted"
    REVOKED = "revoked"


class CallDisposition(str, Enum):
    COMPLETED = "completed"
    RNR = "rnr"
    VOICEMAIL = "voicemail"
    BUSY = "busy"
    FAILED = "failed"
    TRANSFERRED = "transferred"


class LeadClassification(str, Enum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


class AvailabilityStatus(str, Enum):
    AVAILABLE = "available"
    BUSY = "busy"
    OFFLINE = "offline"
    AT_CAPACITY = "at_capacity"


class Role(str, Enum):
    SUPER_ADMIN = "super_admin"
    TENANT_ADMIN = "tenant_admin"
    SALES_MANAGER = "sales_manager"
    SALESPERSON = "salesperson"
