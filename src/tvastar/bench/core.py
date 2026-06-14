"""BenchSuite — run standardised benchmark tasks through a Tvastar agent.

Design
------
A ``BenchTask`` is a self-contained unit of work: a prompt, an optional
workspace (files to write before the agent starts), and a ``verify`` callable
that receives the run result and the workspace path and returns True/False.

``BenchSuite`` fans tasks out concurrently (bounded by ``concurrency``),
writes each workspace into a temp dir, runs the agent in a ``LocalSandbox``
pointed at it, then calls ``verify`` on the *real* output — never the model's
claim. That "verify with real signals" property is the same principle as
``tvastar-fix``.

This is intentionally dependency-free (no HuggingFace, no Docker). Adapters
like ``swebench.py`` add optional-dep loading on top.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass
class BenchTask:
    """One benchmark task.

    Attributes:
        id: Unique identifier (e.g. ``"astropy__astropy-1234"``).
        prompt: The instruction given to the agent.
        workspace: Dict of relative-path → file-content to write before the run.
            E.g. ``{"src/foo.py": "...", "tests/test_foo.py": "..."}``.
        verify: ``(run_result, workspace_path) -> bool`` called after the run.
            Should use *real* signals (exit code, file diffs) — not the model's
            text. Defaults to ``run_result.ok`` (harness-level success check).
        metadata: Arbitrary data preserved in ``BenchResult`` (e.g. repo, split).
    """

    id: str
    prompt: str
    workspace: dict[str, str] = field(default_factory=dict)
    verify: Optional[Callable[[Any, Path], bool]] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class BenchResult:
    """Outcome of running a single ``BenchTask``."""

    task: BenchTask
    resolved: bool  # True = task verified as passing
    run_result: Optional[Any]  # RunResult or None if the run raised
    error: Optional[str]  # exception message if the run raised
    duration: float  # seconds

    @property
    def id(self) -> str:
        return self.task.id


@dataclass
class BenchReport:
    """Aggregate result from a full ``BenchSuite`` run."""

    results: list[BenchResult]
    duration: float
    suite_name: str = ""

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def resolved(self) -> int:
        return sum(1 for r in self.results if r.resolved)

    @property
    def failed_runs(self) -> int:
        return sum(1 for r in self.results if r.error is not None)

    @property
    def score(self) -> float:
        """Resolve rate (resolved / total). 0.0 if no tasks."""
        return self.resolved / self.total if self.total else 0.0

    def print(self) -> None:
        label = f"  {self.suite_name}" if self.suite_name else ""
        print(f"\n{'=' * 64}")
        print(f"Benchmark Report{label}")
        print(f"  Resolved : {self.resolved}/{self.total}  ({self.score:.1%})")
        print(f"  Duration : {self.duration:.1f}s")
        if self.failed_runs:
            print(f"  Errors   : {self.failed_runs} run(s) raised exceptions")
        print(f"{'=' * 64}")
        for r in self.results:
            status = "✓" if r.resolved else ("E" if r.error else "✗")
            print(f"  [{status}] {r.id}  ({r.duration:.1f}s)")
            if r.error:
                print(f"       → {r.error[:120]}")
        print(f"{'=' * 64}\n")

    def to_dict(self) -> dict:
        return {
            "suite": self.suite_name,
            "score": self.score,
            "resolved": self.resolved,
            "total": self.total,
            "duration": self.duration,
            "results": [
                {
                    "id": r.id,
                    "resolved": r.resolved,
                    "error": r.error,
                    "duration": r.duration,
                    "steps": getattr(r.run_result, "steps", None),
                    "metadata": r.task.metadata,
                }
                for r in self.results
            ],
        }


# ── BenchSuite ────────────────────────────────────────────────────────────────


class BenchSuite:
    """Run benchmark tasks through a Tvastar agent.

    Args:
        agent_or_harness: ``AgentSpec`` or ``Harness``.
        concurrency: Max tasks running simultaneously (default 2 — each task
            may itself spawn many tool calls, so keep this modest).
        workdir: Base directory for per-task temp workspaces. Defaults to the
            system temp dir.
        sandbox_timeout: Seconds before a per-task sandbox command times out.

    Example::

        suite = BenchSuite(agent, concurrency=4)
        suite.add(BenchTask(id="t1", prompt="Fix the bug", workspace={"a.py": "..."}))
        report = asyncio.run(suite.run())
    """

    def __init__(
        self,
        agent_or_harness: Any,
        *,
        concurrency: int = 2,
        workdir: Optional[str] = None,
        sandbox_timeout: float = 120.0,
    ) -> None:
        self._agent_or_harness = agent_or_harness
        self._concurrency = concurrency
        self._workdir = workdir
        self._sandbox_timeout = sandbox_timeout
        self._tasks: list[BenchTask] = []
        self.name: str = ""

    def add(self, task: BenchTask) -> "BenchSuite":
        self._tasks.append(task)
        return self

    def add_many(self, tasks: list[BenchTask]) -> "BenchSuite":
        self._tasks.extend(tasks)
        return self

    async def run(self) -> BenchReport:
        from tvastar.agent import AgentSpec, create_agent
        from tvastar.harness import Harness
        from tvastar.sandbox.base import SecurityPolicy
        from tvastar.sandbox.local import LocalSandbox
        from tvastar.tools import default_toolset

        if isinstance(self._agent_or_harness, AgentSpec):
            base_spec = self._agent_or_harness
        else:
            base_spec = self._agent_or_harness.spec

        sem = asyncio.Semaphore(self._concurrency)
        start = time.monotonic()

        async def run_task(task: BenchTask) -> BenchResult:
            async with sem:
                t0 = time.monotonic()
                with tempfile.TemporaryDirectory(dir=self._workdir) as tmpdir:
                    workspace = Path(tmpdir)
                    # Write the task workspace files
                    for rel_path, content in task.workspace.items():
                        dest = workspace / rel_path
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_text(content, encoding="utf-8")

                    # Each task gets its own LocalSandbox pointed at its workspace
                    policy = SecurityPolicy(network=False, timeout_seconds=self._sandbox_timeout)
                    sandbox = LocalSandbox(workspace, policy=policy)

                    # Build a task-scoped spec that uses this sandbox
                    task_spec = create_agent(
                        base_spec.name,
                        model=base_spec.model,
                        instructions=base_spec.instructions,
                        tools=default_toolset(),
                        sandbox=lambda sb=sandbox: sb,
                        max_steps=base_spec.max_steps,
                        max_tokens=base_spec.max_tokens,
                        temperature=base_spec.temperature,
                        thinking_level=base_spec.thinking_level,
                        detect=list(base_spec.detectors),
                    )

                    try:
                        run_result = await Harness(task_spec).run(task.prompt)
                    except Exception as exc:
                        elapsed = time.monotonic() - t0
                        return BenchResult(
                            task=task,
                            resolved=False,
                            run_result=None,
                            error=str(exc),
                            duration=elapsed,
                        )

                    # Verify — use the task's verifier or fall back to run.ok
                    if task.verify is not None:
                        try:
                            resolved = bool(task.verify(run_result, workspace))
                        except Exception as exc:
                            resolved = False
                            run_result = run_result  # preserve
                            elapsed = time.monotonic() - t0
                            return BenchResult(
                                task=task,
                                resolved=False,
                                run_result=run_result,
                                error=f"verify() raised: {exc}",
                                duration=elapsed,
                            )
                    else:
                        resolved = run_result.ok

                    elapsed = time.monotonic() - t0
                    return BenchResult(
                        task=task,
                        resolved=resolved,
                        run_result=run_result,
                        error=None,
                        duration=elapsed,
                    )

        results = await asyncio.gather(*[run_task(t) for t in self._tasks])
        total = time.monotonic() - start
        return BenchReport(
            results=list(results),
            duration=total,
            suite_name=self.name,
        )
