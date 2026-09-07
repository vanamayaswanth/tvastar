"""Opt-in, payload-free execution lineage for Session invocations.

Lineage records contain correlation metadata only. Persistence is best-effort and
uses direct Store keys; it does not provide compare-and-swap or audit guarantees.
Session references are deterministic pseudonyms, not anonymous identifiers.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Iterator, Optional

if TYPE_CHECKING:
    from .memory.store import Store

_VERSION = 1
_ENVELOPE_SCHEMA = "tvastar.execution.envelope"
_OUTCOME_SCHEMA = "tvastar.execution.outcome"
_SCOPE_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_EXECUTION_RE = re.compile(r"^exec_[0-9a-f]{32}$")
_SESSION_REF_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_DEPTH = 20
_MAX_VISITED = 10_000


@dataclass
class ExecutionContext:
    """Mutable recorder state; intentionally not exported from the root package."""

    execution_id: str
    root_execution_id: str
    scope: Optional[str]
    spawned_by: Optional[str] = None
    attempt_count: int = 0
    attempt_trace: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    started_monotonic: float = field(default_factory=time.monotonic)
    completed_at: Optional[float] = None
    duration_ms: Optional[float] = None


@dataclass(frozen=True)
class ExecutionContextSnapshot:
    """Immutable identity visible to code running inside an execution."""

    execution_id: str
    root_execution_id: str
    scope: Optional[str]
    spawned_by: Optional[str] = None


@dataclass(frozen=True)
class _TaskParent:
    """Identity handed explicitly from Session.task() to one child invocation."""

    execution_id: str
    root_execution_id: str
    scope: Optional[str]


@dataclass(frozen=True)
class ExecutionEnvelope:
    """Persisted start metadata; ``session_ref`` is pseudonymous, not anonymous."""

    schema: str
    version: int
    scope: str
    execution_id: str
    root_execution_id: str
    spawned_by: Optional[str]
    session_ref: str
    invocation: str
    started_at: float


@dataclass(frozen=True)
class ExecutionOutcome:
    schema: str
    version: int
    scope: str
    execution_id: str
    status: str
    completed_at: float
    duration_ms: float
    attempt_count: int
    attempt_trace: list[str]


@dataclass(frozen=True)
class LineageDiagnostic:
    code: str
    execution_id: str
    detail: str = ""


@dataclass
class ExecutionView:
    """Direct view of one execution without parent traversal."""

    envelope: Optional[ExecutionEnvelope] = None
    outcome: Optional[ExecutionOutcome] = None
    diagnostics: list[LineageDiagnostic] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.envelope is not None and self.outcome is not None and not self.diagnostics


@dataclass
class ExecutionLineage:
    executions: list[ExecutionEnvelope] = field(default_factory=list)
    outcomes: dict[str, ExecutionOutcome] = field(default_factory=dict)
    diagnostics: list[LineageDiagnostic] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return (
            bool(self.executions)
            and not self.diagnostics
            and all(execution.execution_id in self.outcomes for execution in self.executions)
        )


_active_execution: ContextVar[Optional[ExecutionContext]] = ContextVar(
    "tvastar_execution_context", default=None
)


def validate_lineage_scope(scope: str) -> str:
    """Return a valid lineage scope or raise ValueError at the configuration boundary."""
    if not isinstance(scope, str) or _SCOPE_RE.fullmatch(scope) is None:
        raise ValueError("lineage_scope must match [A-Za-z0-9._-]{1,128}")
    return scope


def _validate_execution_id(execution_id: str) -> str:
    if not isinstance(execution_id, str) or _EXECUTION_RE.fullmatch(execution_id) is None:
        raise ValueError("execution_id must be exec_<uuid4 hex>")
    return execution_id


def _session_ref(session_id: str) -> str:
    """Return a deterministic, linkable pseudonym; this is not anonymization."""
    return f"sha256:{hashlib.sha256(session_id.encode('utf-8')).hexdigest()}"


def new_execution_id() -> str:
    return f"exec_{uuid.uuid4().hex}"


def get_execution_context() -> Optional[ExecutionContextSnapshot]:
    """Return an immutable copy of the active execution identity."""
    context = _active_execution.get()
    if context is None:
        return None
    return ExecutionContextSnapshot(
        execution_id=context.execution_id,
        root_execution_id=context.root_execution_id,
        scope=context.scope,
        spawned_by=context.spawned_by,
    )


def _get_execution_state() -> Optional[ExecutionContext]:
    return _active_execution.get()


@contextmanager
def _execution_context(context: ExecutionContext) -> Iterator[ExecutionContext]:
    token = _active_execution.set(context)
    try:
        yield context
    finally:
        _active_execution.reset(token)


def _task_parent_snapshot() -> Optional[_TaskParent]:
    context = _active_execution.get()
    if context is None:
        return None
    return _TaskParent(
        execution_id=context.execution_id,
        root_execution_id=context.root_execution_id,
        scope=context.scope,
    )


def _create_execution_context(
    scope: Optional[str], parent: Optional[_TaskParent] = None
) -> ExecutionContext:
    """Create a root unless Session.task() explicitly supplied its parent."""
    execution_id = new_execution_id()
    return ExecutionContext(
        execution_id=execution_id,
        root_execution_id=parent.root_execution_id if parent else execution_id,
        scope=parent.scope if parent else scope,
        spawned_by=parent.execution_id if parent else None,
    )


def execution_key(scope: str, execution_id: str) -> str:
    return f"lineage:v1:{validate_lineage_scope(scope)}:execution:{execution_id}"


def outcome_key(scope: str, execution_id: str) -> str:
    return f"lineage:v1:{validate_lineage_scope(scope)}:outcome:{execution_id}"


class ExecutionRecorder:
    """Best-effort create-once recorder over the existing Store interface."""

    def __init__(self, store: "Store", scope: str) -> None:
        self.store = store
        self.scope = validate_lineage_scope(scope)

    def start(self, context: ExecutionContext, *, session_id: str, invocation: str) -> bool:
        if context.scope != self.scope:
            return False
        record = ExecutionEnvelope(
            schema=_ENVELOPE_SCHEMA,
            version=_VERSION,
            scope=self.scope,
            execution_id=context.execution_id,
            root_execution_id=context.root_execution_id,
            spawned_by=context.spawned_by,
            session_ref=_session_ref(session_id),
            invocation=invocation,
            started_at=context.started_at,
        )
        return self._create_once(execution_key(self.scope, context.execution_id), asdict(record))

    def finish(self, context: ExecutionContext, *, status: str) -> bool:
        if context.scope != self.scope:
            return False
        if context.completed_at is None:
            context.completed_at = time.time()
            context.duration_ms = max(0.0, (time.monotonic() - context.started_monotonic) * 1000)
        record = ExecutionOutcome(
            schema=_OUTCOME_SCHEMA,
            version=_VERSION,
            scope=self.scope,
            execution_id=context.execution_id,
            status=status,
            completed_at=context.completed_at,
            duration_ms=context.duration_ms if context.duration_ms is not None else 0.0,
            attempt_count=context.attempt_count,
            attempt_trace=list(context.attempt_trace),
        )
        return self._create_once(outcome_key(self.scope, context.execution_id), asdict(record))

    @staticmethod
    def _equivalent(existing: Any, value: dict[str, Any]) -> bool:
        if existing == value:
            return True
        if isinstance(existing, (str, bytes, bytearray)):
            try:
                return json.loads(existing) == value
            except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                return False
        return False

    def _create_once(self, key: str, value: dict[str, Any]) -> bool:
        try:
            existing = self.store.get(key)
            if existing is not None:
                return self._equivalent(existing, value)
            self.store.set(key, value)
            return self._equivalent(self.store.get(key), value)
        except Exception:
            return False


def _read_record(
    store: "Store", scope: str, execution_id: str, record_type: str
) -> tuple[Optional[ExecutionEnvelope | ExecutionOutcome], Optional[LineageDiagnostic]]:
    is_envelope = record_type == "execution"
    key = execution_key(scope, execution_id) if is_envelope else outcome_key(scope, execution_id)
    expected_schema = _ENVELOPE_SCHEMA if is_envelope else _OUTCOME_SCHEMA
    try:
        raw = store.get(key)
    except Exception as exc:
        return None, LineageDiagnostic("corrupt", execution_id, type(exc).__name__)
    if raw is None:
        return None, LineageDiagnostic("missing", execution_id, record_type)
    if not isinstance(raw, dict):
        return None, LineageDiagnostic("corrupt", execution_id, record_type)
    if (
        raw.get("schema") != expected_schema
        or not isinstance(raw.get("version"), int)
        or isinstance(raw.get("version"), bool)
        or raw.get("version") != _VERSION
    ):
        return None, LineageDiagnostic("unknown", execution_id, record_type)
    if raw.get("scope") != scope:
        return None, LineageDiagnostic("cross_scope", execution_id, str(raw.get("scope", "")))
    try:
        record: ExecutionEnvelope | ExecutionOutcome
        record = ExecutionEnvelope(**raw) if is_envelope else ExecutionOutcome(**raw)
    except (TypeError, ValueError):
        return None, LineageDiagnostic("corrupt", execution_id, record_type)
    if record.execution_id != execution_id or _EXECUTION_RE.fullmatch(record.execution_id) is None:
        return None, LineageDiagnostic("corrupt", execution_id, record_type)
    if isinstance(record, ExecutionEnvelope):
        if (
            not isinstance(record.root_execution_id, str)
            or _EXECUTION_RE.fullmatch(record.root_execution_id) is None
            or (
                record.spawned_by is not None
                and (
                    not isinstance(record.spawned_by, str)
                    or _EXECUTION_RE.fullmatch(record.spawned_by) is None
                )
            )
            or not isinstance(record.session_ref, str)
            or _SESSION_REF_RE.fullmatch(record.session_ref) is None
            or not isinstance(record.started_at, (int, float))
            or isinstance(record.started_at, bool)
        ):
            return None, LineageDiagnostic("corrupt", execution_id, record_type)
        if not isinstance(record.invocation, str) or record.invocation not in {
            "prompt",
            "skill",
            "stream",
        }:
            return None, LineageDiagnostic("unknown", execution_id, "invocation")
    else:
        if (
            not isinstance(record.completed_at, (int, float))
            or isinstance(record.completed_at, bool)
            or not isinstance(record.duration_ms, (int, float))
            or isinstance(record.duration_ms, bool)
            or record.duration_ms < 0
            or not isinstance(record.attempt_count, int)
            or isinstance(record.attempt_count, bool)
            or record.attempt_count < 0
            or not isinstance(record.attempt_trace, list)
            or not all(isinstance(marker, str) for marker in record.attempt_trace)
        ):
            return None, LineageDiagnostic("corrupt", execution_id, record_type)
        if not isinstance(record.status, str) or record.status not in {
            "completed",
            "failed",
            "cancelled",
            "timed_out",
        }:
            return None, LineageDiagnostic("unknown", execution_id, "status")
    return record, None


class ExecutionQuery:
    """Direct-key execution queries; methods never call ``Store.keys()``."""

    def __init__(self, store: "Store", scope: str) -> None:
        self.store = store
        self.scope = validate_lineage_scope(scope)

    def get(self, execution_id: str) -> ExecutionView:
        """Read exactly one envelope and outcome without following ``spawned_by``."""
        execution_id = _validate_execution_id(execution_id)
        view = ExecutionView()
        envelope, diagnostic = _read_record(self.store, self.scope, execution_id, "execution")
        if diagnostic is not None:
            view.diagnostics.append(diagnostic)
            return view
        assert isinstance(envelope, ExecutionEnvelope)
        view.envelope = envelope

        outcome, diagnostic = _read_record(self.store, self.scope, execution_id, "outcome")
        if diagnostic is not None:
            view.diagnostics.append(diagnostic)
        elif isinstance(outcome, ExecutionOutcome):
            view.outcome = outcome
        return view

    def ancestors(
        self,
        execution_id: str,
        max_depth: int = _MAX_DEPTH,
        max_visited: int = _MAX_VISITED,
    ) -> ExecutionLineage:
        """Read one execution and its ancestors through explicit parent keys only."""
        execution_id = _validate_execution_id(execution_id)
        if not 1 <= max_depth <= _MAX_DEPTH:
            raise ValueError("max_depth must be between 1 and 20")
        if not 1 <= max_visited <= _MAX_VISITED:
            raise ValueError("max_visited must be between 1 and 10000")

        result = ExecutionLineage()
        current: Optional[str] = execution_id
        visited: set[str] = set()
        depth = 0
        while current is not None:
            if current in visited:
                result.diagnostics.append(LineageDiagnostic("cycle", current))
                break
            if len(visited) >= max_visited:
                result.diagnostics.append(LineageDiagnostic("max_visited", current))
                break
            if depth >= max_depth:
                result.diagnostics.append(LineageDiagnostic("max_depth", current))
                break
            visited.add(current)
            depth += 1

            view = self.get(current)
            result.diagnostics.extend(view.diagnostics)
            if view.envelope is None:
                break
            result.executions.append(view.envelope)
            if view.outcome is not None:
                result.outcomes[current] = view.outcome
            current = view.envelope.spawned_by
        return result


def get_execution_lineage(
    store: "Store",
    *,
    scope: str,
    execution_id: str,
    max_depth: int = _MAX_DEPTH,
    max_visited: int = _MAX_VISITED,
) -> ExecutionLineage:
    """Compatibility convenience delegating to :meth:`ExecutionQuery.ancestors`."""
    return ExecutionQuery(store, scope).ancestors(execution_id, max_depth, max_visited)
