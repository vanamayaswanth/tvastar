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
import logging
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .cycle_journal import CycleEntry, CycleJournal
from .cycle_policy import CyclePolicy
from .termination_oracle import TerminationOracle

if TYPE_CHECKING:
    from .detect import Finding
    from .harness import Harness
    from .memory.store import Store
    from .session import RunResult

__all__ = ["TaskGraph", "GraphResult", "LoopNode"]


# Type alias for dependency specification: plain name or (name, policy) tuple
DependencySpec = str | tuple[str, Any]


@dataclass
class _TaskNode:
    name: str
    prompt: str
    depends_on: list[str] = field(default_factory=list)
    cycle_policies: dict[str, Any] = field(default_factory=dict)
    edge_conditions: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    cancel_after: float | None = None
    model: Any = None
    loop_node: "LoopNode | None" = None  # when set, custom execution via LoopNode


@dataclass
class GraphResult:
    """All results from a TaskGraph.run() call, keyed by task name."""

    results: dict[str, "RunResult"]
    findings: dict[str, list["Finding"]] = field(default_factory=dict)
    cycle_journals: dict[str, "CycleJournal"] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

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


from .loop_node import LoopNode  # noqa: E402 — SRP extraction


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
        self._oracle: TerminationOracle | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def attach_oracle(self, oracle: TerminationOracle) -> "TaskGraph":
        """
        Attach a TerminationOracle that monitors active cycles.

        The oracle polls CycleJournals at its configured interval and
        cancels stuck cycles. Oracle failure is fail-open (log + continue).

        Returns self for fluent chaining.
        """
        self._oracle = oracle
        return self

    def task(
        self,
        name: str,
        prompt: str,
        *,
        depends_on: "Sequence[DependencySpec] | None" = None,
        edge_conditions: dict[str, Any] | None = None,
        result: Any = None,
        cancel_after: float | None = None,
        model: Any = None,
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
            Each entry is either a plain string (task name) or a tuple
            of (task_name, CyclePolicy) for controlled back-edges.
            Their results are injected into this task's prompt automatically.
        edge_conditions:
            Optional dict mapping dependency name to a callable that
            receives the upstream RunResult and returns bool. True means
            inject normally; False means satisfy (unblock) but do NOT
            inject. If ALL deps return False, the task is skipped entirely.
        result:
            Optional Pydantic model, dataclass, or ``dict`` — parsed from
            the agent's output and stored in ``RunResult.data``.
        cancel_after:
            Optional timeout in seconds.  The task is cancelled if it
            exceeds this duration.
        model:
            Optional Model override for this specific node.  When set,
            the node's session uses this model instead of the harness
            default.  Must implement the Model interface (have a
            ``generate`` method).

        Returns self for fluent chaining.
        """
        if name in self._nodes:
            raise ValueError(f"Duplicate task name: {name!r}")

        # Normalize depends_on: accept list[str | tuple[str, CyclePolicy]]
        dep_names: list[str] = []
        cycle_policies: dict[str, Any] = {}
        for dep in depends_on or []:
            if isinstance(dep, str):
                dep_names.append(dep)
            elif isinstance(dep, tuple) and len(dep) == 2:
                dep_name, policy = dep
                dep_names.append(dep_name)
                cycle_policies[dep_name] = policy
            else:
                raise ValueError(f"depends_on entry must be str or (str, CyclePolicy), got {dep!r}")

        self._nodes[name] = _TaskNode(
            name=name,
            prompt=prompt,
            depends_on=dep_names,
            cycle_policies=cycle_policies,
            edge_conditions=edge_conditions or {},
            result=result,
            cancel_after=cancel_after,
            model=model,
        )
        return self

    def loop_task(
        self,
        name: str,
        loop_node: "LoopNode",
        *,
        depends_on: "Sequence[DependencySpec] | None" = None,
    ) -> "TaskGraph":
        """Register a LoopNode as a task in the graph.

        The LoopNode runs its internal loop iterations during execution,
        respecting max_iterations and quality_gate. Upstream results are
        injected as context. Participates in dependency injection like
        any other task.

        Parameters
        ----------
        name:
            Unique identifier for this task.
        loop_node:
            A LoopNode wrapping LoopConfig + AgentSpec.
        depends_on:
            Names of tasks that must complete before this one starts.

        Returns self for fluent chaining.
        """
        if name in self._nodes:
            raise ValueError(f"Duplicate task name: {name!r}")
        if not isinstance(loop_node, LoopNode):
            raise TypeError("loop_task requires a LoopNode instance")

        dep_names: list[str] = []
        cycle_policies: dict[str, Any] = {}
        for dep in depends_on or []:
            if isinstance(dep, str):
                dep_names.append(dep)
            elif isinstance(dep, tuple) and len(dep) == 2:
                dep_name, policy = dep
                dep_names.append(dep_name)
                cycle_policies[dep_name] = policy
            else:
                raise ValueError(f"depends_on entry must be str or (str, CyclePolicy), got {dep!r}")

        self._nodes[name] = _TaskNode(
            name=name,
            prompt=loop_node.config.goal,
            depends_on=dep_names,
            cycle_policies=cycle_policies,
            loop_node=loop_node,
        )
        return self

    async def run(
        self,
        *,
        inject_results: bool = True,
        concurrency: int = 8,
        resume: bool = False,
        graph_run_id: str | None = None,
        journal: "Store | None" = None,
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
        resume:
            When True, load previously completed results from the journal
            Store and skip execution of nodes that have stored results.
        graph_run_id:
            Caller-supplied identifier for this graph run. Used as a
            prefix in journal keys (format: ``{graph_run_id}:{node_name}``).
        journal:
            A Store instance used for persisting completed node results.
            Any Store implementation works (FileStore, InMemoryStore,
            SQLiteStore). Journaling failures never break a run.
        """
        if not self._nodes:
            return GraphResult({})

        self._validate()

        # Resolve tracer — harness exposes it as .tracer (public) or ._tracer (fallback)
        _tracer = getattr(self._harness, "tracer", None) or getattr(self._harness, "_tracer", None)

        completed: dict[str, "RunResult"] = {}
        errors: dict[str, BaseException] = {}
        skipped: list[str] = []
        done_events: dict[str, asyncio.Event] = {n: asyncio.Event() for n in self._nodes}
        _sem: asyncio.Semaphore | None = asyncio.Semaphore(concurrency) if concurrency > 0 else None
        # Store semaphore for cycle re-entry to access
        self._sem = _sem

        # Track active cycle tasks so oracle can cancel them
        self._cycle_tasks: dict[str, asyncio.Task] = {}
        self._cycle_start_times: dict[str, float] = {}

        # Mutable container so nested coroutines can disable journaling on failure
        _journal_ref: list["Store | None"] = [journal if graph_run_id else None]

        # Reference to active TaskGroup for cycle re-entry spawning
        _tg_ref: list[asyncio.TaskGroup | None] = [None]

        async def _run_one(name: str) -> None:
            node = self._nodes[name]

            # Wait for every dependency — except cycle back-edge deps (first iteration).
            # A node skips waiting on a dep if it declared a cycle_policy for that dep.
            for dep in node.depends_on:
                if dep in node.cycle_policies:
                    # This is the back-edge dep; don't wait on first iteration
                    continue
                await done_events[dep].wait()

            # Propagate upstream failures without running this task (skip cycle-back deps).
            non_cycle_deps = [dep for dep in node.depends_on if dep not in node.cycle_policies]
            dep_errors = [dep for dep in non_cycle_deps if dep in errors]
            if dep_errors:
                errors[name] = None
                done_events[name].set()
                return

            # --- Edge condition evaluation ---
            # After all deps complete, evaluate edge_conditions to decide injection.
            # If ALL deps have conditions and ALL return False → skip task entirely.
            if node.edge_conditions:
                inject_deps: list[str] = []  # deps whose results should be injected
                all_conditioned = True
                all_false = True
                for dep in node.depends_on:
                    if dep in node.cycle_policies:
                        # Cycle-back deps aren't evaluated here
                        continue
                    cond = node.edge_conditions.get(dep)
                    if cond is not None:
                        dep_result = completed.get(dep)
                        if dep_result is not None and cond(dep_result):
                            inject_deps.append(dep)
                            all_false = False
                        # else: condition False — dep satisfied but not injected
                    else:
                        # No condition on this dep — unconditional inject
                        all_conditioned = False
                        all_false = False
                        inject_deps.append(dep)

                if all_conditioned and all_false:
                    # ALL deps had conditions and ALL returned False → skip task
                    skipped.append(name)
                    done_events[name].set()
                    return
                # Store the filtered inject list for prompt building
                _edge_inject_deps = inject_deps
            else:
                _edge_inject_deps = None  # sentinel: use default behavior

            # --- Journal read: skip execution if valid cached result ---
            if resume and _journal_ref[0] is not None and graph_run_id:
                import logging

                stored = None
                try:
                    stored = _journal_ref[0].get(f"{graph_run_id}:{name}")
                except Exception:
                    logging.getLogger("tvastar.graph").warning(
                        "journal read failed for %s; continuing without journaling", name
                    )
                    _journal_ref[0] = None

                if isinstance(stored, str):
                    # Inject as if node completed
                    from .session import RunResult as _RunResult
                    from .types import Usage as _Usage

                    completed[name] = _RunResult(text=stored, messages=[], usage=_Usage(), steps=0)
                    done_events[name].set()
                    return  # skip execution
                # else: discard (None or non-string) and re-execute

            # --- LoopNode custom execution path ---
            if node.loop_node is not None:
                try:
                    # Build upstream context from completed deps
                    upstream_ctx: dict[str, str] | None = None
                    if inject_results and node.depends_on:
                        if _edge_inject_deps is not None:
                            available_deps = [d for d in _edge_inject_deps if d in completed]
                        else:
                            available_deps = [d for d in node.depends_on if d in completed]
                        if available_deps:
                            upstream_ctx = {d: completed[d].text for d in available_deps}

                    run_result = await node.loop_node.execute(context=upstream_ctx)
                    completed[name] = run_result
                except BaseException as exc:
                    errors[name] = exc
                finally:
                    done_events[name].set()
                return

            # Use an anonymous session each run to avoid history contamination
            # if the graph is re-run.
            sess = self._harness.session()
            if node.model is not None:
                import dataclasses

                sess.spec = dataclasses.replace(sess.spec, model=node.model)
            try:
                # Build prompt — inject dependency results when requested
                prompt = node.prompt
                if inject_results and node.depends_on:
                    # Determine which deps to inject from
                    if _edge_inject_deps is not None:
                        # edge_conditions filtered the list
                        available_deps = [dep for dep in _edge_inject_deps if dep in completed]
                    else:
                        # Default: inject all completed deps (skip cycle-back)
                        available_deps = [dep for dep in node.depends_on if dep in completed]
                    if available_deps:
                        parts = [f"[{dep} result]\n{completed[dep].text}" for dep in available_deps]
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

                # --- Cycle re-entry logic ---
                self._maybe_cycle_reenter(name, run_result, _tg_ref)

                # --- Journal write: persist result after success ---
                if _journal_ref[0] is not None and graph_run_id:
                    try:
                        _journal_ref[0].set(f"{graph_run_id}:{name}", run_result.text)
                    except Exception:
                        import logging

                        logging.getLogger("tvastar.graph").warning(
                            "journal write failed for %s; continuing without journaling",
                            name,
                        )
                        _journal_ref[0] = None  # disable for remainder

            except BaseException as exc:
                errors[name] = exc
            finally:
                # Always signal completion and release the one-shot session.
                self._harness._release(sess.id)
                done_events[name].set()

        from contextlib import nullcontext

        _graph_ctx = _tracer.span("graph.run") if _tracer is not None else nullcontext()
        with _graph_ctx:
            try:
                async with asyncio.TaskGroup() as tg:
                    _tg_ref[0] = tg
                    for n in self._nodes:
                        tg.create_task(_run_one(n))
                    # Start oracle poll loop if attached and cycles registered
                    if self._oracle is not None and self._cycle_edges:
                        _oracle_task = tg.create_task(self._run_oracle_poll(_tg_ref))
            except* Exception:
                # ponytail: _run_one catches all exceptions internally and stores
                # in `errors` dict. ExceptionGroup only surfaces if something truly
                # unexpected escapes (e.g. cycle re-entry). We handle below.
                pass

        # Re-raise the first real task failure (not downstream propagation noise).
        real_errors = {k: v for k, v in errors.items() if v is not None}
        if real_errors:
            first_name, first_err = next(iter(real_errors.items()))
            raise RuntimeError(f"Task {first_name!r} failed") from first_err

        all_findings = {name: r.findings for name, r in completed.items() if r.findings}
        return GraphResult(
            completed, findings=all_findings, cycle_journals=self._cycle_journals, skipped=skipped
        )

    # ------------------------------------------------------------------
    # Cycle re-entry
    # ------------------------------------------------------------------

    def _should_cycle_continue(
        self, policy: Any, journal: "CycleJournal", run_result: "RunResult"
    ) -> tuple[bool, str | None]:
        # ponytail: extracted for DRY
        """Determine if a cycle should re-enter or terminate."""
        from .cycle_policy import _AllowTTL, _AllowPredicate

        if journal.terminated:
            return False, None
        if isinstance(policy, _AllowTTL):
            if journal.iterations < policy.max_iterations:
                return True, None
            return False, "ttl_reached"
        elif isinstance(policy, _AllowPredicate):
            if policy.predicate(run_result):
                return True, None
            return False, "predicate_false"
        return False, "unknown"

    def _maybe_cycle_reenter(
        self,
        name: str,
        run_result: "RunResult",
        _tg_ref: list[Any],
    ) -> None:
        """Check if a completed node should re-enter via a registered cycle edge.

        If TTL not reached / predicate still True, spawn a new task in the
        active TaskGroup. Otherwise, record termination in the journal.
        """
        import time as _time

        for edge_key, policy in self._cycle_edges.items():
            # edge_key format: "{source}->{dep}" where source depends on dep
            src, dep = edge_key.split("->")
            # The cycling node is dep (the one we re-enter)
            if src != name:
                continue

            journal = self._cycle_journals[edge_key]

            # If oracle already terminated this cycle, skip
            if journal.terminated:
                continue

            # Record this iteration
            journal.append(
                CycleEntry(
                    iteration=journal.iterations,
                    timestamp=_time.time(),
                    result_text=run_result.text[:500],
                    continued=True,
                )
            )

            # Check termination conditions
            should_reenter, termination_reason = self._should_cycle_continue(
                policy, journal, run_result
            )

            if should_reenter:
                # Spawn re-entry within the active TaskGroup
                tg = _tg_ref[0]
                if tg is not None:
                    import time as _time2

                    t = tg.create_task(self._cycle_rerun(dep, run_result, edge_key, _tg_ref))
                    self._cycle_tasks[edge_key] = t
                    if edge_key not in self._cycle_start_times:
                        self._cycle_start_times[edge_key] = _time2.time()
            else:
                # Terminate: record in journal, last result flows downstream
                journal.append(
                    CycleEntry(
                        iteration=journal.iterations,
                        timestamp=_time.time(),
                        result_text=termination_reason or "terminated",
                        continued=False,
                    )
                )

    async def _cycle_rerun(
        self,
        name: str,
        upstream_result: "RunResult",
        edge_key: str,
        _tg_ref: list[Any],
    ) -> None:
        """Re-execute a cycling node within the existing TaskGroup."""
        import time as _time

        # Early exit if oracle already terminated this cycle
        journal = self._cycle_journals.get(edge_key)
        if journal and journal.terminated:
            return

        node = self._nodes[name]
        # Build prompt with previous iteration result injected
        prompt = f"[previous iteration result]\n{upstream_result.text}\n\n---\n\n{node.prompt}"

        sess = self._harness.session()
        if node.model is not None:
            import dataclasses

            sess.spec = dataclasses.replace(sess.spec, model=node.model)

        try:
            kwargs: dict[str, Any] = {}
            if node.result is not None:
                kwargs["result"] = node.result

            async def _execute_cycle() -> "RunResult":
                async with sess:
                    return await sess.prompt(prompt, **kwargs)

            if self._sem is not None:
                async with self._sem:
                    run_result = await _execute_cycle()
            else:
                run_result = await _execute_cycle()

            # Now check if we should re-enter again
            journal = self._cycle_journals[edge_key]
            policy = self._cycle_edges[edge_key]

            journal.append(
                CycleEntry(
                    iteration=journal.iterations,
                    timestamp=_time.time(),
                    result_text=run_result.text[:500],
                    continued=True,
                )
            )

            should_reenter, termination_reason = self._should_cycle_continue(
                policy, journal, run_result
            )

            if should_reenter:
                tg = _tg_ref[0]
                if tg is not None:
                    t = tg.create_task(self._cycle_rerun(name, run_result, edge_key, _tg_ref))
                    self._cycle_tasks[edge_key] = t
            elif not journal.terminated:
                journal.append(
                    CycleEntry(
                        iteration=journal.iterations,
                        timestamp=_time.time(),
                        result_text=termination_reason or "terminated",
                        continued=False,
                    )
                )
        except BaseException:
            # Record task_error termination in journal if possible
            import time as _time2

            journal = self._cycle_journals.get(edge_key)
            if journal and not journal.terminated:
                journal.append(
                    CycleEntry(
                        iteration=journal.iterations,
                        timestamp=_time2.time(),
                        result_text="task_error",
                        continued=False,
                    )
                )
                raise
            # ponytail: If journal is already terminated (oracle did it),
            # swallow the CancelledError — don't crash the TaskGroup.
        finally:
            self._harness._release(sess.id)

    # ------------------------------------------------------------------
    # Oracle polling
    # ------------------------------------------------------------------

    async def _run_oracle_poll(self, _tg_ref: list[Any]) -> None:
        """Run the attached oracle's poll loop, fail-open on any exception."""
        oracle = self._oracle
        if oracle is None:
            return
        try:
            await oracle._poll_loop(
                self._cycle_journals,
                self._cycle_tasks,
                self._cycle_start_times,
            )
        except asyncio.CancelledError:
            return  # Normal shutdown — graph completed
        except Exception as exc:
            # ponytail: fail-open — oracle crash never takes down the graph
            logging.getLogger("tvastar.termination_oracle").error(
                "Oracle poll loop failed (fail-open): %s", exc
            )

    # ------------------------------------------------------------------
    # Validation (cycle detection + unknown dep check)
    # ------------------------------------------------------------------

    def _validate(self) -> None:
        # Unknown dependencies
        for node in self._nodes.values():
            for dep in node.depends_on:
                if dep not in self._nodes:
                    raise ValueError(f"Task {node.name!r} depends on unknown task {dep!r}")

        # Reset cycle edges registry for this validation pass
        self._cycle_edges: dict[str, Any] = {}

        # Cycle detection via DFS colouring
        WHITE, GRAY, BLACK = 0, 1, 2
        colour: dict[str, int] = defaultdict(lambda: WHITE)

        def _dfs(name: str) -> None:
            colour[name] = GRAY
            node = self._nodes[name]
            for dep in node.depends_on:
                if colour[dep] == GRAY:
                    # Back-edge detected — check cycle policy on either side
                    policy = node.cycle_policies.get(dep)
                    if policy is None:
                        # Check if the dep node declared a policy for this node
                        policy = self._nodes[dep].cycle_policies.get(name)
                    if policy is None or policy == CyclePolicy.FORBID:
                        raise ValueError(f"Cycle detected: {name!r} → {dep!r} forms a loop")
                    # ALLOW_TTL or ALLOW_PREDICATE: register and skip raise
                    self._cycle_edges[f"{name}->{dep}"] = policy
                elif colour[dep] == WHITE:
                    _dfs(dep)
            colour[name] = BLACK

        for name in self._nodes:
            if colour[name] == WHITE:
                _dfs(name)

        # Create a CycleJournal for every registered cycle edge
        self._cycle_journals: dict[str, CycleJournal] = {
            key: CycleJournal() for key in self._cycle_edges
        }

        # Model interface validation
        for node in self._nodes.values():
            if node.model is not None and not hasattr(node.model, "generate"):
                raise TypeError(f"Task {node.name!r} model must implement the Model interface")
