"""Loop engineering layer — run agents on a schedule with verify + handoff.

Werner principle: everything fails, all the time. Design for it.

Lifecycle:
    IDLE → TRIGGERED → RUNNING → VERIFYING → PASS → back to IDLE
                           │                   │
                      INTERRUPTED            FAIL → RETRY (backoff)
                      (crash recovery)             → HANDOFF → HANDOFF_FAILED
                                                   → SUSPENDED (circuit breaker)
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    import pathlib

    from ..agent import AgentSpec
    from ..fleet.swarm import EscalationPolicy
    from ..memory.store import Store
    from ..model.base import Model
    from ..observability import Tracer
    from .classifiers import ClassificationResult, ErrorClassifier
    from .handoff import HandoffPolicy


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class LoopState(str, Enum):
    IDLE = "idle"
    TRIGGERED = "triggered"
    RUNNING = "running"
    VERIFYING = "verifying"
    PASS = "pass"
    FAIL = "fail"
    RETRY = "retry"
    HANDOFF = "handoff"
    HANDOFF_FAILED = "handoff_failed"  # handoff itself threw
    INTERRUPTED = "interrupted"  # crash recovery: was RUNNING on startup
    SUSPENDED = "suspended"  # circuit breaker: too many consecutive failures
    ESCALATING = "escalating"  # waiting for directive from Coordinator (swarm)
    DEGRADED = "degraded"  # proceeding with reduced scope (swarm)


class FailureKind(str, Enum):
    TIMEOUT = "timeout"  # cancel_after fired
    MODEL_ERROR = "model_error"  # provider API error
    LOGIC_ERROR = "logic_error"  # agent ran but goal not met (result.ok False)
    DETECTION = "detection"  # silent-failure detector fired
    UNKNOWN = "unknown"
    AUTH_ERROR = "auth_error"  # permanent: invalid credentials (Req 2.1)
    CONTENT_POLICY = "content_policy"  # permanent: content policy rejection (Req 2.2)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class LoopEvent:
    loop_name: str
    run_id: str
    state: LoopState
    at: float
    data: dict = field(default_factory=dict)


class LoopRun:
    """A single scheduled agent execution record.

    Derived fields (result_text, result_steps, result_stopped, findings) are
    lazily projected from the Event_Log via conversation_id, or eagerly cached
    when the backing Store is InMemoryStore.
    """

    __slots__ = (
        "run_id",
        "loop_name",
        "state",
        "iteration",
        "started_at",
        "ended_at",
        "conversation_id",
        "failure_kind",
        "retry_after",
        "error",
        "context",
        "error_state",
        "_store",
        "_cached_projection",
    )

    def __init__(
        self,
        run_id: str,
        loop_name: str,
        state: LoopState,
        iteration: int,
        started_at: float,
        ended_at: float | None = None,
        conversation_id: str | None = None,
        failure_kind: "FailureKind | None" = None,
        retry_after: float | None = None,
        error: str | None = None,
        context: dict | None = None,
        error_state: str | None = None,
        # Legacy kwargs — accepted for backward compat, seed the cache
        result_text: str | None = None,
        result_steps: int | None = None,
        result_stopped: str | None = None,
        findings: list | None = None,
        # Private — not part of serialization
        _store: "Any | None" = None,
    ) -> None:
        self.run_id = run_id
        self.loop_name = loop_name
        self.state = state
        self.iteration = iteration
        self.started_at = started_at
        self.ended_at = ended_at
        self.conversation_id = conversation_id
        self.failure_kind = failure_kind
        self.retry_after = retry_after
        self.error = error
        self.context = context if context is not None else {}
        self.error_state = error_state
        self._store = _store
        # Seed cache from legacy kwargs if any were provided
        if (
            result_text is not None
            or result_steps is not None
            or result_stopped is not None
            or findings is not None
        ):
            self._cached_projection = {
                "text": result_text,
                "steps": result_steps,
                "stopped": result_stopped,
                "findings": findings if findings is not None else [],
            }
        else:
            self._cached_projection = None

    # ------------------------------------------------------------------
    # Derived properties — lazily projected from Event_Log (Req 3.2–3.5)
    # ------------------------------------------------------------------

    @property
    def result_text(self) -> str | None:
        return self._project().get("text")

    @result_text.setter
    def result_text(self, value: str | None) -> None:
        self._ensure_cache()
        self._cached_projection["text"] = value  # type: ignore[index]

    @property
    def result_steps(self) -> int | None:
        return self._project().get("steps")

    @result_steps.setter
    def result_steps(self, value: int | None) -> None:
        self._ensure_cache()
        self._cached_projection["steps"] = value  # type: ignore[index]

    @property
    def result_stopped(self) -> str | None:
        return self._project().get("stopped")

    @result_stopped.setter
    def result_stopped(self, value: str | None) -> None:
        self._ensure_cache()
        self._cached_projection["stopped"] = value  # type: ignore[index]

    @property
    def findings(self) -> list:
        return self._project().get("findings", [])

    @findings.setter
    def findings(self, value: list) -> None:
        self._ensure_cache()
        self._cached_projection["findings"] = value  # type: ignore[index]

    # ------------------------------------------------------------------
    # Preserved properties (Req 3.6)
    # ------------------------------------------------------------------

    @property
    def ok(self) -> bool:
        return self.state == LoopState.PASS

    @property
    def duration(self) -> float | None:
        if self.ended_at is None:
            return None
        return self.ended_at - self.started_at

    # ------------------------------------------------------------------
    # Internal: projection logic
    # ------------------------------------------------------------------

    def _ensure_cache(self) -> None:
        """Ensure _cached_projection dict exists for setter writes."""
        if self._cached_projection is None:
            self._cached_projection = {"text": None, "steps": None, "stopped": None, "findings": []}

    def _project(self) -> dict:
        """Lazily project fields from Event_Log, caching after first access (Req 10.2)."""
        if self._cached_projection is not None:
            return self._cached_projection
        if self.conversation_id is None or self._store is None:
            return {}
        log = self._store.get(f"event_log:{self.conversation_id}")
        if not log:
            return {}
        from ..conversation.projection import project_fields_from_log

        projection = project_fields_from_log(log)
        self._cached_projection = projection
        return projection

    def eagerly_project(self) -> None:
        """Force projection now and cache. Called for InMemoryStore backends (Req 3.9)."""
        if self._cached_projection is not None:
            return  # already cached (e.g. via setters)
        if self.conversation_id is None or self._store is None:
            return
        log = self._store.get(f"event_log:{self.conversation_id}")
        if not log:
            return
        from ..conversation.projection import project_fields_from_log

        self._cached_projection = project_fields_from_log(log)

    # ------------------------------------------------------------------
    # Repr for debugging
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"LoopRun(run_id={self.run_id!r}, loop_name={self.loop_name!r}, "
            f"state={self.state!r}, iteration={self.iteration})"
        )


@dataclass
class LoopGeneration:
    """A single archived generation — run result, fitness score, and instructions snapshot."""

    gen_id: int
    run_id: str
    loop_name: str
    state: LoopState
    score: float  # 1.0 = PASS, 0.0 = FAIL
    started_at: float
    instructions_snapshot: str

    @property
    def passed(self) -> bool:
        return self.state == LoopState.PASS


@dataclass
class LoopConfig:
    name: str
    goal: str
    schedule: str = "@manual"  # cron expr | @manual | @daily | @hourly | @weekly
    max_iterations: int = 3  # retries before HANDOFF
    cancel_after: float | None = None  # per-run timeout (seconds)
    retry_backoff_base: float = 30.0  # seconds: 30 → 60 → 120 before HANDOFF
    circuit_breaker_limit: int = 5  # consecutive HANDOFF cycles → SUSPENDED
    handoff: "HandoffPolicy | None" = None
    meta_model: "Model | None" = None  # if set, rewrites instructions after each FAIL
    optimizer: "Any | None" = (
        None  # Callable[[str, list[LoopRun]], str] — takes precedence over meta_model
    )
    budget: "Any | None" = None  # BudgetPolicy — cumulative cost cap across all runs
    trigger_on: str | None = None  # None=manual/cron, "event:topic_name"=EventBus trigger
    then: str | None = None  # chain target: trigger this loop on PASS
    allow_concurrent: bool = False  # ponytail: immutable after __post_init__
    adaptive_scheduling: bool = False  # Phase 3 — immutable after __post_init__
    metadata: dict = field(default_factory=dict)
    quality_trend_window: int = 10
    quality_critical_threshold: float = 50.0
    quality_degrading_threshold: float = 70.0
    error_classifier: "ErrorClassifier | None" = None  # NEW: pluggable error classification
    fallback_model: "Model | None" = None  # NEW: content policy fallback (Req 2.5)
    fallback_dir: str | None = None  # NEW: override handoff fallback directory (Req 4.2)
    fallback_retention_days: int = 7  # NEW: fallback file cleanup (Req 4.6)
    escalation_policy: "EscalationPolicy | None" = None  # Swarm: escalate instead of HANDOFF

    # ponytail: fields sealed after construction to prevent runtime mutation
    _SEALED_FIELDS: tuple = field(
        default=("allow_concurrent", "adaptive_scheduling"),
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("LoopConfig.name must not be empty")
        if not self.goal or not self.goal.strip():
            raise ValueError("LoopConfig.goal must not be empty")
        if self.max_iterations < 1:
            raise ValueError(f"max_iterations must be >= 1, got {self.max_iterations}")
        if self.retry_backoff_base < 0:
            raise ValueError(f"retry_backoff_base must be >= 0, got {self.retry_backoff_base}")
        if self.cancel_after is not None and self.cancel_after <= 0:
            raise ValueError(f"cancel_after must be > 0 when set, got {self.cancel_after}")
        if self.circuit_breaker_limit < 1:
            raise ValueError(
                f"circuit_breaker_limit must be >= 1, got {self.circuit_breaker_limit}"
            )
        # Validate trigger_on format
        if self.trigger_on is not None:
            if not self.trigger_on.startswith("event:"):
                raise ValueError(
                    f"LoopConfig.trigger_on must start with 'event:' (got {self.trigger_on!r})"
                )
            topic = self.trigger_on[len("event:") :]
            if not topic.strip():
                raise ValueError("LoopConfig.trigger_on topic must not be empty")
        # Validate schedule at construction time, not at 2am
        if self.schedule != "@manual":
            from datetime import datetime, timezone

            from .schedule import next_run_time

            try:
                next_run_time(self.schedule, datetime.now(tz=timezone.utc))
            except ValueError as exc:
                raise ValueError(f"LoopConfig.schedule invalid: {exc}") from exc
        # Seal immutable fields — mark construction complete
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, name: str, value: object) -> None:
        # ponytail: prevent mutation of sealed fields after construction
        if getattr(self, "_sealed", False) and name in self._SEALED_FIELDS:
            raise AttributeError(f"LoopConfig.{name} is immutable after construction")
        object.__setattr__(self, name, value)


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------

_CIRCUIT_BREAKER_KEY = "loop:{name}:consecutive_failures"
_STATE_KEY = "loop:{name}:last_run"


class Loop:
    """
    Run an agent on a schedule with automatic verify + handoff.

    Werner-hardened:
    - Every state transition is checkpointed to FileStore (default persistence).
    - Crash recovery: RUNNING runs on startup are detected and marked INTERRUPTED.
    - Handoff is persisted before firing; failure is tracked, not swallowed.
    - Circuit breaker: too many consecutive failures → SUSPENDED.
    - Exponential backoff between retries.
    - Scheduler task watchdog: restarts on unexpected death.
    - Memory-safe: history capped at 200 entries, stores metadata not full messages.
    - EventBus trigger support via LoopConfig.trigger_on for event-driven loops.
    """

    def __init__(
        self,
        spec: "AgentSpec",
        config: LoopConfig,
        store: "Store | None" = None,
        tracer: "Tracer | None" = None,
    ) -> None:
        from ..harness import Harness
        from ..memory.store import FileStore

        self._base_spec = spec  # original spec — kept for instruction rebuilds
        self._tracer = tracer
        self._store = store or FileStore(f".tvastar-loops/{config.name}")
        self._harness = Harness(spec, store=self._store, tracer=tracer)
        self._config = config
        self._state = LoopState.IDLE
        self._history: list[LoopRun] = []
        self._max_history = 200  # cap to prevent unbounded growth
        self._task: asyncio.Task | None = None
        self._listeners: list[Callable[[LoopEvent], None]] = []
        self._iteration = 0
        self._consecutive_failures = 0
        self._gen_counter = 0
        self._current_instructions: str = spec.instructions
        self._lock = asyncio.Lock()
        self._bg_tasks: set = set()  # keeps fire-and-forget tasks alive until done
        self._cumulative_usd: float = 0.0  # accumulated cost across all runs
        self._event_bus: Any = None  # set via subscribe_trigger for publishing run_completed
        self._quality_window: list[float] = []  # sliding window for quality trend (Req 2)

        # Crash recovery: detect orphaned RUNNING runs from a previous process
        self._recover()
        # Restore any meta-improved instructions from a previous process
        self._load_meta_instructions()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def state(self) -> LoopState:
        return self._state

    @property
    def config(self) -> LoopConfig:
        return self._config

    def history(self, limit: int = 50) -> list[LoopRun]:
        return self._history[-limit:]

    def last_run(self) -> LoopRun | None:
        return self._history[-1] if self._history else None

    @property
    def generation_archive(self) -> list[LoopGeneration]:
        """All recorded generations, oldest first. Persisted across restarts."""
        try:
            import json

            raw = self._store.get(f"loop:{self.name}:archive")
            if not raw:
                return []
            return [
                LoopGeneration(
                    gen_id=g["gen_id"],
                    run_id=g["run_id"],
                    loop_name=self.name,
                    state=LoopState(g["state"]),
                    score=g["score"],
                    started_at=g["started_at"],
                    instructions_snapshot=g["instructions_snapshot"],
                )
                for g in json.loads(raw)
            ]
        except Exception:
            return []

    def best_generation(self) -> LoopGeneration | None:
        """Return the highest-scoring generation on record (most recent PASS preferred)."""
        archive = self.generation_archive
        if not archive:
            return None
        return max(archive, key=lambda g: (g.score, g.started_at))

    def on_event(self, fn: Callable[[LoopEvent], None]) -> None:
        """Register a listener called on every state transition."""
        self._listeners.append(fn)

    def subscribe_trigger(self, event_bus: Any) -> None:
        """Subscribe this loop to be triggered by EventBus events.

        When LoopConfig.trigger_on starts with "event:", subscribes to that
        topic on the given EventBus. Each event fires a trigger().
        Also stores the EventBus reference for publishing run_completed events.
        """
        self._event_bus = event_bus
        if not self._config.trigger_on or not self._config.trigger_on.startswith("event:"):
            return
        topic = self._config.trigger_on[len("event:") :]

        def _on_event(event: Any) -> None:
            import asyncio

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.trigger(context={"event": event.payload}))
            except RuntimeError:
                pass  # no running loop — skip

        event_bus.subscribe(topic, _on_event, agent=self.name)

    async def trigger(self, context: dict | None = None) -> LoopRun:
        """Run one iteration now (regardless of schedule)."""
        async with self._lock:
            if self._state == LoopState.SUSPENDED:
                raise RuntimeError(
                    f"Loop {self._config.name!r} is SUSPENDED after "
                    f"{self._consecutive_failures} consecutive failures. "
                    "Call loop.reset() to resume."
                )
            if self._state in (LoopState.RUNNING, LoopState.VERIFYING, LoopState.TRIGGERED):
                raise RuntimeError(
                    f"Loop {self._config.name!r} is already {self._state.value}. "
                    "Wait for the current run to complete."
                )

            # Respect retry backoff
            last = self.last_run()
            if (
                last
                and last.retry_after is not None
                and time.time() < last.retry_after
                and self._state == LoopState.RETRY
            ):
                wait = last.retry_after - time.time()
                raise RuntimeError(
                    f"Loop {self._config.name!r} is backing off. Retry in {wait:.0f}s."
                )

            context = context or {}
            run_id = f"run_{uuid.uuid4().hex[:8]}"
            self._iteration += 1

            run = LoopRun(
                run_id=run_id,
                loop_name=self._config.name,
                state=LoopState.TRIGGERED,
                iteration=self._iteration,
                started_at=time.time(),
                context=context,
            )
            self._history.append(run)
            # Enforce cap to prevent unbounded memory growth
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history :]
            self._checkpoint(run)
            self._emit(run, LoopState.TRIGGERED)

        try:
            await self._run_iteration(run, context)
        except Exception as exc:
            run.error = str(exc)
            run.failure_kind = FailureKind.UNKNOWN
            async with self._lock:
                self._set(run, LoopState.FAIL)
                await self._handle_fail(run)
            self._publish_run_completed(run, quality_score=None)
        finally:
            run.ended_at = time.time()
            self._checkpoint(run)
            self._record_generation(run)

        # Self-improvement: after any non-PASS run, fire meta-improvement asynchronously
        # so the next retry (already scheduled after backoff) benefits from improved instructions
        if run.state not in (LoopState.PASS, LoopState.SUSPENDED) and (
            self._config.optimizer is not None or self._config.meta_model is not None
        ):
            t = asyncio.create_task(self._improve_instructions(run))
            self._bg_tasks.add(t)  # prevent GC before completion
            t.add_done_callback(self._bg_tasks.discard)

        return run

    async def start(self) -> None:
        """Start the background scheduler. Non-blocking."""
        if self._config.schedule == "@manual":
            return
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._scheduler_loop(), name=f"loop:{self.name}")
        self._task.add_done_callback(self._on_scheduler_done)

    async def stop(self) -> None:
        """Stop the background scheduler and cancel all pending tasks gracefully.

        Cancels the scheduler task, any pending retry/handoff background tasks,
        and any in-flight instruction improvement tasks tracked in _bg_tasks.
        """
        if self._task:
            self._task.remove_done_callback(self._on_scheduler_done)
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        # Cancel background improvement tasks
        for t in list(self._bg_tasks):
            if not t.done():
                t.cancel()
        self._bg_tasks.clear()

    def reset(self) -> None:
        """Clear SUSPENDED state and reset circuit breaker. Manual intervention."""
        self._consecutive_failures = 0
        self._iteration = 0
        self._state = LoopState.IDLE
        self._store.set(_CIRCUIT_BREAKER_KEY.format(name=self.name), "0")

    # ------------------------------------------------------------------
    # Internal: run lifecycle
    # ------------------------------------------------------------------

    def _classify(self, exc: Exception) -> "ClassificationResult | None":
        """Invoke configured error_classifier. Never fatal — returns None on failure."""
        if self._config.error_classifier is None:
            return None
        try:
            return self._config.error_classifier(exc)
        except Exception:
            return None  # ponytail: classifier failure is never fatal

    async def _run_iteration(self, run: LoopRun, context: dict) -> None:
        from contextlib import nullcontext

        _tracer_ctx = (
            self._tracer.span("loop.run", loop=self.name)
            if self._tracer is not None
            else nullcontext()
        )
        with _tracer_ctx:
            await self._run_iteration_inner(run, context)

    async def _run_iteration_inner(self, run: LoopRun, context: dict) -> None:
        async with self._lock:
            self._set(run, LoopState.RUNNING)

        prompt = self._build_prompt(context)

        # Generate a stable session_id so we can reference the event log later.
        session_id = f"sess_{uuid.uuid4().hex[:12]}"

        try:
            if self._config.cancel_after:
                result = await asyncio.wait_for(
                    self._harness.run(prompt, session_id=session_id),
                    timeout=self._config.cancel_after,
                )
            else:
                result = await self._harness.run(prompt, session_id=session_id)
        except asyncio.TimeoutError:
            run.error = f"Run timed out after {self._config.cancel_after}s"
            run.failure_kind = FailureKind.TIMEOUT
            async with self._lock:
                self._set(run, LoopState.FAIL)
                await self._handle_fail(run)
            self._publish_run_completed(run, quality_score=None)
            return
        except Exception as exc:
            run.error = str(exc)
            classification = self._classify(exc)
            run.failure_kind = (
                classification.failure_kind if classification else FailureKind.MODEL_ERROR
            )
            # Propagate Retry-After to LoopRun for _handle_fail to use
            if classification and classification.retry_after_seconds:
                run.retry_after = time.time() + classification.retry_after_seconds
            async with self._lock:
                self._set(run, LoopState.FAIL)
                await self._handle_fail(run)
            self._publish_run_completed(run, quality_score=None)
            return

        # Reference the session's event log — not full message history
        run.conversation_id = result.conversation_id
        run._store = self._store
        # InMemoryStore is non-durable; eagerly cache projection (Req 3.9)
        from ..memory.store import InMemoryStore

        if isinstance(self._store, InMemoryStore):
            run.eagerly_project()

        # Update agent_sessions index (Req 12.2)
        self._append_agent_session(result.conversation_id)

        # cumulative budget: suspend the loop when total spend across runs exceeds cap
        if self._config.budget is not None and hasattr(result, "cost"):
            self._cumulative_usd += result.cost.usd
            if self._cumulative_usd >= self._config.budget.max_usd:
                async with self._lock:
                    self._set(run, LoopState.SUSPENDED)
                self._publish_run_completed(run, quality_score=result.quality.score)
                return

        async with self._lock:
            self._set(run, LoopState.VERIFYING)

            has_warnings = bool(result.warnings)
            has_findings = any(f.severity in ("ERROR", "WARNING") for f in result.findings)
            failed = (not result.ok) or has_warnings or has_findings

            if not failed:
                self._set(run, LoopState.PASS)
                run.error_state = "resolved"
                self._iteration = 0
                self._consecutive_failures = 0
                self._store.set(_CIRCUIT_BREAKER_KEY.format(name=self.name), "0")
                self._publish_run_completed(run, quality_score=result.quality.score)
                return

            run.failure_kind = FailureKind.DETECTION if has_findings else FailureKind.LOGIC_ERROR
            self._set(run, LoopState.FAIL)
            await self._handle_fail(run)
            self._publish_run_completed(run, quality_score=result.quality.score)

    async def _handle_fail(self, run: LoopRun) -> None:
        """Called under self._lock after a FAIL state is set."""
        run.error_state = "classified"
        # Permanent failures skip retry entirely (Req 2.3, 2.4, 2.5, 2.6)
        if run.failure_kind in (FailureKind.AUTH_ERROR, FailureKind.CONTENT_POLICY):
            run.error_state = "permanent"
            # CONTENT_POLICY with fallback_model: attempt one run with fallback
            if (
                run.failure_kind == FailureKind.CONTENT_POLICY
                and self._config.fallback_model is not None
                and not run.context.get("_fallback_attempted")
            ):
                run.context["_fallback_attempted"] = True
                # Schedule a single retry with the fallback model
                t = asyncio.create_task(self._run_with_fallback_model(run))
                self._bg_tasks.add(t)
                t.add_done_callback(self._bg_tasks.discard)
                return

            self._iteration = 0
            self._consecutive_failures += 1
            self._store.set(
                _CIRCUIT_BREAKER_KEY.format(name=self.name),
                str(self._consecutive_failures),
            )
            self._set(run, LoopState.HANDOFF)
            t = asyncio.create_task(self._fire_handoff(run))
            self._bg_tasks.add(t)
            t.add_done_callback(self._bg_tasks.discard)
            return

        if self._iteration < self._config.max_iterations:
            run.error_state = "retrying"
            # Prefer Retry-After from classification over exponential backoff
            if run.retry_after is not None and run.retry_after > time.time():
                backoff = run.retry_after - time.time()
            else:
                backoff = self._config.retry_backoff_base * (2 ** (self._iteration - 1))
            # Cap at cancel_after if set
            if self._config.cancel_after and backoff > self._config.cancel_after:
                import sys

                print(
                    f"[reliability] Retry-After {backoff:.0f}s exceeds cancel_after "
                    f"{self._config.cancel_after:.0f}s, capping",
                    file=sys.stderr,
                )
                backoff = self._config.cancel_after
            run.retry_after = time.time() + backoff
            self._set(run, LoopState.RETRY)
            # Schedule the retry after backoff — tracked for clean shutdown
            t = asyncio.create_task(self._delayed_retry(run, backoff))
            self._bg_tasks.add(t)
            t.add_done_callback(self._bg_tasks.discard)
        else:
            # Retries exhausted — escalate or handoff
            run.error_state = "escalated"
            if self._config.escalation_policy is not None:
                # Escalation pathway: write to SignalBus and wait for directive
                t = asyncio.create_task(self._escalate(run))
                self._bg_tasks.add(t)
                t.add_done_callback(self._bg_tasks.discard)
            else:
                self._iteration = 0
                self._consecutive_failures += 1
                self._store.set(
                    _CIRCUIT_BREAKER_KEY.format(name=self.name),
                    str(self._consecutive_failures),
                )
                self._set(run, LoopState.HANDOFF)
                # Track handoff task for clean shutdown
                t = asyncio.create_task(self._fire_handoff(run))
                self._bg_tasks.add(t)
                t.add_done_callback(self._bg_tasks.discard)

    async def _escalate(self, run: LoopRun) -> None:
        """Escalation pathway: write escalation to SignalBus, wait for directive.

        Called (outside the lock) when retries exhausted and escalation_policy is set.
        On directive received: apply it, reset iteration, transition to RUNNING.
        On timeout: transition to DEGRADED.
        """
        from ..fleet.models import Escalation

        policy = self._config.escalation_policy
        assert policy is not None  # guaranteed by caller
        ns = policy.namespace or self.name
        signal_bus = policy.signal_bus

        # Write escalation to SignalBus
        escalation = Escalation(
            reason="retries_exhausted",
            error_type=run.failure_kind.value if run.failure_kind else "unknown",
            attempts=self._config.max_iterations,
            last_error=run.error,
        )
        async with self._lock:
            self._set(run, LoopState.ESCALATING)
        await signal_bus.write(ns, "escalation", escalation)
        await signal_bus.write(ns, "status", LoopState.ESCALATING.value)

        # Watch for directive with timeout
        try:
            directive = await asyncio.wait_for(
                self._wait_for_directive(signal_bus, ns),
                timeout=policy.escalation_timeout,
            )
            # Directive received — apply it (reset iteration, transition to RUNNING)
            import logging as _logging

            _logging.getLogger(__name__).info(
                "Loop %r received directive: %s", self.name, directive
            )
            async with self._lock:
                self._iteration = 0
                self._set(run, LoopState.RUNNING)
            await signal_bus.write(ns, "status", LoopState.RUNNING.value)
        except asyncio.TimeoutError:
            # No directive — degrade
            async with self._lock:
                self._set(run, LoopState.DEGRADED)
            await signal_bus.write(ns, "status", LoopState.DEGRADED.value)

    async def _wait_for_directive(self, signal_bus: Any, namespace: str) -> Any:
        """Watch SignalBus for a directive entry in our namespace."""
        async for entry in signal_bus.watch(namespace):
            if entry.key == "directive":
                return entry.value
        return None  # pragma: no cover — watch is infinite

    async def _run_with_fallback_model(self, run: LoopRun) -> None:
        """Attempt one run with the configured fallback model (Req 2.5, 2.6).

        If the fallback also fails with CONTENT_POLICY, transition to HANDOFF.
        """
        import dataclasses
        from ..harness import Harness

        fallback_spec = dataclasses.replace(self._base_spec, model=self._config.fallback_model)
        fallback_harness = Harness(fallback_spec, store=self._store, tracer=self._tracer)

        prompt = self._build_prompt(run.context)
        session_id = f"sess_{uuid.uuid4().hex[:12]}"

        try:
            if self._config.cancel_after:
                result = await asyncio.wait_for(
                    fallback_harness.run(prompt, session_id=session_id),
                    timeout=self._config.cancel_after,
                )
            else:
                result = await fallback_harness.run(prompt, session_id=session_id)
        except Exception as exc:
            classification = self._classify(exc)
            failure_kind = (
                classification.failure_kind if classification else FailureKind.MODEL_ERROR
            )
            # Any failure on fallback → HANDOFF (no further retry)
            async with self._lock:
                run.error = str(exc)
                run.failure_kind = failure_kind
                self._iteration = 0
                self._consecutive_failures += 1
                self._store.set(
                    _CIRCUIT_BREAKER_KEY.format(name=self.name),
                    str(self._consecutive_failures),
                )
                self._set(run, LoopState.HANDOFF)
                t = asyncio.create_task(self._fire_handoff(run))
                self._bg_tasks.add(t)
                t.add_done_callback(self._bg_tasks.discard)
            return

        # Fallback succeeded — check result quality
        has_findings = any(f.severity in ("ERROR", "WARNING") for f in result.findings)
        failed = (not result.ok) or bool(result.warnings) or has_findings

        if not failed:
            # Fallback produced a good result
            async with self._lock:
                run.conversation_id = result.conversation_id
                run._store = self._store
                self._set(run, LoopState.PASS)
                self._iteration = 0
                self._consecutive_failures = 0
                self._store.set(_CIRCUIT_BREAKER_KEY.format(name=self.name), "0")
            self._publish_run_completed(run, quality_score=result.quality.score)
        else:
            # Fallback also failed — HANDOFF
            async with self._lock:
                run.failure_kind = FailureKind.CONTENT_POLICY
                self._iteration = 0
                self._consecutive_failures += 1
                self._store.set(
                    _CIRCUIT_BREAKER_KEY.format(name=self.name),
                    str(self._consecutive_failures),
                )
                self._set(run, LoopState.HANDOFF)
                t = asyncio.create_task(self._fire_handoff(run))
                self._bg_tasks.add(t)
                t.add_done_callback(self._bg_tasks.discard)

    async def _delayed_retry(self, run: LoopRun, delay: float) -> None:
        """Wait backoff, then fire the next iteration if still in RETRY state."""
        await asyncio.sleep(delay)
        try:
            # Guard: only trigger if still in RETRY state (prevents double-trigger
            # when scheduler fires at the same time as delayed retry — Bug #7)
            if self._state != LoopState.RETRY:
                return
            await self.trigger(context=run.context)
        except Exception:
            pass

    def _fallback_dir(self) -> "pathlib.Path":
        """Return the directory for handoff fallback files."""
        from pathlib import Path

        if self._config.fallback_dir:
            return Path(self._config.fallback_dir)
        # Default: alongside the store data
        store_root = getattr(self._store, "root", None)
        if store_root:
            return Path(store_root) / "handoff_fallback"
        return Path("handoff_fallback")

    def _write_handoff_fallback(self, run: LoopRun, delivery_errors: list[str]) -> None:
        """Synchronous write of handoff fallback JSON. Best-effort."""
        import json
        from datetime import datetime, timezone

        fallback_dir = self._fallback_dir()
        try:
            fallback_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "loop_name": run.loop_name,
                "run_id": run.run_id,
                "failure_kind": run.failure_kind.value if run.failure_kind else None,
                "error": run.error,
                "delivery_errors": delivery_errors,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            path = fallback_dir / f"{run.run_id}.json"
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as e:
            import sys

            print(f"[reliability] fallback file write failed: {e}", file=sys.stderr)
            run.context["fallback_write_error"] = str(e)

    def _cleanup_old_fallback_files(self) -> None:
        """Delete fallback files older than retention period. Best-effort."""
        import sys
        import time as _time

        fallback_dir = self._fallback_dir()
        if not fallback_dir.exists():
            return
        retention_seconds = self._config.fallback_retention_days * 86400
        cutoff = _time.time() - retention_seconds
        try:
            for f in fallback_dir.glob("*.json"):
                if f.stat().st_mtime < cutoff:
                    f.unlink()
        except Exception as e:
            print(f"[reliability] fallback file cleanup failed: {e}", file=sys.stderr)

    async def _fire_handoff(self, run: LoopRun) -> None:
        """Persist handoff intent, then call policy. Never silently drop."""
        # Persist before firing — if we crash here, it's still recorded
        self._store.set(
            f"loop:{self.name}:pending_handoff",
            run.run_id,
        )
        if self._config.handoff is None:
            from .handoff import LogHandoff

            handoff = LogHandoff()
        else:
            handoff = self._config.handoff

        delivery_errors: list[str] = []
        for attempt in range(3):
            try:
                await handoff.escalate(run, self._history)
                self._store.delete(f"loop:{self.name}:pending_handoff")
                break
            except Exception as exc:
                delivery_errors.append(f"Attempt {attempt + 1}: {exc}")
                if attempt == 2:
                    self._write_handoff_fallback(run, delivery_errors)
                    async with self._lock:
                        self._set(run, LoopState.HANDOFF_FAILED, {"error": str(exc)})
                    self._checkpoint(run)
                    return
                await asyncio.sleep(10 * (attempt + 1))

        # Check circuit breaker
        if self._consecutive_failures >= self._config.circuit_breaker_limit:
            async with self._lock:
                self._set(run, LoopState.SUSPENDED)
            self._checkpoint(run)
        else:
            async with self._lock:
                self._state = LoopState.IDLE

    # ------------------------------------------------------------------
    # Internal: scheduler
    # ------------------------------------------------------------------

    async def _scheduler_loop(self) -> None:
        from datetime import datetime, timezone

        from .schedule import next_run_time

        while True:
            try:
                if self._state == LoopState.SUSPENDED:
                    await asyncio.sleep(60)
                    continue
                now = datetime.now(tz=timezone.utc)
                nxt = next_run_time(self._config.schedule, now)
                delay = (nxt - now).total_seconds()
                if delay > 0:
                    await asyncio.sleep(delay)
                if self._state not in (LoopState.RUNNING, LoopState.VERIFYING, LoopState.TRIGGERED):
                    await self.trigger()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Don't crash the scheduler — wait 60s and retry
                await asyncio.sleep(60)

    def _on_scheduler_done(self, task: asyncio.Task) -> None:
        """Watchdog: restart scheduler if it exits unexpectedly."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None and self._state != LoopState.SUSPENDED:
            # Unexpected crash — restart
            self._task = asyncio.create_task(self._scheduler_loop(), name=f"loop:{self.name}")
            self._task.add_done_callback(self._on_scheduler_done)

    # ------------------------------------------------------------------
    # Internal: crash recovery
    # ------------------------------------------------------------------

    def _recover(self) -> None:
        """On startup, recover last run from checkpoint.

        Handles:
        - Orphaned RUNNING/VERIFYING/TRIGGERED → mark INTERRUPTED
        - v2 format (has conversation_id) → lazy derivation from Store
        - v1 legacy format (has result fields, no conversation_id) → seed cache directly (REQ-15)
        """
        # Best-effort cleanup of old fallback files (Req 4.6, 4.7)
        self._cleanup_old_fallback_files()

        try:
            raw = self._store.get(_STATE_KEY.format(name=self.name))
            if raw is None:
                return
            import json

            data = json.loads(raw)
            if data.get("state") in (LoopState.RUNNING, LoopState.VERIFYING, LoopState.TRIGGERED):
                # Orphaned run — was in-flight when process died
                conversation_id = data.get("conversation_id")
                run = LoopRun(
                    run_id=data.get("run_id", "unknown"),
                    loop_name=self.name,
                    state=LoopState.INTERRUPTED,
                    iteration=data.get("iteration", 1),
                    started_at=data.get("started_at", time.time()),
                    ended_at=time.time(),
                    conversation_id=conversation_id,
                    error="Process interrupted mid-run (crash recovery)",
                    failure_kind=FailureKind.UNKNOWN,
                    _store=self._store,
                )
                # If conversation_id present but Event_Log unavailable, mark failed (Req 4.3)
                if conversation_id and not self._store.get(f"event_log:{conversation_id}"):
                    run.state = LoopState.FAIL
                    run.error = f"event_log_unavailable: {conversation_id}"
                self._history.append(run)
                self._emit(run, run.state)
            elif not data.get("conversation_id") and any(
                k in data for k in ("result_text", "result_steps", "result_stopped", "findings")
            ):
                # v1 legacy checkpoint — no conversation_id, has direct fields (REQ-15 AC1)
                run = LoopRun(
                    run_id=data.get("run_id", "unknown"),
                    loop_name=self.name,
                    state=data.get("state", LoopState.PASS),
                    iteration=data.get("iteration", 1),
                    started_at=data.get("started_at", time.time()),
                    ended_at=data.get("ended_at"),
                    failure_kind=data.get("failure_kind"),
                    error=data.get("error"),
                    context=data.get("context", {}),
                    result_text=data.get("result_text"),
                    result_steps=data.get("result_steps"),
                    result_stopped=data.get("result_stopped"),
                    findings=data.get("findings", []),
                )
                self._history.append(run)
        except Exception:
            pass  # don't crash on recovery failure

    # ------------------------------------------------------------------
    # Internal: helpers
    # ------------------------------------------------------------------

    def _load_meta_instructions(self) -> None:
        """On startup, restore any meta-improved instructions from a previous run."""
        try:
            raw = self._store.get(f"loop:{self.name}:meta_instructions")
            if not raw:
                return
            from ..harness import Harness
            import dataclasses

            self._current_instructions = raw
            new_spec = dataclasses.replace(self._base_spec, instructions=raw)
            self._harness = Harness(new_spec, store=self._store, tracer=self._tracer)
        except Exception:
            pass  # restoration failure must not crash startup

    def _record_generation(self, run: LoopRun) -> None:
        """Append this run to the generational archive. Keeps last 100 entries."""
        try:
            import json

            self._gen_counter += 1
            entry = {
                "gen_id": self._gen_counter,
                "run_id": run.run_id,
                "state": run.state,
                "score": 1.0 if run.state == LoopState.PASS else 0.0,
                "started_at": run.started_at,
                "instructions_snapshot": self._current_instructions,
            }
            key = f"loop:{self.name}:archive"
            raw = self._store.get(key)
            archive = json.loads(raw) if raw else []
            archive.append(entry)
            if len(archive) > 100:
                archive = archive[-100:]
            self._store.set(key, json.dumps(archive))
        except Exception:
            pass  # archive failure must not crash the run

    async def _improve_instructions(self, run: LoopRun) -> None:
        """Rewrite loop instructions after a FAIL.

        Tries optimizer first (DSPyOptimizer or any callable), falls back to
        meta_model one-shot rewrite. Fires asynchronously — never raises.
        """
        failure_lines: list[str] = []
        if run.error:
            failure_lines.append(f"Error: {run.error}")
        for f in run.findings[:5]:
            detector = getattr(f, "detector", "?")
            message = getattr(f, "message", str(f))
            failure_lines.append(f"[{detector}] {message}")

        if not failure_lines:
            return  # no evidence to improve from

        try:
            import dataclasses

            new_instructions: str | None = None

            # ── optimizer path (DSPyOptimizer or any callable) ──────────────
            if self._config.optimizer is not None:
                import json as _json

                _runs_key = f"loop:{self.name}:runs"
                _raw_runs = self._store.get(_runs_key)
                _stored_runs: list[LoopRun] = _json.loads(_raw_runs) if _raw_runs else []
                all_runs: list[LoopRun] = _stored_runs + [run]
                # Persist updated run list (capped at 50)
                _serialisable = [
                    {
                        "run_id": r.run_id if hasattr(r, "run_id") else r.get("run_id", ""),
                        "state": r.state if hasattr(r, "state") else r.get("state", ""),
                        "started_at": r.started_at
                        if hasattr(r, "started_at")
                        else r.get("started_at", 0),
                        "result_text": r.result_text
                        if hasattr(r, "result_text")
                        else r.get("result_text"),
                        "failure_kind": (
                            r.failure_kind.value
                            if hasattr(r, "failure_kind") and r.failure_kind is not None
                            else (r.get("failure_kind") if isinstance(r, dict) else None)
                        ),
                        "error": r.error if hasattr(r, "error") else r.get("error"),
                    }
                    for r in all_runs[-50:]
                ]
                self._store.set(_runs_key, _json.dumps(_serialisable))
                new_instructions = self._config.optimizer(self._current_instructions, all_runs)

            # ── meta_model path (legacy one-shot rewrite) ───────────────────
            elif self._config.meta_model is not None:
                from ..agent import create_agent
                from ..harness import Harness

                meta_spec = create_agent(
                    f"{self.name}:meta",
                    model=self._config.meta_model,
                    instructions=(
                        "You are an expert prompt engineer specialising in AI agent reliability. "
                        "You will receive agent instructions and failure evidence from a production loop. "
                        "Your job: rewrite the instructions to prevent these failures. "
                        "Preserve all existing rules and safety constraints. "
                        "Add specific, actionable guidance to address each failure. "
                        "Return ONLY the improved instructions — no preamble, no commentary, no quotes."
                    ),
                )
                prompt = (
                    f"Current agent instructions:\n{self._current_instructions}\n\n"
                    f"Failure evidence from run {run.run_id}:\n"
                    + "\n".join(failure_lines)
                    + "\n\nRewrite the instructions to prevent these failures. "
                    "Return ONLY the improved instructions."
                )
                meta_timeout = min(self._config.cancel_after or 120.0, 120.0)
                result = await asyncio.wait_for(
                    Harness(meta_spec).run(prompt),
                    timeout=meta_timeout,
                )
                if result.ok and result.text.strip():
                    new_instructions = result.text.strip()

            if new_instructions and new_instructions != self._current_instructions:
                new_spec = dataclasses.replace(self._base_spec, instructions=new_instructions)
                new_harness = Harness(new_spec, store=self._store, tracer=self._tracer)
                async with self._lock:
                    self._harness = new_harness
                    self._current_instructions = new_instructions
                self._store.set(f"loop:{self.name}:meta_instructions", new_instructions)
                self._emit(run, run.state, {"meta_improved": True, "gen": self._gen_counter})
        except Exception:
            pass  # improvement failure must never crash the loop

    def _build_prompt(self, context: dict) -> str:
        parts = [f"Goal: {self._config.goal}"]
        if context:
            ctx_str = ", ".join(f"{k}={v}" for k, v in context.items())
            parts.append(f"Context: {ctx_str}")
        if self._iteration > 1:
            parts.append(f"This is attempt {self._iteration} of {self._config.max_iterations}.")
            last = next(
                (r for r in reversed(self._history[:-1]) if r.error or r.findings),
                None,
            )
            if last:
                if last.error:
                    parts.append(f"Previous attempt error: {last.error}")
                if last.findings:
                    msgs = [f.message for f in last.findings[:3]]
                    parts.append(f"Previous findings: {'; '.join(msgs)}")
        return "\n".join(parts)

    def _set(self, run: LoopRun, state: LoopState, data: dict | None = None) -> None:
        run.state = state
        self._state = state
        self._emit(run, state, data or {})
        # Write state to SignalBus for observability (Req 2.7) — non-terminal only
        if (
            self._config.escalation_policy is not None
            and state not in (LoopState.PASS, LoopState.HANDOFF)
        ):
            policy = self._config.escalation_policy
            ns = policy.namespace or self.name
            # Best-effort, fire-and-forget — never block state machine
            try:
                import asyncio as _aio

                loop = _aio.get_running_loop()
                t = loop.create_task(policy.signal_bus.write(ns, "status", state.value))
                self._bg_tasks.add(t)
                t.add_done_callback(self._bg_tasks.discard)
            except RuntimeError:
                pass  # no running loop — skip (e.g. during sync tests)

    def _emit(self, run: LoopRun, state: LoopState, data: dict | None = None) -> None:
        event = LoopEvent(
            loop_name=self.name,
            run_id=run.run_id,
            state=state,
            at=time.time(),
            data=data or {},
        )
        for fn in self._listeners:
            try:
                fn(event)
            except Exception:
                pass

    def _append_agent_session(self, conversation_id: str) -> None:
        """Append conversation_id to the agent_sessions index for this loop's agent."""
        key = f"agent_sessions:{self.name}"
        sessions = self._store.get(key) or []
        sessions.append(conversation_id)
        self._store.set(key, sessions)

    def _publish_run_completed(self, run: LoopRun, quality_score: float | None = None) -> None:
        """Publish fleet.loop.run_completed event if EventBus is configured (Req 5.1, 5.6)."""
        # Quality trend tracking (Req 2)
        if quality_score is not None:
            self._quality_window.append(quality_score)
            if len(self._quality_window) > self._config.quality_trend_window:
                self._quality_window = self._quality_window[-self._config.quality_trend_window:]
            if len(self._quality_window) >= 3 and self._event_bus is not None:
                from statistics import mean
                avg = mean(self._quality_window)
                if avg < self._config.quality_critical_threshold:
                    try:
                        self._event_bus.publish(
                            "quality.critical",
                            {"average": avg, "window": list(self._quality_window)},
                            source_agent=self.name,
                        )
                    except Exception:
                        pass
                elif avg < self._config.quality_degrading_threshold:
                    try:
                        self._event_bus.publish(
                            "quality.degrading",
                            {"average": avg, "window": list(self._quality_window)},
                            source_agent=self.name,
                        )
                    except Exception:
                        pass

        if self._event_bus is None:
            return
        try:
            self._event_bus.publish(
                "fleet.loop.run_completed",
                {
                    "conversation_id": run.conversation_id,
                    "agent_name": self.name,
                    "is_error": run.state != LoopState.PASS,
                    "quality_score": quality_score,
                },
                source_agent=self.name,
            )
        except Exception:
            pass  # best-effort — never fail the run for event publishing

    def _checkpoint(self, run: LoopRun) -> None:
        """Persist run state after every transition — survive crashes."""
        try:
            import json

            self._store.set(
                _STATE_KEY.format(name=self.name),
                json.dumps(
                    {
                        "run_id": run.run_id,
                        "state": run.state,
                        "iteration": run.iteration,
                        "started_at": run.started_at,
                        "failure_kind": run.failure_kind,
                        "error": run.error,
                        "conversation_id": run.conversation_id,
                    }
                ),
            )
        except Exception:
            pass  # checkpoint failure must not crash the run


__all__ = [
    "Loop",
    "LoopConfig",
    "LoopState",
    "LoopRun",
    "LoopEvent",
    "LoopGeneration",
    "FailureKind",
    "_CIRCUIT_BREAKER_KEY",
]
