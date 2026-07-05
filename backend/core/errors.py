from dataclasses import dataclass
from typing import Generic, TypeVar, Union

T = TypeVar("T")
E = TypeVar("E")


@dataclass(frozen=True)
class Ok(Generic[T]):
    value: T


@dataclass(frozen=True)
class Err(Generic[E]):
    error: E


Result = Union[Ok[T], Err[E]]


# Domain errors
@dataclass(frozen=True)
class ValidationError:
    field: str
    message: str


@dataclass(frozen=True)
class ConsentBlockedError:
    lead_id: str
    reason: str


@dataclass(frozen=True)
class EngagementLockedError:
    lead_id: str


@dataclass(frozen=True)
class CoolingOffActiveError:
    phone: str
    next_allowed_at: str


@dataclass(frozen=True)
class InvalidTransitionError:
    from_stage: str
    event: str
