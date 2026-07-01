"""Workflow — finite, bounded agent operations with durable run history.

A Workflow wraps an async ``run(ctx)`` function with:
  - a unique ``run_id`` per invocation
  - structured input/output
  - a queryable run registry (in-memory or file-backed)
  - per-run event log (lifecycle + agent activity)
  - CLI inspection via ``tvastar logs <run_id>``

Usage::

    from tvastar.workflow import workflow, WorkflowContext

    @workflow
    async def summarize(ctx: WorkflowContext) -> dict:
        harness = await ctx.init(agent)
        session = await harness.session_async()
        response = await session.prompt(ctx.payload["text"])
        return {"summary": response.text}

    # Run it:
    result = await summarize.run({"text": "..."})
    print(result.run_id, result.output)

    # Inspect a past run:
    record = summarize.registry.get(run_id)
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Optional, Protocol, runtime_checkable

from .agent import AgentSpec
from .harness import Harness
from .memory.store import FileStore, InMemoryStore, Store

if TYPE_CHECKING:
    from .observability import Tracer


# ── Run status ──────────────────────────────────────────────────────────────


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ── Events ──────────────────────────────────────────────────────────────────


@dataclass
class RunEvent:
    """One observable activity record within a workflow run."""

    type: str  # run_start | run_end | log | operation | error
    at: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"type": self.type, "at": self.at, "data": self.data}

    @classmethod
    def from_dict(cls, d: dict) -> "RunEvent":
        return cls(type=d["type"], at=d.get("at", 0.0), data=d.get("data", {}))


# ── WorkflowRun ──────────────────────────────────────────────────────────────


@dataclass
class WorkflowRun:
    """The record of one workflow invocation."""

    run_id: str
    workflow_name: str
    status: RunStatus
    payload: Any
    output: Optional[Any] = None
    error: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    events: list[RunEvent] = field(default_factory=list)

    def add_event(self, type: str, **data: Any) -> None:
        self.events.append(RunEvent(type=type, data=data))

    def log(self, level: str, message: str, **attrs: Any) -> None:
        self.add_event("log", level=level, message=message, **attrs)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "workflow_name": self.workflow_name,
            "status": self.status.value,
            "payload": self.payload,
            "output": self.output,
            "error": self.error,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "events": [e.to_dict() for e in self.events],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "WorkflowRun":
        obj = cls(
            run_id=d["run_id"],
            workflow_name=d["workflow_name"],
            status=RunStatus(d["status"]),
            payload=d.get("payload"),
            output=d.get("output"),
            error=d.get("error"),
            started_at=d.get("started_at", 0.0),
            ended_at=d.get("ended_at"),
        )
        obj.events = [RunEvent.from_dict(e) for e in d.get("events", [])]
        return obj


# ── RunRegistry ──────────────────────────────────────────────────────────────

_RUN_PREFIX = "run:"


class RunRegistry:
    """Persists and retrieves WorkflowRun records."""

    def __init__(self, store: Optional[Store] = None):
        self._store: Store = store or InMemoryStore()

    def save(self, run: WorkflowRun) -> None:
        self._store.set(_RUN_PREFIX + run.run_id, run.to_dict())

    def get(self, run_id: str) -> Optional[WorkflowRun]:
        record = self._store.get(_RUN_PREFIX + run_id)
        if not record:
            return None
        return WorkflowRun.from_dict(record)

    def list_runs(self, workflow_name: Optional[str] = None) -> list[WorkflowRun]:
        runs = []
        for key in self._store.keys(_RUN_PREFIX):
            record = self._store.get(key)
            if record:
                run = WorkflowRun.from_dict(record)
                if workflow_name is None or run.workflow_name == workflow_name:
                    runs.append(run)
        runs.sort(key=lambda r: r.started_at, reverse=True)
        return runs

    def events(self, run_id: str) -> list[RunEvent]:
        run = self.get(run_id)
        return run.events if run else []

    @classmethod
    def file_backed(cls, path: str = ".tvastar-runs") -> "RunRegistry":
        return cls(store=FileStore(path))


# ── WorkflowContext ──────────────────────────────────────────────────────────


@dataclass
class WorkflowContext:
    """Injected into each workflow ``run()`` invocation.

    Provides:
    - ``payload``  — the caller-supplied input dict
    - ``run_id``   — unique ID for this invocation
    - ``log``      — structured logging (info/warn/error)
    - ``init()``   — initialise an AgentSpec into a Harness bound to this run
    - ``checkpoint()`` — persist step data via the configured backend
    - ``get_checkpoint()`` — retrieve previously checkpointed data
    """

    run_id: str
    payload: Any
    _run: WorkflowRun
    _registry: RunRegistry
    _tracer: Optional["Tracer"] = None
    _harnesses: list[Harness] = field(default_factory=list)
    _checkpoint_backend: Optional[WorkflowCheckpoint] = None
    _checkpoint_cache: dict[str, Any] = field(default_factory=dict)

    # ---- logging -----------------------------------------------------------

    def _log(self, level: str, message: str, **attrs: Any) -> None:
        self._run.log(level, message, **attrs)
        self._registry.save(self._run)

    class _Logger:
        def __init__(self, ctx: "WorkflowContext"):
            self._ctx = ctx

        def info(self, msg: str, **kw: Any) -> None:
            self._ctx._log("info", msg, **kw)

        def warn(self, msg: str, **kw: Any) -> None:
            self._ctx._log("warn", msg, **kw)

        def error(self, msg: str, **kw: Any) -> None:
            self._ctx._log("error", msg, **kw)

    @property
    def log(self) -> "_Logger":
        return self._Logger(self)

    # ---- harness init -------------------------------------------------------

    async def init(
        self,
        spec: AgentSpec,
        *,
        store: Optional[Store] = None,
        durable: bool = False,
    ) -> "WorkflowHarness":
        """Initialise an agent for this workflow invocation.

        Returns a WorkflowHarness that exposes .session(), .fs, and .shell().
        """
        harness = Harness(spec, store=store, durable=durable, tracer=self._tracer)
        wh = WorkflowHarness(harness, run=self._run, registry=self._registry)
        self._harnesses.append(harness)
        return wh

    # ---- checkpointing ------------------------------------------------------

    async def checkpoint(self, step_name: str, data: Any) -> None:
        """Persist step data via the configured checkpoint backend.

        Does nothing if no checkpoint backend is configured.
        Raises on filesystem error (checkpoint failure = data loss risk).
        """
        if self._checkpoint_backend is None:
            return
        await self._checkpoint_backend.save(self.run_id, step_name, data)
        self._checkpoint_cache[step_name] = data

    async def get_checkpoint(self, step_name: str) -> Optional[Any]:
        """Retrieve previously checkpointed data for step_name, or None."""
        return self._checkpoint_cache.get(step_name)

    async def skip_if_checkpointed(self, step_name: str) -> Optional[Any]:
        """Return checkpointed data if step was previously completed, else None.

        Convenience for the common pattern::

            data = await ctx.skip_if_checkpointed("analyze")
            if data is not None:
                return data  # skip this phase
            # ... do the work ...
            await ctx.checkpoint("analyze", result_data)
        """
        return await self.get_checkpoint(step_name)

    def build_receipt(self) -> dict:
        """Build a unified execution receipt for this workflow run.

        Aggregates timing, cost, and outcome data across all sessions
        and sub-agent invocations within this workflow.
        """
        return {
            "run_id": self.run_id,
            "workflow_name": self._run.workflow_name,
            "status": self._run.status.value,
            "started_at": self._run.started_at,
            "ended_at": self._run.ended_at,
            "duration_seconds": (self._run.ended_at or 0) - self._run.started_at,
            "events_count": len(self._run.events),
            "output": self._run.output,
        }


# ── WorkflowHarness ──────────────────────────────────────────────────────────


class WorkflowHarness:
    """Thin wrapper around Harness exposing workflow-oriented APIs.

    Adds:
    - ``session_async()``  — open a named or default session
    - ``fs``               — application-level filesystem access
    - ``shell(cmd)``       — application-level shell execution
    """

    def __init__(self, harness: Harness, *, run: WorkflowRun, registry: RunRegistry):
        self._harness = harness
        self._run = run
        self._registry = registry
        self._session_cache: dict[str, Any] = {}

    async def session_async(self, name: str = "default") -> Any:
        """Open (or reuse) a named session."""
        if name not in self._session_cache:
            sess = self._harness.session(session_id=name)
            await sess.start()
            self._session_cache[name] = sess
        return self._session_cache[name]

    # Alias: keep consistent with Flue's API shape
    async def session(self, name: str = "default") -> Any:
        return await self.session_async(name)

    @property
    def fs(self) -> "_WorkflowFS":
        """Application-level filesystem access (not the agent's tool layer)."""
        sandbox = self._harness.spec.sandbox_factory()
        return _WorkflowFS(sandbox)

    async def shell(self, cmd: str, timeout: Optional[float] = None) -> str:
        """Run a shell command in the agent sandbox from application code."""
        sandbox = self._harness.spec.sandbox_factory()
        await sandbox.start()
        try:
            result = await sandbox.exec(cmd, timeout=timeout)
            return result.render()
        finally:
            await sandbox.stop()

    async def close(self) -> None:
        for sess in self._session_cache.values():
            await sess.close()


class _WorkflowFS:
    """Application-level filesystem proxy backed by a sandbox."""

    def __init__(self, sandbox: Any):
        self._sandbox = sandbox
        self._started = False

    async def _ensure(self) -> Any:
        if not self._started:
            await self._sandbox.start()
            self._started = True
        return self._sandbox.fs

    async def write_file(self, path: str, content: str) -> None:
        fs = await self._ensure()
        fs.write(path, content)

    async def read_file(self, path: str) -> str:
        fs = await self._ensure()
        return fs.read(path)

    async def exists(self, path: str) -> bool:
        fs = await self._ensure()
        return fs.exists(path)

    async def list_dir(self, path: str = ".") -> list[str]:
        fs = await self._ensure()
        return fs.listdir(path)


# ── @workflow decorator ──────────────────────────────────────────────────────

WorkflowFn = Callable[[WorkflowContext], Coroutine[Any, Any, Any]]


class Workflow:
    """A registered workflow definition. Produced by ``@workflow``."""

    def __init__(
        self, fn: WorkflowFn, *, name: Optional[str] = None, registry: Optional[RunRegistry] = None
    ):
        self.fn = fn
        self.name = name or fn.__name__
        self.registry: RunRegistry = registry or RunRegistry()

    async def run(
        self,
        payload: Any = None,
        *,
        run_id: Optional[str] = None,
        tracer: Optional["Tracer"] = None,
        checkpoint_backend: Optional[WorkflowCheckpoint] = None,
    ) -> WorkflowRun:
        """Invoke the workflow and return the completed WorkflowRun."""
        rid = run_id or f"run_{uuid.uuid4().hex[:16]}"
        wrun = WorkflowRun(
            run_id=rid,
            workflow_name=self.name,
            status=RunStatus.RUNNING,
            payload=payload,
        )
        wrun.add_event("run_start", workflow=self.name, run_id=rid)
        self.registry.save(wrun)

        # Pre-load checkpoint cache from backend if available
        checkpoint_cache: dict[str, Any] = {}
        if checkpoint_backend is not None:
            checkpoint_cache = await checkpoint_backend.load(rid)

        ctx = WorkflowContext(
            run_id=rid,
            payload=payload,
            _run=wrun,
            _registry=self.registry,
            _tracer=tracer,
            _checkpoint_backend=checkpoint_backend,
            _checkpoint_cache=checkpoint_cache,
        )

        try:
            output = await self.fn(ctx)
            wrun.output = output
            wrun.status = RunStatus.COMPLETED
            wrun.ended_at = time.time()
            wrun.add_event("run_end", status="completed", output=output)
        except Exception as exc:
            wrun.error = f"{type(exc).__name__}: {exc}"
            wrun.status = RunStatus.FAILED
            wrun.ended_at = time.time()
            wrun.add_event("run_end", status="failed", error=wrun.error)
        finally:
            self.registry.save(wrun)

        return wrun

    def logs(self, run_id: str) -> None:
        """Print a human-readable event log for a run (mirrors `tvastar logs`)."""
        run = self.registry.get(run_id)
        if not run:
            print(f"[tvastar] No run found: {run_id}")
            return
        print(f"Run: {run.run_id}  workflow={run.workflow_name}  status={run.status.value}")
        print(
            f"Started: {_fmt_time(run.started_at)}  "
            f"Ended: {_fmt_time(run.ended_at) if run.ended_at else '—'}"
        )
        print()
        for ev in run.events:
            ts = _fmt_time(ev.at)
            data_str = "  ".join(
                f"{k}={v!r}" for k, v in ev.data.items() if k not in ("run_id", "workflow")
            )
            print(f"  {ts}  [{ev.type}]  {data_str}")
        if run.error:
            print(f"\nError: {run.error}")

    def get_run(self, run_id: str) -> Optional[WorkflowRun]:
        return self.registry.get(run_id)

    def list_runs(self) -> list[WorkflowRun]:
        return self.registry.list_runs(workflow_name=self.name)


def workflow(
    fn: Optional[WorkflowFn] = None,
    *,
    name: Optional[str] = None,
    registry: Optional[RunRegistry] = None,
) -> Any:
    """Decorator that turns an async function into a Workflow.

    Usage::

        @workflow
        async def my_workflow(ctx: WorkflowContext) -> dict:
            harness = await ctx.init(agent)
            session = await harness.session()
            response = await session.prompt(ctx.payload["text"])
            return {"result": response.text}

        run = await my_workflow.run({"text": "Hello"})
        print(run.run_id, run.output)
    """

    def wrap(f: WorkflowFn) -> Workflow:
        return Workflow(f, name=name, registry=registry)

    if fn is not None:
        return wrap(fn)
    return wrap


# ── Checkpoint Protocol & Implementation ─────────────────────────────────────


@runtime_checkable
class WorkflowCheckpoint(Protocol):
    """Protocol for checkpoint persistence backends."""

    async def save(self, run_id: str, step_name: str, data: Any) -> None: ...
    async def load(self, run_id: str) -> dict[str, Any]: ...


class FileCheckpoint:
    """JSON-file-backed checkpoint implementation.

    Stores one JSON file per run_id in the configured directory.
    Each file maps step_name -> data.
    """

    def __init__(self, directory: str = ".tvastar-checkpoints/") -> None:
        self._directory = Path(directory)

    async def save(self, run_id: str, step_name: str, data: Any) -> None:
        """Persist step data into the run's JSON file.

        Creates the directory and file if they don't exist.
        Raises filesystem errors (e.g. permission denied, disk full).
        """
        self._directory.mkdir(parents=True, exist_ok=True)
        file_path = self._directory / f"{run_id}.json"

        # Load existing data if the file exists
        existing: dict[str, Any] = {}
        if file_path.exists():
            try:
                existing = json.loads(file_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                existing = {}

        existing[step_name] = data
        file_path.write_text(json.dumps(existing), encoding="utf-8")

    async def load(self, run_id: str) -> dict[str, Any]:
        """Load the checkpoint dict for a run_id.

        Returns {} on missing or corrupt file.
        """
        file_path = self._directory / f"{run_id}.json"
        if not file_path.exists():
            return {}
        try:
            content = file_path.read_text(encoding="utf-8")
            result = json.loads(content)
            if not isinstance(result, dict):
                return {}
            return result
        except (json.JSONDecodeError, ValueError, OSError):
            return {}


# ── CLI helper (tvastar logs <run_id>) ───────────────────────────────────────


def _fmt_time(ts: Optional[float]) -> str:
    if ts is None:
        return "—"
    import datetime

    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]


def cli_logs(run_id: str, registry_path: str = ".tvastar-runs") -> int:
    """Entry point for ``tvastar logs <run_id>``."""
    reg = RunRegistry.file_backed(registry_path)
    run = reg.get(run_id)
    if not run:
        print(f"[tvastar] Run not found: {run_id}")
        return 1
    # Reuse Workflow.logs output via a dummy Workflow
    dummy = Workflow(lambda ctx: None, name=run.workflow_name, registry=reg)  # type: ignore
    dummy.logs(run_id)
    return 0
