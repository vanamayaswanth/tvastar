"""
tvastar.graph — DAG-based parallel task execution.

Independent tasks run concurrently. A task starts as soon as all its
dependencies complete. Wall-clock time equals the critical path, not
the sum of all tasks.

Dependency results are automatically injected into downstream prompts
so each task has access to upstream data without extra wiring.

Usage::

    from tvastar import TaskGraph

    graph = TaskGraph(harness)
    graph.task("leads",   "Fetch the lead list from CRM")
    graph.task("pricing", "Scrape competitor pricing")
    graph.task("news",    "Find recent news about the prospect")
    graph.task("analyse", "Score and prioritise leads",
               depends_on=["leads", "pricing", "news"])
    graph.task("emails",  "Write personalised cold emails",
               depends_on=["analyse"])
    graph.task("report",  "Write executive summary",
               depends_on=["analyse"])

    results = await graph.run()
    # leads + pricing + news  → run in parallel
    # analyse                 → waits for all three, receives their results
    # emails + report         → run in parallel after analyse
    print(results["emails"].text)
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .detect import Finding
    from .harness import Harness
    from .session import RunResult

__all__ = ["TaskGraph", "GraphResult"]


@dataclass
class _TaskNode:
    name: str
    prompt: str
    depends_on: list[str] = field(default_factory=list)
    result: Any = None
    cancel_after: float | None = None


@dataclass
class GraphResult:
    """All results from a TaskGraph.run() call, keyed by task name."""

    results: dict[str, "RunResult"]
    findings: dict[str, list["Finding"]] = field(default_factory=dict)

    def __getitem__(self, name: str) -> "RunResult":
        return self.results[name]

    def __iter__(self):
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)

    @property
    def ok(self) -> bool:
        """True when every task finished cleanly with no warnings."""
        return all(r.ok for r in self.results.values()) and not any(
            v for v in self.findings.values()
        )

    @property
    def text(self) -> dict[str, str]:
        """Final text output from every task, keyed by name."""
        return {name: r.text for name, r in self.results.items()}

    @property
    def all_findings(self) -> list["Finding"]:
        """Flat list of all findings across every task."""
        out: list[Any] = []
        for fs in self.findings.values():
            out.extend(fs)
        return out


class TaskGraph:
    """
    DAG-based parallel task executor.

    Add tasks with :meth:`task`, then call :meth:`run`.  Tasks whose
    ``depends_on`` lists are empty start immediately.  A task starts as
    soon as every dependency has completed.  Dependency results are
    prepended to the downstream task's prompt so the model has full
    context without extra wiring.
    """

    def __init__(self, harness: "Harness") -> None:
        self._harness = harness
        self._nodes: dict[str, _TaskNode] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def task(
        self,
        name: str,
        prompt: str,
        *,
        depends_on: list[str] | None = None,
        result: Any = None,
        cancel_after: float | None = None,
    ) -> "TaskGraph":
        """
        Register a task node.

        Parameters
        ----------
        name:
            Unique identifier for this task (used in ``depends_on`` lists
            and as the key in :class:`GraphResult`).
        prompt:
            The text sent to the agent for this task.
        depends_on:
            Names of tasks that must complete before this one starts.
            Their results are injected into this task's prompt automatically.
        result:
            Optional Pydantic model, dataclass, or ``dict`` — parsed from
            the agent's output and stored in ``RunResult.data``.
        cancel_after:
            Optional timeout in seconds.  The task is cancelled if it
            exceeds this duration.

        Returns self for fluent chaining.
        """
        if name in self._nodes:
            raise ValueError(f"Duplicate task name: {name!r}")
        self._nodes[name] = _TaskNode(
            name=name,
            prompt=prompt,
            depends_on=depends_on or [],
            result=result,
            cancel_after=cancel_after,
        )
        return self

    async def run(
        self,
        *,
        inject_results: bool = True,
        concurrency: int = 8,
    ) -> GraphResult:
        """
        Execute the task graph and return a :class:`GraphResult`.

        Parameters
        ----------
        inject_results:
            When True (default), prepend each dependency's output to the
            downstream task's prompt so it has full context.
        concurrency:
            Maximum number of tasks running model calls simultaneously.
            Defaults to 8 to avoid thundering-herd retry storms when the
            model provider rate-limits.  Pass ``0`` for unlimited.
        """
        if not self._nodes:
            return GraphResult({})

        self._validate()

        # Resolve tracer — harness exposes it as .tracer (public) or ._tracer (fallback)
        _tracer = getattr(self._harness, "tracer", None) or getattr(
            self._harness, "_tracer", None
        )

        completed: dict[str, "RunResult"] = {}
        errors: dict[str, BaseException] = {}
        done_events: dict[str, asyncio.Event] = {n: asyncio.Event() for n in self._nodes}
        _sem: asyncio.Semaphore | None = asyncio.Semaphore(concurrency) if concurrency > 0 else None

        async def _run_one(name: str) -> None:
            node = self._nodes[name]

            # Wait for every dependency — including failed ones to avoid deadlock.
            for dep in node.depends_on:
                await done_events[dep].wait()

            # Propagate upstream failures without running this task.
            dep_errors = [dep for dep in node.depends_on if dep in errors]
            if dep_errors:
                errors[name] = None
                done_events[name].set()
                return

            # Use an anonymous session each run to avoid history contamination
            # if the graph is re-run.
            sess = self._harness.session()
            try:
                # Build prompt — inject dependency results when requested
                prompt = node.prompt
                if inject_results and node.depends_on:
                    parts = [f"[{dep} result]\n{completed[dep].text}" for dep in node.depends_on]
                    prompt = "\n\n".join(parts) + "\n\n---\n\n" + node.prompt

                kwargs: dict[str, Any] = {}
                if node.result is not None:
                    kwargs["result"] = node.result

                # Acquire the concurrency semaphore only around the model call,
                # not the dependency-wait above, so waiting tasks don't hold slots.
                async def _execute() -> "RunResult":
                    from contextlib import nullcontext

                    _task_ctx = (
                        _tracer.span("graph.task", task=name)
                        if _tracer is not None
                        else nullcontext()
                    )
                    with _task_ctx:
                        async with sess:
                            coro = sess.prompt(prompt, **kwargs)
                            if node.cancel_after is not None:
                                return await asyncio.wait_for(coro, timeout=node.cancel_after)
                            return await coro

                if _sem is not None:
                    async with _sem:
                        run_result = await _execute()
                else:
                    run_result = await _execute()

                completed[name] = run_result
            except BaseException as exc:
                errors[name] = exc
            finally:
                # Always signal completion and release the one-shot session.
                self._harness._release(sess.id)
                done_events[name].set()

        from contextlib import nullcontext

        _graph_ctx = (
            _tracer.span("graph.run")
            if _tracer is not None
            else nullcontext()
        )
        with _graph_ctx:
            await asyncio.gather(*[_run_one(n) for n in self._nodes])

        # Re-raise the first real task failure (not downstream propagation noise).
        real_errors = {k: v for k, v in errors.items() if v is not None}
        if real_errors:
            first_name, first_err = next(iter(real_errors.items()))
            raise RuntimeError(f"Task {first_name!r} failed") from first_err

        all_findings = {name: r.findings for name, r in completed.items() if r.findings}
        return GraphResult(completed, findings=all_findings)

    # ------------------------------------------------------------------
    # Validation (cycle detection + unknown dep check)
    # ------------------------------------------------------------------

    def _validate(self) -> None:
        # Unknown dependencies
        for node in self._nodes.values():
            for dep in node.depends_on:
                if dep not in self._nodes:
                    raise ValueError(f"Task {node.name!r} depends on unknown task {dep!r}")

        # Cycle detection via DFS colouring
        WHITE, GRAY, BLACK = 0, 1, 2
        colour: dict[str, int] = defaultdict(lambda: WHITE)

        def _dfs(name: str) -> None:
            colour[name] = GRAY
            for dep in self._nodes[name].depends_on:
                if colour[dep] == GRAY:
                    raise ValueError(f"Cycle detected: {name!r} → {dep!r} forms a loop")
                if colour[dep] == WHITE:
                    _dfs(dep)
            colour[name] = BLACK

        for name in self._nodes:
            if colour[name] == WHITE:
                _dfs(name)
